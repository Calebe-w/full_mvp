import requests
import json
import re

APP_ID = "7966222230508822"
SECRET_KEY = "gNzaGQConytGMnB8WyStgl86myNgvpNB"

def renovar_todos_os_tokens(contas):
    """Bate na API do ML e atualiza os tokens vencidos"""
    print("\n Renovando acessos de todas as contas antes de iniciar...")
    url_token = "https://api.mercadolibre.com/oauth/token"
    teve_atualizacao = False
    
    for user_id, dados_conta in contas.items():
        refresh_antigo = dados_conta.get("refresh_token")
        
        payload = {
            "grant_type": "refresh_token",
            "client_id": APP_ID,
            "client_secret": SECRET_KEY,
            "refresh_token": refresh_antigo
        }
        
        resposta = requests.post(url_token, data=payload).json()
        
        if "access_token" in resposta:
            contas[user_id]["access_token"] = resposta["access_token"]
            contas[user_id]["refresh_token"] = resposta["refresh_token"]
            teve_atualizacao = True
            print(f"   {dados_conta.get('nome_da_conta', user_id)}: Token renovado!")
        else:
            print(f"   {dados_conta.get('nome_da_conta', user_id)}: Falha ao renovar.")
            
    if teve_atualizacao:
        with open("tokens_das_contas.json", "w", encoding="utf-8") as f:
            json.dump(contas, f, indent=4)
            
    return contas


def verificar_e_montar_novo_sku(sku_atual, prefixo_alvo, prefixo_novo):
    """Peneira o SKU via Regex e remonta com o sufixo antigo se der match"""
    if not sku_atual:
        return None

    partes = str(sku_atual).split(' ', 1)
    prefixo_atual = partes[0]
    sufixo = partes[1] if len(partes) > 1 else ""

    letras_alvo = re.sub(r'[^a-zA-Z]', '', prefixo_alvo).lower()
    numeros_alvo = re.sub(r'[^\d]', '', prefixo_alvo)

    letras_atual = re.sub(r'[^a-zA-Z]', '', prefixo_atual).lower()
    numeros_atual = re.sub(r'[^\d]', '', prefixo_atual)

    if numeros_atual == numeros_alvo and letras_atual == letras_alvo:
        if sufixo:
            return f"{prefixo_novo} {sufixo}"
        else:
            return prefixo_novo
            
    return None