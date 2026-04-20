import json
import os
import gspread
from google.oauth2.service_account import Credentials
from loguru import logger
import sys

logger.remove()
logger.add(sink=sys.stdout, format="{time:HH:mm:ss} | {level:<7} | {message}", level="INFO")

def autenticar_client(creds_path):
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    return gspread.authorize(creds)

def carregar_batch(path):
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def agrupar_por_coluna(cells):
    grupos = {}
    for cell in cells:
        col = cell['col']
        if col not in grupos:
            grupos[col] = []
        grupos[col].append(cell)
    return grupos

def testar_coluna(ws, client, col, cells, spreadsheet_id, worksheet_name):
    logger.info(f"\n{'='*50}")
    logger.info(f"TESTANDO COLUNA {col} ({len(cells)} células)")
    logger.info(f"{'='*50}")

    ws_teste = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    gspread_cells = []
    for cell in cells[:20]:
        gspread_cells.append(gspread.Cell(cell['row'], cell['col'], str(cell['value'])))

    try:
        resp = ws_teste.update_cells(gspread_cells, value_input_option='USER_ENTERED')
        logger.success(f"Coluna {col}: ✅ SUCESSO - {len(gspread_cells)} células atualizadas")
        logger.debug(f"Response: {repr(resp)}")
        return True
    except Exception as ex:
        logger.error(f"Coluna {col}: ❌ ERRO - {ex}")
        try:
            if hasattr(ex, 'response'):
                logger.error(f"Response details: {ex.response.text}")
        except:
            pass
        return False

def main():
    batch_path = "logs/failed_update_cells_latest.json"

    if not os.path.exists(batch_path):
        logger.error(f"Arquivo não encontrado: {batch_path}")
        logger.info("Procurando arquivos de batch falho...")
        import glob
        arquivos = sorted(glob.glob("logs/failed_update_cells_*.json"))
        if arquivos:
            batch_path = arquivos[-1]
            logger.info(f"Usando: {batch_path}")
        else:
            logger.error("Nenhum batch falho encontrado em logs/")
            return

    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS')
    if not creds_path:
        logger.error("GOOGLE_APPLICATION_CREDENTIALS não definido")
        return

    config_path = os.environ.get('CONFIG_PATH', 'utils/config.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)

    spreadsheet_id = config.get('main_sheet', {}).get('spreadsheet_id')
    worksheet_name = config.get('main_sheet', {}).get('worksheet_name')

    if not spreadsheet_id or not worksheet_name:
        logger.error("Configuração de planilha incompleta")
        return

    logger.info(f"Carregando batch: {batch_path}")
    batch = carregar_batch(batch_path)
    cells = batch.get('data', [])

    if not cells:
        logger.error("Nenhuma célula no batch")
        return

    logger.info(f"Total de células: {len(cells)}")

    grupos = agrupar_por_coluna(cells)

    logger.info(f"\nColunas detectadas: {sorted(grupos.keys())}")
    for col, grupo in sorted(grupos.items()):
        logger.info(f"  Coluna {col}: {len(grupo)} células")

    client = autenticar_client(creds_path)
    ws = client.open_by_key(spreadsheet_id).worksheet(worksheet_name)

    resultados = {}
    for col in sorted(grupos.keys()):
        sucesso = testar_coluna(ws, client, col, grupos[col], spreadsheet_id, worksheet_name)
        resultados[col] = "OK" if sucesso else "FALHOU"

    logger.info(f"\n{'='*50}")
    logger.info("RESUMO DOS TESTES")
    logger.info(f"{'='*50}")
    for col, status in resultados.items():
        emoji = "✅" if status == "OK" else "❌"
        logger.info(f"  Coluna {col}: {emoji} {status}")

    colunas_falhas = [col for col, status in resultados.items() if status == "FALHOU"]
    if colunas_falhas:
        logger.warning(f"\n⚠️ Colunas com problemas: {colunas_falhas}")
        logger.info("Verifique se estas colunas estão protegidas ou têm restrições na planilha.")
    else:
        logger.success("\n✅ Todas as colunas passaram no teste!")

if __name__ == "__main__":
    main()
