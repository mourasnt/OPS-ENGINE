
import redis
import json
import time
import os
from loguru import logger
from playwright.sync_api import Page

# --- HELPERS DE UPDATE E ERRO ---
def enviar_job_update(r_client: redis.Redis, config: dict, row: int, colunas: list, valores: list):
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
        logger.debug(f"[Worker Manifesto] Job UPDATE (Linha {row}) enviado ao Writer: {colunas} = {valores}")
    except Exception as e:
        logger.error(f"[Worker Manifesto] Falha ao enviar job UPDATE (Linha {row}) para o Redis: {e}")

def enviar_job_append_erro(r_client: redis.Redis, config: dict, mdfe: str, campo: str, valor: str):
    try:
        results_queue = config['redis_settings']['results_queue']
        dados_linha_erro = [campo, valor]
        payload = {
            "tipo_job": "APPEND_ERROR_LOG",
            "payload": {
                "dados_linha": dados_linha_erro
            }
        }
        r_client.rpush(results_queue, json.dumps(payload))
        logger.debug(f"[Worker Manifesto] Job APPEND (MDFe {mdfe}) enviado ao Writer: {campo} -> {valor}")
    except Exception as e:
        logger.error(f"[Worker Manifesto] Falha ao enviar job APPEND (MDFe {mdfe}) para o Redis: {e}")

# --- WORKER DE ENCERRAMENTO DE MANIFESTO (MDFe) ---
def fluxo_encerrar_manifesto_worker(page: Page, config: dict):
    import threading
    worker_name = threading.current_thread().name
    logger.info(f"[Worker Manifesto] Iniciando... (Thread: {worker_name})")

    redis_cfg = config.get('redis_settings', {})
    r_host = redis_cfg.get('host')
    r_port = redis_cfg.get('port')
    r_db = redis_cfg.get('db')
    q_manifesto = redis_cfg.get('manifesto_queue')
    s_manifesto = redis_cfg.get('manifesto_set')
    if not q_manifesto or not s_manifesto:
        logger.critical(f"[Worker Manifesto] Configuração de fila/set de manifesto ausente. Worker encerrando.")
        return

    try:
        from utils.redis_client import get_redis
        r = get_redis(host=r_host, port=r_port, db=r_db)
        logger.info(f"[Worker Manifesto] Conectado ao Redis em {r_host}:{r_port}. Ouvindo a fila '{q_manifesto}'")
    except Exception as e:
        logger.critical(f"[Worker Manifesto] Não foi possível conectar ao Redis: {e}. Worker encerrando.")
        return

    # Watchdog e Pool Manager
    watchdog = config.get('watchdog', None)
    pool_manager = config.get('thread_pool_manager', None)

    def verificar_deve_morrer() -> bool:
        try:
            if pool_manager:
                return pool_manager.thread_deve_morrer("manifesto")
        except Exception as e:
            logger.error(f"[Worker Manifesto] Erro ao verificar downscaling: {e}")
        return False

    def verificar_kill_signal(job_id_atual: str) -> bool:
        try:
            kill_signals = r.smembers("watchdog:kill_workers")
            for signal_json in kill_signals:
                try:
                    signal = json.loads(signal_json)
                    if signal.get("job_id") == job_id_atual:
                        r.srem("watchdog:kill_workers", signal_json)
                        logger.warning(f"[Worker Manifesto] 💀 Kill signal detectado para job '{job_id_atual}'!")
                        return True
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.error(f"[Worker Manifesto] Erro ao verificar kill signal: {e}")
        return False

    tentativas_reconexao = 0
    max_tentativas_reconexao = 3
    job_atual = None

    while True:
        # Downscaling
        if verificar_deve_morrer():
            logger.warning(f"[Worker Manifesto] 💀 Downscaling detectado. Thread será encerrada.")
            break

        # Kill signal
        if job_atual and verificar_kill_signal(job_atual):
            logger.critical(f"[Worker Manifesto] Encerrando thread por kill signal do Watchdog!")
            break

        try:
            resultado_bruto = r.blpop([q_manifesto], timeout=60)
            if resultado_bruto is None:
                logger.debug(f"[Worker Manifesto] Nenhum job recebido. Reiniciando loop.")
                continue

            _, job_json = resultado_bruto
            job = json.loads(job_json)
            linha_data = job['data']
            linha_num = job['row']
            mdfe = (linha_data.get('MDFe') or '').strip()
            lt = (linha_data.get('N° Carga') or '').strip()
            manifesto_id = f"{mdfe}-{lt}"
            job_atual = manifesto_id
            logger.info(f"[Worker Manifesto] Job recebido: MDFe {mdfe} (Linha {linha_num}). Processando...")

            # Registrar job no watchdog
            if watchdog:
                watchdog.registrar_job(manifesto_id, worker_id=worker_name, tipo_job="manifesto")

            # --- Lógica principal do job ---
            from utils.manifesto_utils import navegar_e_validar_mdfe
            resultado = navegar_e_validar_mdfe(page, mdfe)
            if not resultado:
                logger.error(f"[Worker Manifesto] Não foi possível navegar ou validar status do MDFe {mdfe}.")
                enviar_job_append_erro(r, config, mdfe, "Erro Navegação/Validação", "Card ou status não encontrado")
                continue

            status_mdfe = resultado.get("status_mdfe")
            if status_mdfe != "autorizado":
                logger.warning(f"[Worker Manifesto] MDFe {mdfe} não está autorizado (Status: {status_mdfe}). Pulando job.")
                enviar_job_update(r, config, linha_num, ["Status de emissão"], [f"Manifesto não autorizado ({status_mdfe})"])
                continue

            # --- Lógica Playwright de encerramento (placeholder) ---
            logger.info(f"[Worker Manifesto] (PLACEHOLDER) Encerramento de manifesto para MDFe {mdfe} - implementar Playwright depois.")

            # Exemplo de update após sucesso
            enviar_job_update(r, config, linha_num, ["Status de emissão"], ["Manifesto Encerrado"])

            tentativas_reconexao = 0

        except redis.exceptions.ConnectionError as e:
            tentativas_reconexao += 1
            logger.error(f"[Worker Manifesto] Erro de conexão Redis ({tentativas_reconexao}/{max_tentativas_reconexao}): {e}")
            if tentativas_reconexao >= max_tentativas_reconexao:
                logger.critical("[Worker Manifesto] Máximo de tentativas de reconexão atingido. Worker encerrando.")
                break
            time.sleep(10)
            continue
        except Exception as e:
            logger.error(f"[Worker Manifesto] Erro ao processar job: {e}")
            enviar_job_append_erro(r, config, mdfe, "Erro Encerramento Manifesto", str(e))
            time.sleep(5)
            continue
        finally:
            # Finalizar job no watchdog
            if watchdog and job_atual:
                watchdog.finalizar_job(job_atual)
            try:
                logger.debug(f"[Worker Manifesto] [{manifesto_id}] Processamento finalizado. Removendo cadeado do '{s_manifesto}'.")
                r.srem(s_manifesto, manifesto_id)
            except Exception as e_redis:
                logger.error(f"[Worker Manifesto] [{manifesto_id}] FALHA CRÍTICA ao remover cadeado do '{s_manifesto}': {e_redis}")

    logger.info(f"[Worker Manifesto] Encerrado.")
