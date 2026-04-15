import json
import re

def sanitizar_para_sheets(valor, max_chars: int = 300) -> str:
    """Remove/escape caracteres problemáticos para o Google Sheets."""
    if not valor:
        return str(valor) if valor is not None else ""
    
    valor = str(valor)
    
    valor = valor[:max_chars]
    
    valor = valor.replace('%22', '"').replace('%20', ' ').replace('%2F', '/')
    
    if 'http://' in valor or 'https://' in valor:
        valor = re.sub(r'https?://\S+', '[URL]', valor)
    
    valor = valor.replace('\n', ' | ').replace('\r', '').replace('\t', ' ')
    
    if valor.startswith('ERRO:'):
        if '[URL]' in valor:
            valor = valor.replace('[URL]', 'API')
        if len(valor) > max_chars:
            valor = valor[:max_chars-3] + '...'
    
    return valor.strip()


def validar_e_limpar_placa(valor_placa):
    if not isinstance(valor_placa, str):
        return None

    placa_limpa = valor_placa.strip().replace('-', '').upper()

    if len(placa_limpa) == 7 and placa_limpa.isalnum():
        return placa_limpa
    
    return None

def carregar_config(config_path='utils/config.json'):
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
        return config
    except FileNotFoundError:
        return None
    except json.JSONDecodeError:
        return None