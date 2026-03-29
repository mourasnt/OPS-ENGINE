import pandas as pd
import redis
import json
import time
import datetime
import os
from loguru import logger
from playwright.sync_api import Page
from utils.fluxo_utils import goto_cards, analisar_status_emissao, identificar_tipo_card
from utils.filtros import filtro_cards
from fluxos.revisar import revisar_lt
from fluxos.preencher_cte import preencher_cte
from fluxos.preencher_mdfe import preencher_mdfe
from utils.watchdog import TimeoutDetector

config_path = os.path.join(os.path.dirname(__file__), "..", "utils", "config.json")
with open(config_path, "r", encoding="utf-8") as f:
    timeout_config = json.load(f)

PAGE_RELOAD_TIMEOUT = timeout_config.get("timeout_settings", {}).get("page_reload_ms", 45000)


def enviar_job_update(r_client: redis.Redis, config: dict, row: int, colunas: list, valores: list):
    """Envia um job de ATUALIZAÇÃO para a fila do Writer."""
    try:
        results_queue = config['redis_settings']['results_queue']
        payload = {
            "tipo_job": "UPDATE_SHEET",
            "payload": {
                "row": row,
                "colunas": colunas,
                "novos_valores": valores
            }
        }
        r_client.rpush(results_queue, json.dumps(payload))
        logger.debug(f"[Worker Emissão] Job UPDATE (Linha {row}) enviado ao Writer: {colunas} = {valores}")
    except Exception as e:
        logger.error(f"[Worker Emissão] Falha ao enviar job UPDATE (Linha {row}) para o Redis: {e}")

def fluxo_verificar_emissao_worker(page: Page, config: dict):
    import threading
    worker_name = threading.current_thread().name
    logger.info(f"[Worker Emissão] Iniciando... (Thread: {worker_name})")
    
    redis_cfg = config.get('redis_settings', {})
    r_host = os.environ.get('REDIS_HOST')
    r_port = int(os.environ.get('REDIS_PORT'))
    r_db_filas = redis_cfg.get('db_filas')
    q_emissao = redis_cfg.get('emission_queue')
    s_controle = redis_cfg.get('control_set')
    if not s_controle:
        logger.critical(f"[Worker Emissão] Config 'control_set' não encontrada. O Worker não pode limpar o cadeado!")
        return
    
    watchdog = config.get('watchdog', None)
    
    pool_manager = config.get('thread_pool_manager', None)
    
    def verificar_deve_morrer() -> bool:
        """Verifica se esta thread foi marcada para morte por downscaling."""
        try:
            if pool_manager:
                return pool_manager.thread_deve_morrer("emissao")
        except Exception as e:
            logger.error(f"[Worker Emissão] Erro ao verificar downscaling: {e}")
        return False
    
    try:
        from utils.redis_client import get_redis
        r = get_redis(host=r_host, port=r_port, db=r_db_filas)
        logger.info(f"[Worker Emissão] Conectado ao Redis em {r_host}:{r_port}. Ouvindo a fila '{q_emissao}'")
    except Exception as e:
        logger.critical(f"[Worker Emissão] Não foi possível conectar ao Redis: {e}. Worker encerrando.")
        return

    def verificar_kill_signal(job_id_atual: str) -> bool:
        """Verifica se este job foi sinalizado para morrer pelo watchdog."""
        try:
            kill_signals = r.smembers("watchdog:kill_workers")
            for signal_json in kill_signals:
                try:
                    signal = json.loads(signal_json)
                    if signal.get("job_id") == job_id_atual:
                        r.srem("watchdog:kill_workers", signal_json)
                        logger.warning(f"[Worker Emissão] 💀 Kill signal detectado para job '{job_id_atual}'!")
                        return True
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.error(f"[Worker Emissão] Erro ao verificar kill signal: {e}")
        return False

    tentativas_reconexao = 0
    max_tentativas_reconexao = 3
    job_atual = None

    while True:
        numero_lt = None
        
        if verificar_deve_morrer():
            logger.warning(f"[Worker Emissão] 💀 Downscaling detectado. Thread será encerrada.")
            break
        
        if job_atual and verificar_kill_signal(job_atual):
            logger.critical(f"[Worker Emissão] Encerrando thread por kill signal do Watchdog!")
            break
        
        try:
            resultado_bruto = r.blpop([q_emissao], timeout=60) 
            
            if resultado_bruto is None:
                logger.debug(f"[Worker Emissão] Nenhum job recebido. Reiniciando loop.")
                continue

            _, job_json = resultado_bruto
            job = json.loads(job_json)
            
            linha_data = job['data']
            linha_num = job['row']

            numero_lt = (linha_data.get("N° Carga") or "").strip()
            id = (linha_data.get("ID 3ZX") or "").strip() or f"{numero_lt}-{linha_num}"
            logger.info(f"[Worker Emissão] Job recebido: LT {numero_lt} (Linha {linha_num}). Processando...")
            
            job_atual = numero_lt
            
            tentativas_reconexao = 0
            
            if watchdog:
                watchdog.registrar_job(numero_lt, worker_id=worker_name, tipo_job="emissao")

        except redis.exceptions.ConnectionError as e:
            tentativas_reconexao += 1
            logger.error(f"[Worker Emissão] Erro de conexão Redis ({tentativas_reconexao}/{max_tentativas_reconexao}): {e}")
            if tentativas_reconexao >= max_tentativas_reconexao:
                logger.critical("[Worker Emissão] Máximo de tentativas de reconexão atingido. Worker encerrando.")
                break
            time.sleep(10)
            continue
        except Exception as e:
            logger.error(f"[Worker Emissão] Erro ao obter/decodificar job do Redis: {e}")
            time.sleep(5)
            continue

        try:
            numero_lt = (linha_data.get("N° Carga") or "").strip()
            cte_valor = (linha_data.get("CTE") or "").strip()
            mdfe_valor = (linha_data.get("MDFe") or "").strip()
            status_transporte = (linha_data.get("Status") or "").strip()
            id = (linha_data.get("ID 3ZX") or "").strip() or f"{numero_lt}-{linha_num}"

            if not numero_lt:
                motivo = "Linha sem 'N° Carga'"
                logger.warning(f"[Worker Emissão] Linha {linha_num} pulada: {motivo}")
                continue
            
            numero_lt = str(numero_lt).strip()

            cte_preenchido = pd.notna(cte_valor) and str(cte_valor).strip() != ""
            mdfe_preenchido = pd.notna(mdfe_valor) and str(mdfe_valor).strip() != ""
            data_agora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            if cte_preenchido and mdfe_preenchido:
                logger.info(f"[Worker Emissão] LT {numero_lt}: CT-e e MDF-e já estão preenchidos na planilha.")
                if str(cte_valor).strip() in ["NFS", "Nota de Serviço"]:
                    enviar_job_update(r, config, linha_num, ["Status de emissão"], ["Nota de Serviço"])
                else:
                    enviar_job_update(r, config, linha_num, ["Status de emissão"], ["Finalizado"])
                continue
            

            logger.info(f"[Worker Emissão] Iniciando RPA para LT: {numero_lt} (Linha {linha_num})")
            from utils.manifesto_utils import navegar_e_validar_mdfe
            resultado = navegar_e_validar_mdfe(page, numero_lt)
            if not resultado:
                logger.error(f"[Worker Emissão] Não foi possível encontrar o card ou analisar o status para a LT {numero_lt}.")
                continue

            card = resultado.get("card")
            analise = resultado.get("analise")
            status_card = analise.get("status_card") if analise else None
            status_mdfe = resultado.get("status_mdfe")

            if not card or not status_card:
                logger.error(f"[Worker Emissão] Não foi possível encontrar o card ou analisar o status para a LT {numero_lt}.")
                continue

            colunas_update = ["Data Verificação"]
            valores_update = [data_agora]

            if status_card == "ag._revisão":
                tipo_card = identificar_tipo_card(card)
                
                if tipo_card == "cte":
                    logger.info(f"[Worker Emissão] [LT {numero_lt}] Status 'ag._revisão' (CTE). Executando RPA de revisão...")
                    with TimeoutDetector("Revisar LT", max_seconds=30, job_id=numero_lt):
                        resultado_rpa = revisar_lt(page, numero_lt)
                    
                    if resultado_rpa["status"] == "sucesso":
                        logger.success(f"[Worker Emissão] [LT {numero_lt}] Revisão concluída. Job será re-processado pelo Poller.")
                        colunas_update.extend(["Data Revisão"])
                        valores_update.extend([data_agora])
                    else:
                        motivo = resultado_rpa["motivo"]
                        logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha no RPA de Revisão: {motivo}")
                
                elif tipo_card == "nfs":
                    logger.info(f"[Worker Emissão] [LT {numero_lt}] É uma Nota de Serviço (NFS). Finalizando.")
                    colunas_update.extend(["Status de emissão", "CTE", "Data Revisão"])
                    valores_update.extend(["Nota de Serviço", "Nota de Serviço", data_agora])

                cards_tab = page.get_by_role("tab", name="Cards")
                cards_tab.scroll_into_view_if_needed()
                cards_tab.click(force=True)
                page.wait_for_function('document.querySelector("[role=tab][aria-selected=true]")?.textContent.includes("Cards")')

            elif status_card in ["liberado", "inconsistente", "ag._emissão"]:
                
                if not cte_preenchido:
                    if analise["status_cte"] == "autorizado":
                        logger.info(f"[Worker Emissão] [LT {numero_lt}] Status CT-e 'Autorizado'. Extraindo dados...")
                        with TimeoutDetector("Preencher CT-e", max_seconds=30, job_id=numero_lt):
                            resultado_cte = preencher_cte(page, card, numero_lt)
                        
                        if resultado_cte["status"] == "sucesso":
                            cte_preenchido = True
                            colunas_update.extend(["CTE", "$ Transportado"])
                            valores_update.extend([resultado_cte["numeros_ctes"], resultado_cte["valor_total"]])
                        
                        elif resultado_cte["status"] == "sem_dados":
                            motivo = "Status 'Autorizado' clicado, mas nenhum CT-e extraído."
                            logger.warning(f"[Worker Emissão] [LT {numero_lt}] {motivo}")
                        
                        elif resultado_cte["status"] == "falha_rpa":
                            motivo = resultado_cte["motivo"]
                            logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha RPA (preencher_cte): {motivo}")


                    elif analise["status_cte"] == "rejeitado":
                        logger.warning(f"[Worker Emissão] [LT {numero_lt}] CT-e 'Rejeitado'. Marcando como erro.")
                        colunas_update.append("Status de emissão")
                        valores_update.append("Arquivo c/ Erro")
                        cte_preenchido = True
                    else:
                        logger.info(f"[Worker Emissão] [LT {numero_lt}] Status CT-e: {analise['status_cte']} (Aguardando).")

                if not mdfe_preenchido:
                    if status_mdfe == "autorizado":
                        logger.info(f"[Worker Emissão] [LT {numero_lt}] Status MDF-e 'Autorizado'. Extraindo dados...")
                        with TimeoutDetector("Preencher MDF-e", max_seconds=30, job_id=numero_lt):
                            resultado_mdfe = preencher_mdfe(page, card, numero_lt)
                        
                        if resultado_mdfe["status"] == "sucesso":
                            mdfe_preenchido = True
                            colunas_update.extend(["MDFe", "Chave"])
                            valores_update.extend([resultado_mdfe["numeros_mdfes"], resultado_mdfe["chaves"]])
                        
                        elif resultado_mdfe["status"] == "falha_rpa":
                            motivo = resultado_mdfe["motivo"]
                            logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha RPA (preencher_mdfe): {motivo}")
                        else:
                            logger.info(f"[Worker Emissão] [LT {numero_lt}] Resultado preencher_mdfe: {resultado_mdfe['status']}")

                    elif status_mdfe == "-" or status_transporte in ["ENTREGA FINALIZADA", "AGUARDANDO DESCARGA"]:
                        logger.info(f"[Worker Emissão] [LT {numero_lt}] MDF-e não é necessário (Status: {status_transporte} ou '-').")
                        mdfe_preenchido = True
                    else:
                         logger.info(f"[Worker Emissão] [LT {numero_lt}] Status MDF-e: {status_mdfe} (Aguardando).")

                if cte_preenchido and mdfe_preenchido:
                    logger.success(f"[Worker Emissão] [LT {numero_lt}] Ambos CT-e e MDF-e preenchidos. Finalizando job.")
                    colunas_update.append("Status de emissão")
                    valores_update.append("Finalizado")

            else:
                motivo = f"Status do card não tratado: '{status_card}'"
                logger.warning(f"[Worker Emissão] [LT {numero_lt}] {motivo}")

            if len(colunas_update) > 1:
                enviar_job_update(r, config, linha_num, colunas_update, valores_update)
            else:
                logger.info(f"[Worker Emissão] [LT {numero_lt}] Nenhuma atualização necessária neste ciclo.")

        except Exception as e:
            logger.exception(f"[Worker Emissão] Erro ao processar LT {numero_lt} (Linha {linha_num}). Tentando recarregar a página e continuar.")
            
            try:
                page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded")
            except Exception as reload_err:
                logger.error(f"[Worker Emissão] Falha ao recarregar página: {reload_err}")
                try:
                    page.goto("https://portal.emiteai.com.br/#/emissor", timeout=PAGE_RELOAD_TIMEOUT)
                except Exception as goto_err:
                    logger.error(f"[Worker Emissão] Falha crítica ao navegar: {goto_err}")
            continue
        finally:
            if watchdog and numero_lt:
                watchdog.finalizar_job(numero_lt)
            try:
                logger.debug(f"[Worker Emissão] [LT {numero_lt}] Processamento finalizado. Removendo cadeado do '{s_controle}'.")
                r.srem(s_controle, id)
            except Exception as e_redis:
                logger.error(f"[Worker Emissão] [LT {numero_lt}] FALHA CRÍTICA ao remover cadeado do '{s_controle}': {e_redis}")
        
    logger.info(f"[Worker Emissão] Encerrado.")