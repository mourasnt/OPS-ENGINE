
from datetime import datetime
import re

import redis
import json
import time
import os
from loguru import logger
from playwright.sync_api import Page

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

def fluxo_encerrar_manifesto_worker(page: Page, config: dict):
    import threading
    worker_name = threading.current_thread().name
    logger.info(f"[Worker Manifesto] Iniciando... (Thread: {worker_name})")

    redis_cfg = config.get('redis_settings', {})
    r_host = os.environ.get('REDIS_HOST')
    r_port = int(os.environ.get('REDIS_PORT'))
    r_db_filas = redis_cfg.get('db_filas')
    q_manifesto = redis_cfg.get('manifesto_queue')
    s_manifesto = redis_cfg.get('manifesto_set')
    if not q_manifesto or not s_manifesto:
        logger.critical(f"[Worker Manifesto] Configuração de fila/set de manifesto ausente. Worker encerrando.")
        return

    try:
        from utils.redis_client import get_redis
        r = get_redis(host=r_host, port=r_port, db=r_db_filas)
        logger.info(f"[Worker Manifesto] Conectado ao Redis em {r_host}:{r_port}. Ouvindo a fila '{q_manifesto}'")
    except Exception as e:
        logger.critical(f"[Worker Manifesto] Não foi possível conectar ao Redis: {e}. Worker encerrando.")
        return

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
    manifesto_id = None

    while True:
        if verificar_deve_morrer():
            logger.warning(f"[Worker Manifesto] 💀 Downscaling detectado. Thread será encerrada.")
            break

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

            if watchdog:
                watchdog.registrar_job(manifesto_id, worker_id=worker_name, tipo_job="manifesto")

            from utils.manifesto_utils import navegar_e_validar_mdfe
            resultado = navegar_e_validar_mdfe(page, lt)
            if not resultado:
                logger.error(f"[Worker Manifesto] Não foi possível navegar ou validar status do MDFe {mdfe}.")
                enviar_job_append_erro(r, config, mdfe, "Erro Navegação/Validação", "Card ou status não encontrado")
                continue

            status_mdfe = resultado.get("status_mdfe")
            if status_mdfe == "encerrado":
                logger.warning(f"[Worker Manifesto] MDFe {mdfe} está encerrado. Atualizando planilha.")
                enviar_job_update(r, config, linha_num, ["MDF-e Baixado ?"], ["SIM"])
                continue
            elif status_mdfe != "autorizado":
                logger.warning(f"[Worker Manifesto] MDFe {mdfe} não está autorizado e nem encerrado (Status: {status_mdfe}). Pulando job.")
                continue

            try:
                logger.info(f"[Worker Manifesto] (PLACEHOLDER) Encerramento de manifesto para MDFe {mdfe} - implementar Playwright depois.")
                card_locator = page.locator(".MuiGrid-root.MuiGrid-item.MuiGrid-grid-xs-12.MuiGrid-grid-sm-6").filter(
                    has_text=re.compile(rf"DT:\s*{re.escape(lt)}")
                )
                card = card_locator.first
            except Exception as e:
                logger.error(f"[Worker Manifesto] Erro ao localizar card para LT {lt}: {e}")

            card.locator("span", has_text=re.compile(r"^\s*MDF-e\s*$")).first.locator("xpath=../..").locator("button").first.click()

            page.get_by_role("checkbox").first.check()
            page.get_by_text("Opções").first.click()
            


            try:
                opcao_encerrar = page.get_by_role("menuitem", name="Encerrar").first

                opcao_encerrar.wait_for(state="visible", timeout=3000) 

                opcao_encerrar.click()
                logger.info("Clicou na opção 'Encerrar' com sucesso!")

            except Exception as e:
                motivo = "A opção 'Encerrar' não está disponível neste menu ou não carregou a tempo."
                logger.error(f"[MDF-e Encerrar] [LT {lt}] {motivo}")

            try:
                agora = datetime.now()
                data_encerramento = agora.strftime('%d/%m/%Y %H:%M')
                
                campo_data = page.locator('input[name="dataEncerramento"]')

                campo_data.wait_for(state="visible", timeout=5000)
                
                campo_data.click()
                campo_data.fill(data_encerramento)
                time.sleep(2)

                page.get_by_text("Confirmar").first.click()
                time.sleep(2)
                
                modal_confirmacao = page.locator('div[role="dialog"]').filter(has_text="Confirmação de encerramento MDF-e")

                botao_final = modal_confirmacao.get_by_role("button", name="Confirmar")
                botao_final.wait_for(state="visible", timeout=5000)
                botao_final.click()

                alerta = page.locator(".MuiAlert-message").first
                texto_alerta = alerta.inner_text(timeout=10000)

                if "sucesso" in texto_alerta.lower() or "emitido" in texto_alerta.lower():
                    logger.success(f"[MDF-e Encerrar] [LT {lt}] Encerramento realizado: {texto_alerta}")
                    enviar_job_update(r, config, linha_num, ["MDF-e Baixado ?"], ["SIM"])
                else:
                    logger.warning(f"[MDF-e Encerrar] [LT {lt}] Alerta sem texto de sucesso: {texto_alerta}")

            except TimeoutError:
                motivo = "Timeout aguardando campos ou alerta de confirmação."
                logger.error(f"[MDF-e Encerrar] [LT {lt}] {motivo}")
            except Exception as e:
                motivo = f"Erro no modal de emissão: {e}"
                logger.error(f"[MDF-e Encerrar] [LT {lt}] {motivo}")     

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
            if watchdog and job_atual:
                watchdog.finalizar_job(job_atual)
            if manifesto_id:
                try:
                    logger.debug(f"[Worker Manifesto] [{manifesto_id}] Processamento finalizado. Removendo cadeado do '{s_manifesto}'.")
                    r.srem(s_manifesto, manifesto_id)
                except Exception as e_redis:
                    logger.error(f"[Worker Manifesto] [{manifesto_id}] FALHA CRÍTICA ao remover cadeado do '{s_manifesto}': {e_redis}")
            else:
                logger.debug("[Worker Manifesto] manifesto_id não definido no finally; pulando remoção de cadeado.")

    logger.info(f"[Worker Manifesto] Encerrado.")
