import os
import sys
import gspread
import redis
import json
import time
import pandas as pd
from datetime import datetime
from zoneinfo import ZoneInfo
from google.oauth2.service_account import Credentials
from loguru import logger
from utils.helpers import carregar_config
from utils.job_history import JobHistory
from utils.api_client import RasterService

# --- CONFIGURAÇÃO DO LOGGER ---
logger.remove()
logger.add(
    sink=sys.stdout, 
    format="{time:DD-MM-YYYY HH:mm:ss} | {level:<7} | [Poller] {message}",
    level="INFO"
)
logger.add(
    "logs/poller.log", 
    rotation="10 MB", 
    retention="5 days", 
    level="DEBUG",
    format="{time:DD-MM-YYYY HH:mm:ss} | {level:<7} | {file}:{line} | {message}"
)

# --- FUNÇÕES AUXILIARES (NOVAS PARA SM) ---
def atualizar_cache_bases(client, config, r_bases):
    """Lê a planilha de Bases/Locais e atualiza o Redis DB 2."""
    loc_cfg = config.get('locations_sheet', {})
    loc_sheet_id = loc_cfg.get('spreadsheet_id')
    loc_ws_name = loc_cfg.get('worksheet_name')
    
    if not loc_sheet_id or not loc_ws_name:
        logger.warning("Aba de 'locations_sheet' não configurada no config.json. Cache de bases ignorado.")
        return

    try:
        ws = client.open_by_key(loc_sheet_id).worksheet(loc_ws_name)
        records = ws.get_all_records()
        r_bases.set("cache:bases", json.dumps(records))
        logger.debug(f"Cache de Bases atualizado no Redis DB 2 com {len(records)} registros.")
    except Exception as e:
        logger.error(f"Erro ao atualizar cache de bases: {e}")

def is_within_eta(eta_str: str, hr_antes: int) -> bool:
    """Valida se o ETA está dentro do limite de horas configurado."""
    if not eta_str: return False
    eta_str = eta_str.strip()
    try:
        if "T" in eta_str: dt = datetime.fromisoformat(eta_str)
        else: dt = datetime.strptime(eta_str, "%d/%m/%Y %H:%M")
    except Exception:
        try: dt = datetime.strptime(eta_str, "%d/%m/%Y %H:%M:%S")
        except Exception: return False
        
    dt = dt.replace(tzinfo=ZoneInfo("America/Sao_Paulo"))
    delta_hours = (dt - datetime.now(ZoneInfo("America/Sao_Paulo"))).total_seconds() / 3600
    return delta_hours <= hr_antes


# --- FUNÇÃO DE OBTENÇÃO DE DADOS ---
def obter_dados_para_poller(config, client):
    # Passamos o client já instanciado do loop principal para economizar tempo
    main_sheet_cfg = config.get('main_sheet', {})
    spreadsheet_id = main_sheet_cfg.get('spreadsheet_id')
    worksheet_name = main_sheet_cfg.get('worksheet_name')
    header_row_num = main_sheet_cfg.get('header_row_number', 3)
    header_row_index = header_row_num - 1

    try:
        logger.info(f"Abrindo planilha: {spreadsheet_id} | Aba: {worksheet_name}")
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(worksheet_name)

        logger.info("Baixando cabeçalho e colunas relevantes da planilha (otimizado)...")
        headers = worksheet.row_values(header_row_num)
        header_map = {h: i + 1 for i, h in enumerate(headers) if h}

        # Colunas mínimas para controle de filas
        required_cols = ['Status de emissão', 'N° Carga', 'ID 3ZX', 'Status', 'MDFe', 'MDF-e Baixado ?']
        missing_required = [col for col in required_cols if col not in header_map]
        if missing_required:
            logger.critical(f"Colunas obrigatórias ausentes no cabeçalho: {missing_required}")
            return pd.DataFrame()

        # Colunas adicionais necessárias pelos workers e pelo CDC + SM Efetivação
        optional_cols = [
            'Tabela Frete', 'Pedágio', 'Placa', 'Placa 2', 'Origem', 'Destino', 'Motorista', 'CTE',
            'ETA Origem', 'ETA Destino', 'CPT', 'PRÉ SM', 'SM EFET.', 'CPF'  # Adicionadas as colunas do SM
        ]
        missing_optional = [col for col in optional_cols if col not in header_map]
        if missing_optional:
            logger.warning(f"Colunas opcionais não encontradas: {missing_optional}. Valores serão preenchidos vazios.")

        cols_to_fetch = required_cols + optional_cols

        # Busca as colunas solicitadas; se não existir, preenche com lista vazia
        cols_values = {}
        max_len = 0
        for col in cols_to_fetch:
            if col in header_map:
                col_idx = header_map[col]
                values = worksheet.col_values(col_idx)
                values = values[header_row_index + 1:]  # Remove cabeçalho
            else:
                values = []

            cols_values[col] = values
            max_len = max(max_len, len(values))

        # Normaliza tamanhos e monta lista de linhas
        rows = []
        for i in range(max_len):
            row = {}
            for col_name, col_list in cols_values.items():
                row[col_name] = col_list[i] if i < len(col_list) else ''
            row['original_row_number'] = i + header_row_num + 1
            rows.append(row)

        df = pd.DataFrame(rows)
        logger.info(f"Planilha processada. Total estimado de {len(df)} linhas (após cabeçalho).")
        return df

    except gspread.exceptions.APIError as e:
        logger.error(f"Erro de API do Google: {e}. Verifique cotas e permissões.")
        return None
    except Exception as e:
        logger.exception("Erro inesperado ao obter dados do Sheets.")
        return None


# --- FUNÇÃO DE DETECÇÃO DE MUDANÇAS (CDC) ---
def verificar_mudancas_cdc(r_state, linha, id_3zx):
    """
    Compara o estado atual da linha com o estado armazenado no Redis.
    Apenas loga se houver mudanças.
    """
    if not id_3zx:
        return

    # Campos que desejamos monitorar
    campos_monitorados = ["Origem", "Destino", "Motorista", "Placa", "Placa 2", "Status"]
    
    estado_atual = {campo: str(linha.get(campo, "")).strip() for campo in campos_monitorados}
    redis_key = f"viagem_state:{id_3zx}"

    try:
        estado_anterior_str = r_state.get(redis_key)

        if estado_anterior_str:
            estado_anterior = json.loads(estado_anterior_str)
            mudancas = {}
            
            # Compara campo a campo
            for campo in campos_monitorados:
                val_anterior = estado_anterior.get(campo, "")
                val_atual = estado_atual.get(campo, "")
                
                if val_anterior != val_atual:
                    mudancas[campo] = {"de": val_anterior, "para": val_atual}

            if mudancas:
                logger.info(f"🔄 [CDC DETECTOU MUDANÇA] ID 3ZX: {id_3zx} | Alterações: {json.dumps(mudancas, ensure_ascii=False)}")
                r_state.set(redis_key, json.dumps(estado_atual))
        else:
            # Primeira vez vendo essa viagem: salva o estado base (silenciosamente)
            r_state.set(redis_key, json.dumps(estado_atual))
            
    except Exception as e:
        logger.error(f"Erro ao verificar mudanças CDC para ID {id_3zx}: {e}")
        
def verificar_pendencias_api(config, r_filas):
    """Lê os jobs pendentes no Redis, consulta a API e manda ordens pro Writer"""
    try:
        history = JobHistory(redis_client=r_filas)
        pending_jobs = history.get_pending_jobs()
        if not pending_jobs:
            return

        logger.info(f"Verificando status de {len(pending_jobs)} jobs de SM pendentes na API...")
        
        api_base_url = os.environ.get('API_BASE_URL', config.get('sm_settings', {}).get('api_base_url', ''))
        api_raster = RasterService(api_base_url)
        fila_resultados = config.get('redis_settings', {}).get('results_queue', 'fila:resultados')
        s_controle = config.get('redis_settings', {}).get('control_set', 'jobs_em_progresso')

        for id_3zx, check_data in pending_jobs.items():
            job_id = check_data.get("job_id")
            rownum = check_data.get("row")
            job_type = check_data.get("job_type", "criar_pre_sm")
            
            if not job_id: continue
            job_id_clean = job_id.replace("job:", "")
            
            # Bate na API para verificar status
            resp = api_raster.status_job(job_id_clean)
            if not getattr(resp, "ok", False): continue
            
            status_data = resp.json()
            api_status = status_data.get("status", "")
            api_result = status_data.get("result", {})

            if api_status == "complete":
                coluna_alvo = "PRÉ SM" if job_type == "criar_pre_sm" else "SM EFET."
                valor_final = ""
                
                if api_result.get("sucesso"):
                    if job_type == "criar_pre_sm":
                        valor_final = api_result.get("resultado", {}).get("PreSM", {}).get("Codigo", "ERRO: Sem Código")
                    else:
                        valor_final = "OK"
                    history.update_job_status(id_3zx, job_id_clean, rownum, "SUCCESS")
                else:
                    err = api_result.get("erro") or "Erro desconhecido"
                    valor_final = f"ERRO: {err}"
                    history.update_job_status(id_3zx, job_id_clean, rownum, "ERROR", err)

                # Manda ordem para o Writer preencher a planilha
                job_writer = {
                    "tipo_job": "UPDATE_SHEET",
                    "payload": {"row": int(rownum), "colunas": [coluna_alvo], "novos_valores": [str(valor_final)]}
                }
                r_filas.rpush(fila_resultados, json.dumps(job_writer))
                
                # Libera o job do Set de Controle
                r_filas.srem(s_controle, id_3zx)

    except Exception as e:
        logger.error(f"Erro ao verificar pendências da API no Poller: {e}")


# --- LÓGICA PRINCIPAL DO POLLER ---
def iniciar_poller(config):
    redis_cfg = config.get('redis_settings', {})
    poller_cfg = config.get('poller_settings', {})
    sm_cfg = config.get('sm_settings', {}) # Novas configs de SM
    
    r_host = os.environ.get('REDIS_HOST')
    r_port = int(os.environ.get('REDIS_PORT'))
    
    # DB para filas (Jobs)
    r_db_filas = redis_cfg.get('db_fila')
    # DB para estado do CDC
    r_db_estado = redis_cfg.get('db_state') 
    # DB para Cache de Bases (SM)
    r_db_bases = redis_cfg.get('db_bases')

    q_conferencia = redis_cfg.get('conference_queue')
    q_emissao = redis_cfg.get('emission_queue')
    q_manifesto = redis_cfg.get('manifesto_queue')
    
    # Novas filas de SM
    q_pre_sm = redis_cfg.get('pre_sm_queue', 'queue:pre_sm')
    q_efetivacao = redis_cfg.get('efetivacao_queue', 'queue:efetivacao_sm')

    s_controle = redis_cfg.get('control_set')
    s_manifesto = redis_cfg.get('manifesto_set', 'jobs_manifesto_em_progresso')
    intervalo = poller_cfg.get('poll_interval_seconds', 300)

    if not all([r_host, r_port, q_conferencia, q_emissao, s_controle, q_manifesto, s_manifesto]):
        logger.critical("Configurações do Redis (filas ou set) estão faltando no config.json.")
        return

    try:
        from utils.redis_client import get_redis
        # Conexão para as filas de trabalho
        r_filas = get_redis(host=r_host, port=r_port, db=r_db_filas)
        # Conexão exclusiva para o estado das viagens (CDC)
        r_state = redis.Redis(host=r_host, port=r_port, db=r_db_estado, decode_responses=True)
        # Conexão exclusiva para o Cache de Bases
        r_bases = redis.Redis(host=r_host, port=r_port, db=r_db_bases, decode_responses=True)
        r_filas.ping() 
    except Exception as e:
        logger.critical(f"Não foi possível conectar ao(s) Redis: {e}")
        return

    STATUS_EMISSAO_TERMINAIS = [
        'Finalizado', 
        'Nota de Serviço', 
        'Arquivo c/ Erro',
        'Pendente de Infos'
    ]
    STATUS_CONFERIR = config.get('poller_settings', {}).get('statusConferir')
    
    # Variáveis da lógica de Risco
    STATUS_PRE_SM = sm_cfg.get('status_array_pre_sm', ['PROGRAMADA'])
    STATUS_EFETIVACAO = sm_cfg.get('status_array_efetivacao', ['EM VIAGEM'])
    HR_ANTES_ETA = sm_cfg.get('HR_ANTES_ETA', 24)


    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or config.get('creds_path')

    # --- LOOP PRINCIPAL ---
    while True:
        logger.info("Iniciando novo ciclo de polling...")

        # Instancia o cliente 1x por ciclo
        try:
            logger.info("Autenticando no Google Sheets...")
            SCOPES = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
            creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
            client = gspread.authorize(creds)
        except Exception as e:
            logger.error(f"Erro ao autenticar no Google Sheets: {e}")
            time.sleep(intervalo)
            continue

        # 1. Atualiza cache de bases para o db_bases do Redis
        atualizar_cache_bases(client, config, r_bases)

        # 2. Verifica pendências de jobs de SM na API e manda ordens pro Writer preencher a planilha
        verificar_pendencias_api(config, r_filas)

        # 3. Puxa a planilha
        df_planilha = obter_dados_para_poller(config, client)

        if df_planilha is None:
            logger.error("Falha ao obter dados da planilha. Pulando este ciclo.")
            time.sleep(intervalo)
            continue
            
        if df_planilha.empty:
            logger.info("Planilha vazia. Nenhum dado para processar.")
            time.sleep(intervalo)
            continue

        cont_conferencia = 0
        cont_emissao = 0
        cont_manifesto = 0
        cont_limpeza = 0
        cont_pre_sm = 0
        cont_efetivacao = 0
        
        dados = df_planilha.to_dict('records')

        for linha in dados:
            try:
                statusEmissao = linha.get('Status de emissão', '').strip()
                status = linha.get('Status', '').strip()
                lt = linha.get('N° Carga', '').strip()
                id_3zx = linha.get('ID 3ZX', '').strip()
                mdfe = linha.get('MDFe', '').strip()
                mdfe_baixado = linha.get('MDF-e Baixado ?', '').strip().upper()
                
                # Campos de Risco
                pre_sm_val = str(linha.get('PRÉ SM', '')).strip()
                sm_efet_val = str(linha.get('SM EFET.', '')).strip().upper()

                if not lt:
                    continue

                # ==========================================
                # NOVO: VERIFICADOR DE MUDANÇAS (CDC)
                # ==========================================
                verificar_mudancas_cdc(r_state, linha, id_3zx)

                # --- 1. Lógica de Limpeza ---
                if statusEmissao in STATUS_EMISSAO_TERMINAIS and (mdfe_baixado == "SIM" or mdfe == False ):
                    foi_removido = r_filas.srem(s_controle, id_3zx)
                    if foi_removido == 1:
                        logger.debug(f"Job {lt} concluído. Removido do set de controle.")
                        cont_limpeza += 1

                # ==========================================
                # LÓGICA DE GERENCIAMENTO DE RISCO (SM)
                # ==========================================
                # Fila: PRÉ-SM
                if not pre_sm_val and status in STATUS_PRE_SM:
                    if is_within_eta(linha.get('ETA Origem', ''), HR_ANTES_ETA):
                        foi_adicionado = r_filas.sadd(s_controle, id_3zx)
                        if foi_adicionado == 1:
                            job_payload = {'row': linha['original_row_number'], 'data': linha}
                            r_filas.rpush(q_pre_sm, json.dumps(job_payload))
                            logger.info(f"Novo job de PRÉ-SM para LT {lt} (Linha {linha['original_row_number']})")
                            cont_pre_sm += 1
                        else:
                            logger.debug(f"Job {lt} (Pré-SM) já está em progresso. Pulando.")
                            
                # Fila: EFETIVAÇÃO SM
                elif pre_sm_val.isdigit() and sm_efet_val == 'PENDENTE' and status in STATUS_EFETIVACAO:
                    foi_adicionado = r_filas.sadd(s_controle, id_3zx)
                    if foi_adicionado == 1:
                        job_payload = {'row': linha['original_row_number'], 'data': linha}
                        r_filas.rpush(q_efetivacao, json.dumps(job_payload))
                        logger.info(f"Novo job de EFETIVAÇÃO SM para LT {lt} (Linha {linha['original_row_number']})")
                        cont_efetivacao += 1
                    else:
                        logger.debug(f"Job {lt} (Efetivação SM) já está em progresso. Pulando.")
                # ==========================================


                # --- 2. Lógica de Fila: Conferência ---
                elif statusEmissao == 'Pendente' and status in STATUS_CONFERIR:
                    foi_adicionado = r_filas.sadd(s_controle, id_3zx)
                    if foi_adicionado == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r_filas.rpush(q_conferencia, json.dumps(job_payload))
                        logger.info(f"Novo job de CONFERÊNCIA para LT {lt} (Linha {linha['original_row_number']})")
                        cont_conferencia += 1
                    else:
                        logger.debug(f"Job {lt} (Conferência) já está em progresso. Pulando.")

                # --- 3. Lógica de Fila: Emissão ---
                elif statusEmissao == 'Verificar Emissão':
                    foi_adicionado = r_filas.sadd(s_controle, id_3zx)
                    if foi_adicionado == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r_filas.rpush(q_emissao, json.dumps(job_payload))
                        logger.info(f"Novo job de EMISSÃO para LT {lt} (Linha {linha['original_row_number']})")
                        cont_emissao += 1
                    else:
                        logger.debug(f"Job {lt} (Emissão) já está em progresso. Pulando.")

                # --- 4. Lógica de Fila: Encerramento de Manifesto (MDFe) ---
                elif mdfe and mdfe_baixado != 'SIM' and statusEmissao in STATUS_EMISSAO_TERMINAIS and status in ("ENTREGA FINALIZADA", "AGUARDANDO DESCARGA"):
                    logger.debug(f"MDFe {mdfe} encontrado para LT {lt}. Verificando se deve criar job de encerramento...")
                    manifesto_id = f"{mdfe}-{lt}"
                    foi_adicionado_manifesto = r_filas.sadd(s_manifesto, manifesto_id)
                    if foi_adicionado_manifesto == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r_filas.rpush(q_manifesto, json.dumps(job_payload))
                        logger.info(f"Novo job de ENCERRAMENTO DE MANIFESTO para MDFe {mdfe} (Linha {linha['original_row_number']})")
                        cont_manifesto += 1
                    else:
                        logger.debug(f"Job de Manifesto {mdfe} já está em progresso. Pulando.")

            except Exception as e:
                logger.error(f"Erro ao processar linha {linha.get('original_row_number', 'N/A')}: {e}")

        logger.info(f"Ciclo de polling finalizado.")
        logger.info(f"Novos Jobs - Conf: {cont_conferencia} | Emiss: {cont_emissao} | Manifesto: {cont_manifesto} | Pré-SM: {cont_pre_sm} | Efetivação: {cont_efetivacao}")
        logger.info(f"Jobs Limpos: {cont_limpeza}.")
        logger.info(f"Próximo ciclo em {intervalo} segundos.")
        time.sleep(intervalo)

# --- PONTO DE ENTRADA ---
if __name__ == "__main__":
    config = carregar_config()
    if config:
        iniciar_poller(config)
    else:
        logger.critical("Não foi possível carregar a configuração. Encerrando Poller.")