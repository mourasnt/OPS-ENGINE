import os
import sys
import gspread
import json
from google.oauth2.service_account import Credentials
from loguru import logger

logger.remove()
logger.add(sink=sys.stdout, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")

def autenticar_client(creds_path):
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def carregar_config():
    with open('utils/config.json', 'r', encoding='utf-8') as f:
        return json.load(f)

def load_env():
    env_path = '.env'
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ.setdefault(key.strip(), value.strip())

load_env()

def col_index_para_letra(n):
    resultado = ""
    while n > 0:
        n -= 1
        resultado = chr(65 + (n % 26)) + resultado
        n //= 26
    return resultado

def letra_para_col_index(s):
    resultado = 0
    for c in s.upper():
        resultado = resultado * 26 + (ord(c) - ord('A') + 1)
    return resultado

def validar_mapeamento(ws, header_row=3):
    logger.info("=" * 70)
    logger.info("VALIDAÇÃO DE MAPEAMENTO DE COLUNAS")
    logger.info("=" * 70)

    headers = ws.row_values(header_row)
    header_map = {h: i + 1 for i, h in enumerate(headers) if h}

    logger.info(f"\nCabeçalho encontrado na linha {header_row}")
    logger.info("-" * 70)

    colunas_ordenadas = sorted(header_map.items(), key=lambda x: x[1])

    logger.info(f"{'Índice':<10} {'Letra':<10} {'Nome da Coluna':<40}")
    logger.info("-" * 70)

    colunas_relevantes = [
        'PRÉ SM', 'COD SM', 'SM EFET.', 'Status de emissão', 'CTE',
        'MDFe', '$ Transportado', 'Status', 'N° Carga'
    ]

    for nome, idx in colunas_ordenadas:
        letra = col_index_para_letra(idx)
        marker = " <<<" if nome in colunas_relevantes else ""
        logger.info(f"{idx:<10} {letra:<10} {nome:<40}{marker}")

    logger.info("-" * 70)

    logger.info("\n>>> COLUNAS RELEVANTES:")
    for nome in colunas_relevantes:
        if nome in header_map:
            idx = header_map[nome]
            letra = col_index_para_letra(idx)
            logger.info(f"  {nome}: Índice {idx} (Coluna {letra})")
        else:
            logger.warning(f"  {nome}: NÃO ENCONTRADA")

    return header_map

def testar_celulas_individuais(ws, celulas_teste):
    logger.info("\n" + "=" * 70)
    logger.info("TESTE DE CÉLULAS INDIVIDUAIS")
    logger.info("=" * 70)

    resultados = []

    for celula in celulas_teste:
        row = celula['row']
        col = celula['col']
        valor = celula['valor']
        desc = celula.get('desc', f"R{row}C{col}")

        letra = col_index_para_letra(col)

        logger.info(f"\n[Teste] {desc}")
        logger.info(f"  Célula: R{row}C{col} (Coluna {letra})")
        logger.info(f"  Valor:  {valor[:50]}{'...' if len(str(valor)) > 50 else ''}")

        try:
            gspread_cell = gspread.Cell(row, col, str(valor))
            resp = ws.update_cells([gspread_cell], value_input_option='USER_ENTERED')
            logger.info(f"  Resultado: SUCESSO")
            resultados.append({**celula, 'status': 'OK', 'letra': letra})
        except Exception as ex:
            erro_msg = str(ex)
            if 'protected' in erro_msg.lower():
                logger.error(f"  Resultado: PROTEGIDA")
                logger.error(f"  Erro: {erro_msg}")
                resultados.append({**celula, 'status': 'PROTEGIDA', 'letra': letra, 'erro': erro_msg})
            else:
                logger.error(f"  Resultado: ERRO")
                logger.error(f"  Erro: {erro_msg}")
                resultados.append({**celula, 'status': 'ERRO', 'letra': letra, 'erro': erro_msg})

    return resultados

def main():
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path or not os.path.exists(creds_path):
        creds_path = 'dados/credentials.json'
    if not creds_path:
        logger.error("GOOGLE_APPLICATION_CREDENTIALS não definido")
        return

    config = carregar_config()
    spreadsheet_id = config['main_sheet']['spreadsheet_id']
    worksheet_name = config['main_sheet']['worksheet_name']
    header_row = config['main_sheet'].get('header_row_number', 3)

    logger.info(f"Planilha: {spreadsheet_id}")
    logger.info(f"Aba: {worksheet_name}")
    logger.info(f"Linha cabeçalho: {header_row}")

    client = autenticar_client(creds_path)
    ws = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    header_map = validar_mapeamento(ws, header_row)

    celulas_teste = [
        {'row': 18103, 'col': header_map.get('COD SM', 53), 'valor': 'TESTE_COD_SM', 'desc': 'COD SM'},
        {'row': 18103, 'col': header_map.get('SM EFET.', 17), 'valor': 'TESTE_SM_EFET', 'desc': 'SM EFET.'},
        {'row': 18134, 'col': header_map.get('PRÉ SM', 16), 'valor': 'TESTE_PRE_SM', 'desc': 'PRÉ SM'},
        {'row': 18840, 'col': header_map.get('PRÉ SM', 16), 'valor': 'TESTE_PRE_SM_2', 'desc': 'PRÉ SM 2'},
    ]

    resultados = testar_celulas_individuais(ws, celulas_teste)

    logger.info("\n" + "=" * 70)
    logger.info("RESUMO DOS TESTES")
    logger.info("=" * 70)

    for r in resultados:
        status_icon = "OK" if r['status'] == 'OK' else "PROTEGIDA" if r['status'] == 'PROTEGIDA' else "ERRO"
        logger.info(f"  [{status_icon}] {r['desc']}: R{r['row']}C{r['col']} (Coluna {r['letra']})")

    logger.info("\n" + "=" * 70)

    protegidas = [r for r in resultados if r['status'] == 'PROTEGIDA']
    if protegidas:
        logger.warning(f"\nCÉLULAS PROTEGIDAS ENCONTRADAS: {len(protegidas)}")
        for r in protegidas:
            logger.warning(f"  - {r['desc']}: R{r['row']}C{r['col']} (Coluna {r['letra']})")
    else:
        logger.success("\nNENHUMA CÉLULA PROTEGIDA ENCONTRADA!")

if __name__ == "__main__":
    main()
