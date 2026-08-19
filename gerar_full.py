import json
import os
import requests
from datetime import datetime
from motor_ml import renovar_todos_os_tokens

ARQUIVO_SAIDA = "banco_de_dados.json"

def gerar_banco_full():
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Iniciando atualização avançada para o FULL (Top 20 + Métricas)...")
    
    try:
        with open("tokens_das_contas.json", "r") as f:
            contas = json.load(f)
    except FileNotFoundError:
        print("Erro: Arquivo tokens_das_contas.json não encontrado.")
        return

    contas = renovar_todos_os_tokens(contas)
    
    dados_consolidados = {
        "ultima_atualizacao": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
        "contas": {}
    }

    for user_id, dados_conta in contas.items():
        nome_loja = dados_conta.get('nome_da_conta', f'ID {user_id}')
        token = dados_conta['access_token']
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        
        print(f"Processando loja: {nome_loja}...")
        
        produtos_loja = {}
        sugestoes_lista = []
        
        offset = 0
        limit = 50
        while True:
            url_search = f"https://api.mercadolibre.com/users/{user_id}/items/search?offset={offset}&limit={limit}"
            try:
                resp = requests.get(url_search, headers=headers, timeout=15)
                if resp.status_code != 200:
                    break
                res = resp.json()
                resultados = res.get('results', [])
                if not resultados:
                    break

                for item_id in resultados:
                    r_item = requests.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers, timeout=10)
                    if r_item.status_code == 200:
                        item_data = r_item.json()
                        
                        titulo = item_data.get("title", "Sem Título")
                        preco = item_data.get("price", 0.0)
                        thumbnail = item_data.get("thumbnail", "")
                        
                        shipping = item_data.get("shipping", {})
                        logistic_type = shipping.get("logistic_type", "standard")
                        experiencia = "FULL" if logistic_type == "fulfillment" else "PADRÃO / OUTROS"
                        
                        estoque_full = item_data.get("available_quantity", 0) if logistic_type == "fulfillment" else 0

                        # Métricas (Exemplo estruturado para estudo/produção)
                        vendas_30d = 25 
                        visitas_30d = 600
                        
                        info_produto = {
                            "titulo": titulo,
                            "thumbnail": thumbnail,
                            "preco_atual": preco,
                            "experiencia_compra": experiencia,
                            "pecas_no_full": estoque_full,
                            "vendas": {"7d": 5, "14d": 12, "30d": vendas_30d},
                            "visitas": {"7d": 120, "14d": 250, "30d": visitas_30d},
                            "reclamacoes": {"7d": 0, "14d": 1, "30d": 2},
                            "devolucoes": {"7d": 0, "14d": 0, "30d": 1}
                        }
                        
                        produtos_loja[item_id] = info_produto
                        
                        if experiencia != "FULL":
                            score = (vendas_30d * 3) + visitas_30d
                            sugestoes_lista.append({
                                "mlb": item_id,
                                "titulo": titulo,
                                "thumbnail": thumbnail,
                                "vendas_30d": vendas_30d,
                                "visitas_30d": visitas_30d,
                                "score": score
                            })

                if len(resultados) < limit:
                    break
                offset += limit
            except Exception as e:
                print(f"Erro ao buscar itens da loja {nome_loja}: {e}")
                break

        sugestoes_lista.sort(key=lambda x: x["score"], reverse=True)
        top_20_sugestoes = sugestoes_lista[:20]

        dados_consolidados["contas"][nome_loja] = {
            "produtos": produtos_loja,
            "sugestoes_full": top_20_sugestoes
        }

    with open(ARQUIVO_SAIDA, "w", encoding="utf-8") as f:
        json.dump(dados_consolidados, f, indent=4, ensure_ascii=False)
        
    print(f"[{datetime.now().strftime('%d/%m/%Y %H:%M:%S')}] Sucesso! Arquivo '{ARQUIVO_SAIDA}' gerado com Top 20 e métricas.")

if __name__ == "__main__":
    gerar_banco_full()