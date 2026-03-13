import os
import sys
import gspread
import redis
import json
import time
import pandas as pd
from google.oauth2.service_account import Credentials
from loguru import logger
from utils.helpers import carregar_config

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

# --- FUNÇÃO DE OBTENÇÃO DE DADOS ---
def obter_dados_para_poller(config):
    creds_path = os.environ.get('GOOGLE_APPLICATION_CREDENTIALS') or config.get('creds_path')
    if not creds_path:
        logger.critical("Arquivo de credenciais do Google não configurado. Defina GOOGLE_APPLICATION_CREDENTIALS ou atualize o config.json.")
        return None
    main_sheet_cfg = config.get('main_sheet', {})

    spreadsheet_id = main_sheet_cfg.get('spreadsheet_id')
    worksheet_name = main_sheet_cfg.get('worksheet_name')
    header_row_num = main_sheet_cfg.get('header_row_number', 3)
    header_row_index = header_row_num - 1

    try:
        logger.info("Autenticando no Google Sheets...")
        SCOPES = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        creds = Credentials.from_service_account_file(creds_path, scopes=SCOPES)
        client = gspread.authorize(creds)

        logger.info(f"Abrindo planilha: {spreadsheet_id} | Aba: {worksheet_name}")
        spreadsheet = client.open_by_key(spreadsheet_id)
        worksheet = spreadsheet.worksheet(worksheet_name)

        logger.info("Baixando cabeçalho e colunas relevantes da planilha (otimizado)...")
        headers = worksheet.row_values(header_row_num)
        header_map = {h: i + 1 for i, h in enumerate(headers) if h}

        # Colunas mínimas para controle de filas
        required_cols = ['Status de emissão', 'N° Carga', 'ID 3ZX', 'Status', 'MDFe']
        missing_required = [col for col in required_cols if col not in header_map]
        if missing_required:
            logger.critical(f"Colunas obrigatórias ausentes no cabeçalho: {missing_required}")
            return pd.DataFrame()

        # Colunas adicionais necessárias pelos workers (ex.: conferência usa frete/placas)
        optional_cols = ['Tabela Frete', 'Pedágio', 'Placa', 'Placa 2', 'Origem', 'Destino', 'Motorista', 'CTE']
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

    except gspread.exceptions.APIError as e:
        logger.error(f"Erro de API do Google: {e}. Verifique cotas e permissões.")
        return None
    except Exception as e:
        logger.exception("Erro inesperado ao obter dados do Sheets.")
        return None

# --- LÓGICA PRINCIPAL DO POLLER ---
def iniciar_poller(config):
    redis_cfg = config.get('redis_settings', {})
    poller_cfg = config.get('poller_settings', {})
    
    r_host = redis_cfg.get('host')
    r_port = redis_cfg.get('port')
    r_db = redis_cfg.get('db')

    q_conferencia = redis_cfg.get('conference_queue')
    q_emissao = redis_cfg.get('emission_queue')
    q_manifesto = redis_cfg.get('manifesto_queue')  # Nova fila para jobs de encerramento de manifesto
    s_controle = redis_cfg.get('control_set')
    s_manifesto = redis_cfg.get('manifesto_set', 'jobs_manifesto_em_progresso')  # Novo set de controle para manifesto
    intervalo = poller_cfg.get('poll_interval_seconds', 300) # Padrão 5 min

    if not all([r_db, r_host, r_port, q_conferencia, q_emissao, s_controle, q_manifesto, s_manifesto]):
        logger.critical("Configurações do Redis (filas ou set) estão faltando no config.json (incluindo manifesto_queue/set).")
        return

    try:
        from utils.redis_client import get_redis
        r = get_redis(host=r_host, port=r_port, db=r_db)
    except Exception as e:
        logger.critical(f"Não foi possível conectar ao Redis: {e}")
        return

    STATUS_TERMINAIS = [
        'Finalizado', 
        'Nota de Serviço', 
        'Arquivo c/ Erro',
        'Pendente de Infos',
        '' # Linha vazia
    ]
    STATUS_CONFERIR = config.get('poller_settings', {}).get('statusConferir')

    # --- LOOP PRINCIPAL ---
    while True:
        logger.info("Iniciando novo ciclo de polling...")

        df_planilha = obter_dados_para_poller(config)

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
        dados = df_planilha.to_dict('records')

        for linha in dados:
            try:
                statusEmissao = linha.get('Status de emissão', '').strip()
                status = linha.get('Status', '').strip()
                lt = linha.get('N° Carga', '').strip()
                id = linha.get('ID 3ZX', '').strip()
                mdfe = linha.get('MDFe', '').strip()  # Corrigido para o nome correto da coluna
                mdfe_baixado = linha.get('MDF-e Baixado ?', '').strip().upper()  # Pode ser 'SIM' ou 'NÃO'

                if not lt:
                    continue

                # --- 1. Lógica de Limpeza ---
                if statusEmissao in STATUS_TERMINAIS and mdfe:
                    foi_removido = r.srem(s_controle, id)
                    if foi_removido == 1:
                        logger.debug(f"Job {lt} concluído. Removido do set de controle.")
                        cont_limpeza += 1

                # --- 2. Lógica de Fila: Conferência ---
                elif statusEmissao == 'Pendente' and status in STATUS_CONFERIR:
                    foi_adicionado = r.sadd(s_controle, id)
                    if foi_adicionado == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r.rpush(q_conferencia, json.dumps(job_payload))
                        logger.info(f"Novo job de CONFERÊNCIA para LT {lt} (Linha {linha['original_row_number']})")
                        cont_conferencia += 1
                    else:
                        logger.debug(f"Job {lt} (Conferência) já está em progresso. Pulando.")

                # --- 3. Lógica de Fila: Emissão ---
                elif statusEmissao == 'Verificar Emissão':
                    foi_adicionado = r.sadd(s_controle, id)
                    if foi_adicionado == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r.rpush(q_emissao, json.dumps(job_payload))
                        logger.info(f"Novo job de EMISSÃO para LT {lt} (Linha {linha['original_row_number']})")
                        cont_emissao += 1
                    else:
                        logger.debug(f"Job {lt} (Emissão) já está em progresso. Pulando.")


                # --- 5. Lógica de Fila: Encerramento de Manifesto (MDFe) ---
                elif mdfe and mdfe_baixado != 'SIM':
                    manifesto_id = f"{mdfe}-{lt}"
                    foi_adicionado_manifesto = r.sadd(s_manifesto, manifesto_id)
                    if foi_adicionado_manifesto == 1:
                        job_payload = {
                            'row': linha['original_row_number'],
                            'data': linha
                        }
                        r.rpush(q_manifesto, json.dumps(job_payload))
                        logger.info(f"Novo job de ENCERRAMENTO DE MANIFESTO para MDFe {mdfe} (Linha {linha['original_row_number']})")
                        cont_manifesto += 1
                    else:
                        logger.debug(f"Job de Manifesto {mdfe} já está em progresso. Pulando.")

            except Exception as e:
                logger.error(f"Erro ao processar linha {linha.get('original_row_number', 'N/A')}: {e}")

        logger.info(f"Ciclo de polling finalizado.")
        logger.info(f"Novos Jobs: {cont_conferencia} (Conferência), {cont_emissao} (Emissão), {cont_manifesto} (Encerramento Manifesto).")
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