import json
import os
import requests
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from motor_ml import renovar_todos_os_tokens

ARQUIVO_SAIDA = "banco_de_dados.json"

def processar_item(item_id, token, vendas_por_item):
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        r_item = requests.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers, timeout=10)
        r_rep = requests.get(f"https://api.mercadolibre.com/items/{item_id}/reputation", headers=headers, timeout=10)
        
        if r_item.status_code != 200: return None
        item_data = r_item.json()
        rep_data = r_rep.json() if r_rep.status_code == 200 else {}

        # Métricas
        experiencia_score = rep_data.get("buying_experience_score", 100)
        reclamacoes = rep_data.get("claims", 0)
        devolucoes = rep_data.get("returns", 0)
        
        logistica = "FULL" if item_data.get("shipping", {}).get("logistic_type") == "fulfillment" else "PADRÃO"
        
        # Visitas Reais
        visitas_30d = 0
        try:
            r_vis = requests.get(f"https://api.mercadolibre.com/visits/items?ids={item_id}", headers=headers, timeout=5)
            if r_vis.status_code == 200: visitas_30d = r_vis.json().get(item_id, 0)
        except: pass

        return {
            "item_id": item_id,
            "info": {
                "titulo": item_data.get("title", "Sem Título"),
                "thumbnail": item_data.get("thumbnail", ""),
                "preco_atual": item_data.get("price", 0.0),
                "experiencia_compra": experiencia_score,
                "logistica": logistica,
                "pecas_no_full": item_data.get("available_quantity", 0),
                "vendas": vendas_por_item.get(item_id, {"7d": 0, "14d": 0, "30d": 0}),
                "visitas": {"7d": int(visitas_30d*0.25), "14d": int(visitas_30d*0.5), "30d": visitas_30d},
                "reclamacoes": reclamacoes,
                "devolucoes": devolucoes
            }
        }
    except: return None

def gerar_banco_full():
    print(f"[{datetime.now().strftime('%H:%M:%S')}] Iniciando atualização otimizada...")
    
    with open("tokens_das_contas.json", "r") as f:
        contas = json.load(f)

    banco_antigo = {}
    if os.path.exists(ARQUIVO_SAIDA):
        with open(ARQUIVO_SAIDA, "r", encoding="utf-8") as f:
            banco_antigo = json.load(f).get("contas", {})

    contas = renovar_todos_os_tokens(contas)
    dados_consolidados = {"ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M:%S"), "contas": {}}
    
    hoje = datetime.now(timezone.utc).replace(tzinfo=None)
    data_30d_atras = (hoje - timedelta(days=30)).isoformat() + "Z"

    for user_id, dados_conta in contas.items():
        nome_loja = dados_conta.get('nome_da_conta', f'ID {user_id}')
        token = dados_conta['access_token']
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        print(f"\n--- Processando loja: {nome_loja} ---")
        
        # 1. Puxar todos os pedidos com paginação completa
        print(f"[{nome_loja}] Buscando pedidos dos últimos 30 dias...")
        vendas_por_item = {}
        offset_pedidos = 0
        while True:
            url_orders = f"https://api.mercadolibre.com/orders/search?seller={user_id}&order.date_created.from={data_30d_atras}&offset={offset_pedidos}&limit=50&status=paid"
            resp = requests.get(url_orders, headers=headers, timeout=15)
            if resp.status_code != 200: 
                print(f"[{nome_loja}] Aviso ao buscar pedidos (Status: {resp.status_code})")
                break
            results = resp.json().get("results", [])
            if not results: break
            for pedido in results:
                data_pedido = datetime.strptime(pedido.get("date_created", "").split(".")[0], "%Y-%m-%dT%H:%M:%S")
                for item_info in pedido.get("order_items", []):
                    item_id = item_info.get("item", {}).get("id")
                    qtd = item_info.get("quantity", 1)
                    if item_id not in vendas_por_item: vendas_por_item[item_id] = {"7d": 0, "14d": 0, "30d": 0}
                    vendas_por_item[item_id]["30d"] += qtd
                    if data_pedido >= (hoje - timedelta(days=14)): vendas_por_item[item_id]["14d"] += qtd
                    if data_pedido >= (hoje - timedelta(days=7)): vendas_por_item[item_id]["7d"] += qtd
            if len(results) < 50: break
            offset_pedidos += 50
        print(f"[{nome_loja}] Pedidos mapeados com sucesso.")

        # 2. Puxar itens da loja
        print(f"[{nome_loja}] Buscando lista de anúncios...")
        item_ids = []
        offset_items = 0
        while True:
            url_items = f"https://api.mercadolibre.com/users/{user_id}/items/search?offset={offset_items}&limit=50"
            resp = requests.get(url_items, headers=headers)
            if resp.status_code != 200: break
            ids = resp.json().get("results", [])
            if not ids: break
            item_ids.extend(ids)
            if len(ids) < 50: break
            offset_items += 50
        print(f"[{nome_loja}] Total de anúncios encontrados: {len(item_ids)}. Iniciando varredura paralela...")

        # 3. Processamento Paralelo
        produtos_loja = {}
        concluidos = 0
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = {executor.submit(processar_item, iid, token, vendas_por_item): iid for iid in item_ids}
            for future in as_completed(futures):
                res = future.result()
                if res: 
                    produtos_loja[res["item_id"]] = res["info"]
                concluidos += 1
                if concluidos % 10 == 0 or concluidos == len(item_ids):
                    print(f"[{nome_loja}] Progresso: {concluidos}/{len(item_ids)} anúncios processados...", end="\r")

        print(f"\n[{nome_loja}] Loja finalizada com sucesso!")
        dados_consolidados["contas"][nome_loja] = {"produtos": produtos_loja}

    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        json.dump(dados_consolidados, f, indent=4, ensure_ascii=False)
    print("\n[SUCESSO] Atualização finalizada! Arquivo JSON gerado.")

if __name__ == "__main__":
    gerar_banco_full()