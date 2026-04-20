import os
import sys
import gspread
import json
from google.oauth2.service_account import Credentials
from loguru import logger

logger.remove()
logger.add(sink=sys.stdout, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")

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

def col_index_para_letra(n):
    resultado = ""
    while n > 0:
        n -= 1
        resultado = chr(65 + (n % 26)) + resultado
        n //= 26
    return resultado

def testar_batch_original(ws, cells, delay=1):
    logger.info("=" * 70)
    logger.info("TESTE DO BATCH ORIGINAL (7 células)")
    logger.info("=" * 70)

    gspread_cells = []
    for cell in cells:
        row = cell['row']
        col = cell['col']
        valor = cell['valor']
        letra = col_index_para_letra(col)
        logger.info(f"  R{row}C{col} ({letra}): {str(valor)[:40]}...")
        gspread_cells.append(gspread.Cell(row, col, str(valor)))

    logger.info("\nEnviando batch completo...")

    try:
        resp = ws.update_cells(gspread_cells, value_input_option='USER_ENTERED')
        logger.success(f"SUCESSO! Batch de {len(gspread_cells)} células enviado!")
        return True
    except Exception as ex:
        erro_msg = str(ex)
        logger.error(f"ERRO no batch: {erro_msg}")

        if 'protected' in erro_msg.lower():
            logger.info("\nDetalhando célula por célula para identificar o problema...")

            for cell in cells:
                row = cell['row']
                col = cell['col']
                valor = cell['valor']
                letra = col_index_para_letra(col)

                logger.info(f"\n  Testando: R{row}C{col} ({letra})...")
                try:
                    gspread_cell = [gspread.Cell(row, col, str(valor))]
                    ws.update_cells(gspread_cell, value_input_option='USER_ENTERED')
                    logger.info(f"    -> OK")
                except Exception as ex2:
                    logger.error(f"    -> ERRO: {ex2}")

                import time
                time.sleep(delay)

        return False

def testar_celulas_especificas(ws, cells, delay=0.5):
    logger.info("\n" + "=" * 70)
    logger.info("TESTE CÉLULA POR CÉLULA")
    logger.info("=" * 70)

    resultados = []

    for cell in cells:
        row = cell['row']
        col = cell['col']
        valor = cell['valor']
        letra = col_index_para_letra(col)
        desc = cell.get('desc', f"R{row}C{col}")

        logger.info(f"\n[Teste] {desc}")
        logger.info(f"  Célula: R{row}C{col} (Coluna {letra})")

        try:
            gspread_cell = [gspread.Cell(row, col, str(valor))]
            resp = ws.update_cells(gspread_cell, value_input_option='USER_ENTERED')
            logger.info(f"  Resultado: SUCESSO")
            resultados.append({**cell, 'status': 'OK', 'letra': letra})
        except Exception as ex:
            erro_msg = str(ex)
            if 'protected' in erro_msg.lower():
                logger.error(f"  Resultado: PROTEGIDA")
                resultados.append({**cell, 'status': 'PROTEGIDA', 'letra': letra, 'erro': erro_msg})
            else:
                logger.error(f"  Resultado: ERRO")
                resultados.append({**cell, 'status': 'ERRO', 'letra': letra, 'erro': erro_msg})

        import time
        time.sleep(delay)

    return resultados

def main():
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path or not os.path.exists(creds_path):
        creds_path = 'dados/credentials.json'

    config = carregar_config()
    spreadsheet_id = config['main_sheet']['spreadsheet_id']
    worksheet_name = config['main_sheet']['worksheet_name']
    header_row = config['main_sheet'].get('header_row_number', 3)

    client = autenticar_client(creds_path)
    ws = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    cells_original = [
        {'row': 18103, 'col': 53, 'valor': '8661978', 'desc': 'COD SM'},
        {'row': 18103, 'col': 17, 'valor': 'OK', 'desc': 'SM EFET.'},
        {'row': 18134, 'col': 16, 'valor': 'TESTE_ERRO_API', 'desc': 'PRÉ SM (erro)'},
        {'row': 18840, 'col': 16, 'valor': 'TESTE_ERRO_API_2', 'desc': 'PRÉ SM 2 (erro)'},
        {'row': 18017, 'col': 39, 'valor': '455183/455181/455182', 'desc': 'CTE'},
        {'row': 18017, 'col': 36, 'valor': '3.092,14', 'desc': '$ Transportado'},
        {'row': 18017, 'col': 35, 'valor': 'Finalizado', 'desc': 'Status de emissão'},
    ]

    sucesso = testar_batch_original(ws, cells_original)

    if not sucesso:
        resultados = testar_celulas_especificas(ws, cells_original)

        logger.info("\n" + "=" * 70)
        logger.info("RESUMO DOS TESTES")
        logger.info("=" * 70)

        for r in resultados:
            status = "OK" if r['status'] == 'OK' else "PROTEGIDA" if r['status'] == 'PROTEGIDA' else "ERRO"
            logger.info(f"  [{status}] {r['desc']}: R{r['row']}C{r['col']} (Coluna {r['letra']})")

        protegidas = [r for r in resultados if r['status'] == 'PROTEGIDA']
        if protegidas:
            logger.warning(f"\nCÉLULAS PROTEGIDAS: {len(protegidas)}")
            for r in protegidas:
                logger.warning(f"  - {r['desc']}: R{r['row']}C{r['col']} (Coluna {r['letra']})")
    else:
        logger.success("\nBATCH FUNCIONOU! Nenhum problema detectado.")

if __name__ == "__main__":
    main()
