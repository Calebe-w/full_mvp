import json
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from motor_ml import renovar_todos_os_tokens

ARQUIVO_SAIDA = "banco_de_dados.json"

def processar_item(item_id, token, vendas_por_item):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r_item = requests.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers, timeout=10)
        if r_item.status_code != 200: 
            return None
            
        item_data = r_item.json()
        if item_data.get("status") != "active": 
            return None

        preco_final = item_data.get("price", 0.0)
        try:
            r_price = requests.get(f"https://api.mercadolibre.com/items/{item_id}/prices", headers=headers, timeout=5)
            if r_price.status_code == 200:
                for p in r_price.json().get("prices", []):
                    if p.get("type") in ["standard", "promotion"]:
                        preco_final = p.get("amount", preco_final)
                        if p.get("type") == "promotion": break
        except: pass

        r_rep = requests.get(f"https://api.mercadolibre.com/items/{item_id}/reputation", headers=headers, timeout=10)
        rep_data = r_rep.json() if r_rep.status_code == 200 else {}

        experiencia_score = rep_data.get("buying_experience_score", 100)
        claims_data = rep_data.get("claims", {})
        reclamacoes_total = claims_data.get("value", 0) if isinstance(claims_data, dict) else (claims_data if isinstance(claims_data, (int, float)) else 0)
        
        returns_data = rep_data.get("returns", {})
        devolucoes_total = returns_data.get("value", 0) if isinstance(returns_data, dict) else (returns_data if isinstance(returns_data, (int, float)) else 0)

        shipping = item_data.get("shipping", {})
        logistic_type = shipping.get("logistic_type", "standard")
        experiencia_texto = "FULL" if logistic_type == "fulfillment" else "PADRÃO / OUTROS"
        
        estoque_full = 0
        if logistic_type == "fulfillment":
            stock_resp = requests.get(f"https://api.mercadolibre.com/items/{item_id}/stock", headers=headers, timeout=10)
            if stock_resp.status_code == 200:
                for loc in stock_resp.json().get("location_types", []):
                    if loc.get("type") == "fulfillment": 
                        estoque_full = loc.get("quantity", 0)
            if estoque_full == 0: 
                estoque_full = item_data.get("available_quantity", 0)

        visitas_30d = 0
        try:
            r_vis = requests.get(f"https://api.mercadolibre.com/visits/items?ids={item_id}", headers=headers, timeout=5)
            if r_vis.status_code == 200: 
                visitas_30d = r_vis.json().get(item_id, 0)
        except: pass

        titulo = item_data.get("title", "Sem Título")
        raw_thumb = item_data.get("thumbnail", "")
        thumbnail = raw_thumb.replace("http://", "https://") if raw_thumb else ""
        
        vendas_item = vendas_por_item.get(str(item_id), {"7d": 0, "14d": 0, "30d": 0})

        info_produto = {
            "titulo": titulo,
            "thumbnail": thumbnail,
            "preco_atual": preco_final,
            "experiencia_compra": experiencia_score,
            "experiencia_compra_texto": experiencia_texto,
            "pecas_no_full": estoque_full,
            "vendas": {"7d": vendas_item["7d"], "14d": vendas_item["14d"], "30d": vendas_item["30d"]},
            "visitas": {"7d": int(visitas_30d * 0.25), "14d": int(visitas_30d * 0.5), "30d": visitas_30d},
            "reclamacoes": {"7d": int(reclamacoes_total * 0.25), "14d": int(reclamacoes_total * 0.5), "30d": int(reclamacoes_total)},
            "devolucoes": {"7d": int(devolucoes_total * 0.25), "14d": int(devolucoes_total * 0.5), "30d": int(devolucoes_total)}
        }
        
        sugestao_obj = None
        if experiencia_texto != "FULL":
            score = (vendas_item["30d"] * 3) + visitas_30d
            sugestao_obj = {"mlb": item_id, "titulo": titulo, "thumbnail": thumbnail, "vendas_30d": vendas_item["30d"], "visitas_30d": visitas_30d, "score": score}

        return {"item_id": item_id, "info": info_produto, "sugestao": sugestao_obj}
    except Exception as e: 
        return None

def gerar_banco_full():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando extração limpa de pedidos (com URL encode)...")
    
    try:
        with open("tokens_das_contas.json", "r") as f: 
            contas = json.load(f)
    except Exception as e:
        print("Erro ao abrir tokens_das_contas.json.")
        return

    contas = renovar_todos_os_tokens(contas)
    dados_consolidados = {"ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "contas": {}}
    hoje = datetime.now(timezone.utc)
    
    limite_30d = hoje - timedelta(days=30)
    limite_14d = hoje - timedelta(days=14)
    limite_7d = hoje - timedelta(days=7)
    
    # Formato de data rigoroso que a API do ML aceita
    data_30d_str = limite_30d.strftime("%Y-%m-%dT00:00:00.000-00:00")

    for user_id, dados_conta in contas.items():
        nome_loja = dados_conta.get('nome_da_conta', f'ID {user_id}')
        token = dados_conta.get('access_token', '')
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        print(f"\n--- Processando loja: {nome_loja} ---")
        vendas_por_item = {}
        offset_pedidos = 0
        
        # 1. VARREDURA DE PEDIDOS GARANTIDA (PAGINANDO APENAS OS ÚLTIMOS 30 DIAS)
        while True:
            # Passando os parâmetros via dict pro requests codificar a URL sem quebrar a data
            params = {
                "seller": user_id,
                "offset": offset_pedidos,
                "limit": 50,
                "order.date_created.from": data_30d_str,
                "sort": "date_desc" # Garante do mais novo pro mais velho
            }
            
            resp = requests.get("https://api.mercadolibre.com/orders/search", headers=headers, params=params, timeout=15)
            
            if resp.status_code != 200: 
                print(f"[{nome_loja}] Erro na API: {resp.status_code} - {resp.text}")
                break
                
            data_resp = resp.json()
            results = data_resp.get("results", [])
            
            if offset_pedidos == 0:
                total_recentes = data_resp.get("paging", {}).get("total", 0)
                print(f"[{nome_loja}] Raio-X: Encontrados {total_recentes} pedidos estritos dos últimos 30 dias.")
            
            if not results: 
                break
            
            for pedido in results:
                if pedido.get("status") == "cancelled": 
                    continue
                
                try:
                    data_str = pedido.get("date_created", "").split(".")[0]
                    data_pedido = datetime.strptime(data_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                except: 
                    data_pedido = datetime.min.replace(tzinfo=timezone.utc)

                if data_pedido >= limite_30d:
                    for item_info in pedido.get("order_items", []):
                        item_id = str(item_info.get("item", {}).get("id", ""))
                        qtd = int(item_info.get("quantity", 1))
                        if not item_id: continue
                        
                        if item_id not in vendas_por_item: 
                            vendas_por_item[item_id] = {"7d": 0, "14d": 0, "30d": 0}
                        
                        vendas_por_item[item_id]["30d"] += qtd
                        if data_pedido >= limite_14d: vendas_por_item[item_id]["14d"] += qtd
                        if data_pedido >= limite_7d: vendas_por_item[item_id]["7d"] += qtd
            
            if len(results) < 50: 
                break
            
            offset_pedidos += 50
            
        print(f"[{nome_loja}] Produtos diferentes com vendas (últimos 30d): {len(vendas_por_item)}")

        # 2. BUSCA ANÚNCIOS ATIVOS
        item_ids = []
        offset_items = 0
        while True:
            url_items = f"https://api.mercadolibre.com/users/{user_id}/items/search?offset={offset_items}&limit=50"
            resp = requests.get(url_items, headers=headers)
            if resp.status_code != 200: break
            ids = resp.json().get("results", [])
            if not ids: break
            item_ids.extend([str(i) for i in ids])
            if len(ids) < 50: break
            offset_items += 50

        # 3. PROCESSAMENTO MULTITHREAD
        produtos_loja = {}
        sugestoes_lista = []
        concluidos = 0
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(processar_item, iid, token, vendas_por_item): iid for iid in item_ids}
            for future in as_completed(futures):
                res = future.result()
                if res: 
                    produtos_loja[res["item_id"]] = res["info"]
                    if res["sugestao"]: sugestoes_lista.append(res["sugestao"])
                concluidos += 1
                if concluidos % 10 == 0 or concluidos == len(item_ids):
                    print(f"[{nome_loja}] Progresso anúncios: {concluidos}/{len(item_ids)}...", end="\r")

        sugestoes_lista.sort(key=lambda x: x["score"], reverse=True)
        dados_consolidados["contas"][nome_loja] = {"produtos": produtos_loja, "sugestoes_full": sugestoes_lista[:20]}

    # 4. SALVAR BANCO DE DADOS
    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        json.dump(dados_consolidados, f, indent=4, ensure_ascii=False)
    print("\n\n[SUCESSO] Banco de dados finalizado e salvo com sucesso!")

if __name__ == "__main__":
    gerar_banco_full()