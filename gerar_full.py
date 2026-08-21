import json
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from motor_ml import renovar_todos_os_tokens

ARQUIVO_SAIDA = "banco_de_dados.json"

def processar_item(item_id, token, vendas_por_item):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    hoje = datetime.now(timezone.utc)
    
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

        reclamacoes_7d = 0
        reclamacoes_14d = 0
        reclamacoes_30d = 0
        
        try:
            url_claims = f"https://api.mercadolibre.com/post-sale/claims/search?item_id={item_id}"
            r_claims = requests.get(url_claims, headers=headers, timeout=5)
            if r_claims.status_code == 200:
                claims_data = r_claims.json().get("data", [])
                for claim in claims_data:
                    data_str = claim.get("date_created", "").split(".")[0]
                    try:
                        data_claim = datetime.strptime(data_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                        if data_claim >= (hoje - timedelta(days=30)): reclamacoes_30d += 1
                        if data_claim >= (hoje - timedelta(days=14)): reclamacoes_14d += 1
                        if data_claim >= (hoje - timedelta(days=7)): reclamacoes_7d += 1
                    except: pass
        except: pass

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
        
        vendas_item = vendas_por_item.get(str(item_id), {
            "7d": 0, "14d": 0, "30d": 0,
            "dev_7d": 0, "dev_14d": 0, "dev_30d": 0,
            "fat_7d": 0.0, "fat_14d": 0.0, "fat_30d": 0.0,
            "vendas_diarias": {}
        })

        info_produto = {
            "titulo": titulo,
            "thumbnail": thumbnail,
            "preco_atual": preco_final,
            "experiencia_compra": "N/A",
            "experiencia_compra_texto": experiencia_texto,
            "pecas_no_full": estoque_full,
            "vendas": {"7d": vendas_item["7d"], "14d": vendas_item["14d"], "30d": vendas_item["30d"]},
            "vendas_diarias": vendas_item["vendas_diarias"],
            "faturamento": {
                "7d": round(vendas_item["fat_7d"], 2), 
                "14d": round(vendas_item["fat_14d"], 2), 
                "30d": round(vendas_item["fat_30d"], 2)
            },
            "visitas": {"7d": int(visitas_30d * 0.25), "14d": int(visitas_30d * 0.5), "30d": visitas_30d},
            "reclamacoes": {"7d": reclamacoes_7d, "14d": reclamacoes_14d, "30d": reclamacoes_30d},
            "devolucoes": {"7d": vendas_item["dev_7d"], "14d": vendas_item["dev_14d"], "30d": vendas_item["dev_30d"]}
        }
        
        sugestao_obj = None
        if experiencia_texto != "FULL":
            score = (vendas_item["30d"] * 3) + visitas_30d
            sugestao_obj = {
                "mlb": item_id, 
                "titulo": titulo, 
                "thumbnail": thumbnail, 
                "vendas_30d": vendas_item["30d"], 
                "faturamento_30d": round(vendas_item["fat_30d"], 2),
                "visitas_30d": visitas_30d, 
                "score": score
            }

        return {"item_id": item_id, "info": info_produto, "sugestao": sugestao_obj}
    except Exception as e: 
        return None

def gerar_banco_full():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Extraindo pedidos com mapeamento de vendas diárias...")
    
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
    
    data_30d_str = limite_30d.strftime("%Y-%m-%dT00:00:00.000-00:00")

    for user_id, dados_conta in contas.items():
        nome_loja = dados_conta.get('nome_da_conta', f'ID {user_id}')
        token = dados_conta.get('access_token', '')
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        print(f"\n--- Processando loja: {nome_loja} ---")
        vendas_por_item = {}
        offset_pedidos = 0
        
        while True:
            params = {
                "seller": user_id,
                "offset": offset_pedidos,
                "limit": 50,
                "order.date_created.from": data_30d_str,
                "sort": "date_desc"
            }
            
            resp = requests.get("https://api.mercadolibre.com/orders/search", headers=headers, params=params, timeout=15)
            if resp.status_code != 200: break
                
            data_resp = resp.json()
            results = data_resp.get("results", [])
            if not results: break
            
            for pedido in results:
                status_p = pedido.get("status", "")
                tags = pedido.get("tags", [])
                
                is_cancelled = (status_p == "cancelled")
                is_returned = ("returned" in tags)
                
                try:
                    data_str = pedido.get("date_created", "").split(".")[0]
                    data_pedido = datetime.strptime(data_str, "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
                except: 
                    data_pedido = datetime.min.replace(tzinfo=timezone.utc)

                if data_pedido >= limite_30d:
                    dia_formatado = data_pedido.strftime("%Y-%m-%d")
                    
                    for item_info in pedido.get("order_items", []):
                        item_id = str(item_info.get("item", {}).get("id", ""))
                        qtd = int(item_info.get("quantity", 1))
                        unit_price = float(item_info.get("unit_price", 0.0))
                        
                        if not item_id: continue
                        
                        if item_id not in vendas_por_item: 
                            vendas_por_item[item_id] = {
                                "7d": 0, "14d": 0, "30d": 0,
                                "dev_7d": 0, "dev_14d": 0, "dev_30d": 0,
                                "fat_7d": 0.0, "fat_14d": 0.0, "fat_30d": 0.0,
                                "vendas_diarias": {}
                            }
                        
                        if not is_cancelled:
                            # Adicionando na contagem acumulada
                            vendas_por_item[item_id]["30d"] += qtd
                            vendas_por_item[item_id]["fat_30d"] += (qtd * unit_price)
                            
                            # Adicionando na contagem diária (Para o gráfico)
                            if dia_formatado not in vendas_por_item[item_id]["vendas_diarias"]:
                                vendas_por_item[item_id]["vendas_diarias"][dia_formatado] = 0
                            vendas_por_item[item_id]["vendas_diarias"][dia_formatado] += qtd
                            
                            if data_pedido >= limite_14d: 
                                vendas_por_item[item_id]["14d"] += qtd
                                vendas_por_item[item_id]["fat_14d"] += (qtd * unit_price)
                            if data_pedido >= limite_7d: 
                                vendas_por_item[item_id]["7d"] += qtd
                                vendas_por_item[item_id]["fat_7d"] += (qtd * unit_price)
                            
                        if is_returned:
                            vendas_por_item[item_id]["dev_30d"] += qtd
                            if data_pedido >= limite_14d: vendas_por_item[item_id]["dev_14d"] += qtd
                            if data_pedido >= limite_7d: vendas_por_item[item_id]["dev_7d"] += qtd
            
            if len(results) < 50: break
            offset_pedidos += 50
            
        print(f"[{nome_loja}] Produtos com vendas diárias mapeadas: {len(vendas_por_item)}")

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

        sugestoes_lista.sort(key=lambda x: (x["vendas_30d"], x["visitas_30d"]), reverse=True)
        dados_consolidados["contas"][nome_loja] = {"produtos": produtos_loja, "sugestoes_full": sugestoes_lista[:20]}

    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        json.dump(dados_consolidados, f, indent=4, ensure_ascii=False)
    print("\n\n[SUCESSO] Banco de dados finalizado e salvo com sucesso!")

if __name__ == "__main__":
    gerar_banco_full()