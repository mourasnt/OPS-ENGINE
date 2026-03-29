import os
import sys
import gspread
import redis
import json
import time
from google.oauth2.service_account import Credentials
from loguru import logger
from utils.helpers import carregar_config 

# --- CONFIGURAÇÃO DO LOGGER ---
logger.remove()
logger.add(
    sink=sys.stdout, 
    format="{time:DD-MM-YYYY HH:mm:ss} | {level:<7} | [Writer] {message}",
    level="INFO"
)
logger.add(
    "logs/writer.log", 
    rotation="10 MB", 
    retention="5 days", 
    level="DEBUG",
    format="{time:DD-MM-YYYY HH:mm:ss} | {level:<7} | {file}:{line} | {message}"
)

# --- FUNÇÕES HELPER DE AUTENTICAÇÃO ---
from utils.retry import retry
import datetime


@retry((Exception,), tries=3, delay=2, backoff=2, logger=logger)
def autenticar_client(creds_path):
    """Autentica e retorna o CLIENT gspread (com retries)."""
    logger.info("Autenticando no Google Sheets...")
    SCOPES = [
        'https://www.googleapis.com/auth/spreadsheets',
        'https://www.googleapis.com/auth/drive'
    ]
    creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
    client = gspread.authorize(creds)
    logger.success("Cliente gspread autenticado com sucesso.")
    return client


@retry((gspread.exceptions.APIError, Exception), tries=4, delay=2, backoff=2, logger=logger)
def send_update_cells(ws_main, batch_update_cells):
    """Send update_cells to Google Sheets with retries and return API response if available."""
    logger.info(f"Tentando enviar lote de {len(batch_update_cells)} células para o Sheets...")
    resp = ws_main.update_cells(batch_update_cells, value_input_option='USER_ENTERED')
    # Log the API response if any
    try:
        logger.debug(f"update_cells response: {repr(resp)}")
    except Exception:
        logger.debug("update_cells response disponível, mas falha ao serializar a resposta.")
    return resp


@retry((gspread.exceptions.APIError, Exception), tries=4, delay=2, backoff=2, logger=logger)
def send_append_rows(ws_errors, batch_append_rows):
    """Send append_rows to Google Sheets with retries and return API response if available."""
    logger.info(f"Tentando enviar lote de {len(batch_append_rows)} linhas para o Sheets (erros)...")
    resp = ws_errors.append_rows(batch_append_rows, value_input_option='USER_ENTERED')
    try:
        logger.debug(f"append_rows response: {repr(resp)}")
    except Exception:
        logger.debug("append_rows response disponível, mas falha ao serializar a resposta.")
    return resp


def _extract_api_error(exc: Exception) -> str:
    try:
        # gspread APIError often contains useful info in args
        if hasattr(exc, 'response'):
            resp = getattr(exc, 'response')
            try:
                return str(resp)
            except Exception:
                pass
        if exc.args:
            return json.dumps({'args': [str(a) for a in exc.args]})
        return str(exc)
    except Exception:
        return repr(exc)


def persist_failed_batch(kind: str, data, error: str = None):
    ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
    path = f"logs/failed_{kind}_{ts}.json"
    payload = {
        'timestamp': ts,
        'kind': kind,
        'data': data,
        'error': error
    }
    try:
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        logger.warning(f"Batch salvo em {path} para reprocessamento manual. Erro: {error}")
    except Exception:
        logger.exception("Falha ao persistir batch de falha no disco.")

def obter_mapa_cabecalho(worksheet, linha_cabecalho):
    """Lê o cabeçalho 1x e cria um mapa 'Nome Coluna' -> indice (1-based)"""
    try:
        logger.info(f"Obtendo mapa do cabeçalho da linha {linha_cabecalho}...")
        headers = worksheet.row_values(linha_cabecalho)
        header_map = {header: i + 1 for i, header in enumerate(headers) if header}
        logger.info("Mapa de cabeçalho criado.")
        return header_map
    except Exception as e:
        logger.error(f"Falha ao obter mapa de cabeçalho: {e}")
        return None

# --- LÓGICA PRINCIPAL DO WRITER ---
def iniciar_writer(config):

    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or config.get('creds_path')
    # Validação de arquivo de credenciais
    if not creds_path or not os.path.exists(creds_path):
        logger.critical("Arquivo de credenciais do Google não encontrado. Defina GOOGLE_APPLICATION_CREDENTIALS ou atualize o config.json para apontar para o JSON.")
        return

    main_sheet_cfg = config.get('main_sheet', {})
    main_sheet_id = main_sheet_cfg.get('spreadsheet_id')
    main_ws_name = main_sheet_cfg.get('worksheet_name')
    header_row = main_sheet_cfg.get('header_row_number', 3)

    error_sheet_cfg = config.get('error_log_sheet', {})
    error_sheet_id = error_sheet_cfg.get('spreadsheet_id')
    error_ws_name = error_sheet_cfg.get('worksheet_name')

    redis_cfg = config.get('redis_settings', {})
    r_host = os.environ.get('REDIS_HOST')
    r_port = int(os.environ.get('REDIS_PORT'))
    r_db = redis_cfg.get('db_fila')
    fila_resultados = redis_cfg.get('results_queue')

    writer_cfg = config.get('writer_settings', {})
    max_batch_cells = writer_cfg.get('batch_max_size_cells', 200)
    max_batch_rows = writer_cfg.get('batch_max_size_rows', 50)
    max_wait_s = writer_cfg.get('batch_max_wait_seconds', 5)
    
    # --- Validações Críticas ---
    if not all([creds_path, main_sheet_id, main_ws_name, error_sheet_id, error_ws_name, fila_resultados]):
        logger.critical("Configurações essenciais (planilhas, creds, fila) estão faltando.")
        if "PREENCHA_AQUI" in f"{error_sheet_id}{error_ws_name}":
            logger.critical(">>> Você esqueceu de preencher os dados da 'error_log_sheet' no config.json.")
        return
        
    try:
        # --- Conexões ---
        client = autenticar_client(creds_path)
        if not client: return

        logger.info(f"Abrindo planilha principal: {main_sheet_id} | Aba: {main_ws_name}")
        ws_main = client.open_by_key(main_sheet_id).worksheet(main_ws_name)
        
        logger.info(f"Abrindo planilha de erros: {error_sheet_id} | Aba: {error_ws_name}")
        ws_errors = client.open_by_key(error_sheet_id).worksheet(error_ws_name)

        header_map = obter_mapa_cabecalho(ws_main, header_row)
        if not header_map: return
        
        from utils.redis_client import get_redis
        r = get_redis(host=r_host, port=r_port, db=r_db)
        logger.success(f"Conectado ao Redis em {r_host}:{r_port}. Ouvindo a fila: '{fila_resultados}'")
    
    except Exception as e:
        logger.critical(f"Falha na inicialização (Sheets ou Redis): {e}")
        return

    # --- Listas de Lote (Batch) ---
    batch_update_cells = []
    batch_append_rows = []
    
    while True:
        try:
            # 1. OUVIR A FILA
            resultado_bruto = r.blpop([fila_resultados], timeout=max_wait_s)

            if resultado_bruto:
                # 2. PROCESSOAR O JOB RECEBIDO
                _, job_json = resultado_bruto
                job = json.loads(job_json)
                
                tipo_job = job.get('tipo_job')
                payload = job.get('payload')
                
                if tipo_job == "UPDATE_SHEET":
                    linha = int(payload['row'])
                    colunas = payload['colunas']
                    valores = payload['novos_valores']
                    
                    for coluna, valor in zip(colunas, valores):
                        col_idx = header_map.get(coluna)
                        if col_idx:
                            batch_update_cells.append(gspread.Cell(linha, col_idx, str(valor)))
                        else:
                            logger.debug(f"UPDATE (Linha {linha}): Coluna '{coluna}' não encontrada no mapa.")
                
                elif tipo_job == "APPEND_ERROR_LOG":
                    dados_linha = payload['dados_linha']
                    batch_append_rows.append(dados_linha)
                    logger.debug(f"APPEND: Novo log de erro adicionado ao lote: {dados_linha[1]}")

                else:
                    logger.warning(f"Job recebido com tipo desconhecido: '{tipo_job}'")

            # 3. VERIFICAR SE OS LOTES DEVEM SER ENVIADOS
            cells_cheio = len(batch_update_cells) >= max_batch_cells
            rows_cheio = len(batch_append_rows) >= max_batch_rows
            timeout_sem_job = (resultado_bruto is None) and (len(batch_update_cells) > 0 or len(batch_append_rows) > 0)

            if cells_cheio or rows_cheio or timeout_sem_job:
                
                # --- 4. ENVIAR LOTE DE UPDATES ---
                if batch_update_cells:
                    logger.info(f"Enviando lote de {len(batch_update_cells)} CÉLULAS para atualização...")
                    try:
                        resp = send_update_cells(ws_main, batch_update_cells)
                        # Log response details at INFO level if it contains useful data
                        try:
                            logger.info(f"update_cells API response: {repr(resp)}")
                        except Exception:
                            logger.debug("Resposta do update_cells não serializável para log.")

                        batch_update_cells.clear()
                        logger.success("Lote de CÉLULAS enviado com sucesso.")
                    except Exception as ex:
                        err = _extract_api_error(ex)
                        logger.exception(f"Falha ao enviar lote de CÉLULAS. Mantendo no buffer para tentativa futura. Erro API: {err}")
                        # Persiste o batch para reprocessamento manual
                        try:
                            payload = [{'row': c.row, 'col': c.col, 'value': c.value} for c in batch_update_cells]
                        except Exception:
                            payload = str(batch_update_cells[:50])
                        persist_failed_batch('update_cells', payload, error=err)
                        time.sleep(30)
                        # Não limpar o batch: tentaremos novamente no próximo ciclo
                
                # --- 5. ENVIAR LOTE DE APPENDS ---
                if batch_append_rows:
                    logger.info(f"Enviando lote de {len(batch_append_rows)} LINHAS para log de erros...")
                    try:
                        resp = send_append_rows(ws_errors, batch_append_rows)
                        try:
                            logger.info(f"append_rows API response: {repr(resp)}")
                        except Exception:
                            logger.debug("Resposta do append_rows não serializável para log.")

                        batch_append_rows.clear()
                        logger.success("Lote de LINHAS enviado com sucesso.")
                    except Exception as ex:
                        err = _extract_api_error(ex)
                        logger.exception(f"Falha ao enviar lote de LINHAS. Mantendo no buffer para tentativa futura. Erro API: {err}")
                        # Persiste o batch para reprocessamento manual
                        try:
                            payload = batch_append_rows
                        except Exception:
                            payload = str(batch_append_rows[:20])
                        persist_failed_batch('append_rows', payload, error=err)
                        time.sleep(30)
                        # Não limpar o batch: tentaremos novamente no próximo ciclo

        except gspread.exceptions.APIError as e:
            logger.error(f"Erro de API do Google: {e}. Tentando novamente em 60s...")
            logger.debug(f"Células no lote: {len(batch_update_cells)} | Linhas no lote: {len(batch_append_rows)}")
            time.sleep(60) 
        
        except redis.exceptions.ConnectionError as e:
            logger.critical(f"Perda de conexão com o Redis: {e}. Tentando reconectar com backoff...")
            # Tentativas de reconexão com backoff exponencial
            backoff = 1
            reconectado = False
            for attempt in range(1, 6):
                try:
                    time.sleep(backoff)
                    r = redis.Redis(host=r_host, port=r_port, db=r_db, decode_responses=True)
                    r.ping()
                    logger.success("Reconectado ao Redis com sucesso.")
                    reconectado = True
                    break
                except Exception as ex:
                    logger.error(f"Tentativa {attempt} falhou: {ex}")
                    backoff *= 2
            if not reconectado:
                logger.critical("Não foi possível reconectar ao Redis após várias tentativas. Encerrando Writer.")
                return
            else:
                continue 
            
        except KeyboardInterrupt:
            logger.info("Interrupção manual. Enviando lotes finais...")
            # Tenta enviar o que sobrou
            if batch_update_cells:
                ws_main.update_cells(batch_update_cells, value_input_option='USER_ENTERED')
            if batch_append_rows:
                ws_errors.append_rows(batch_append_rows, value_input_option='USER_ENTERED')
            logger.info("Encerrando.")
            break

        except Exception as e:
            logger.exception("Erro inesperado no loop do Writer. Limpando lotes e aguardando antes de tentar novamente.")
            batch_update_cells.clear()
            batch_append_rows.clear()
            time.sleep(5)


if __name__ == "__main__":
    config = carregar_config() 
    if config:
        iniciar_writer(config)
    else:
        logger.critical("Não foi possível carregar a configuração. Encerrando Writer.")
