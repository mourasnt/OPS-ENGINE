import datetime
import json
import os
import time
from loguru import logger
from playwright.sync_api import Page
from utils.config import Config

from workers.base import BaseWorker


def _capturar_debug(page, prefixo, numero_lt):
    debug_dir = os.path.join(os.getcwd(), "debug_screenshots")
    os.makedirs(debug_dir, exist_ok=True)
    screenshot_path = os.path.join(debug_dir, f"{prefixo}_{numero_lt}.png")
    try:
        page.screenshot(path=screenshot_path)
        logger.debug(f"[DEBUG] Screenshot salvo: {screenshot_path}")
    except Exception as e:
        logger.debug(f"[DEBUG] Falha ao capturar screenshot: {e}")


def enviar_job_update(r_client, config: dict, row: int, colunas: list, valores: list):
    try:
        results_queue = Config().results_queue
        payload = {
            "tipo_job": "UPDATE_SHEET",
            "payload": {
                "row": row,
                "colunas": colunas,
                "novos_valores": valores
            }
        }
        r_client.rpush(results_queue, json.dumps(payload))
        logger.debug(f"[Worker Manifesto] Job UPDATE (Linha {row}) enviado: {colunas} = {valores}")
    except Exception as e:
        logger.error(f"[Worker Manifesto] Falha ao enviar job UPDATE: {e}")


def enviar_job_append_erro(r_client, config: dict, mdfe: str, campo: str, valor: str):
    try:
        results_queue = Config().results_queue
        dados_linha_erro = [campo, valor]
        payload = {
            "tipo_job": "APPEND_ERROR_LOG",
            "payload": {
                "dados_linha": dados_linha_erro
            }
        }
        r_client.rpush(results_queue, json.dumps(payload))
        logger.debug(f"[Worker Manifesto] Job APPEND (MDFe {mdfe}): {campo} -> {valor}")
    except Exception as e:
        logger.error(f"[Worker Manifesto] Falha ao enviar job APPEND: {e}")


class ManifestoWorker(BaseWorker):
    """Worker para encerramento de MDF-e."""
    
    def __init__(self, config: dict, nome_worker: str, page: Page):
        super().__init__(config, nome_worker)
        self.page = page
    
    @property
    def queue_name(self) -> str:
        return Config().manifesto_queue
    
    @property
    def control_set(self) -> str:
        return Config().manifesto_set
    
    @property
    def tipo_job(self) -> str:
        return "manifesto"
    
    def _processar_job(self, data: dict, row: int):
        """Lógica específica de encerramento de manifesto."""
        mdfe = (data.get('MDFe') or '').strip()
        lt = (data.get('N° Carga') or '').strip()
        manifesto_id = f"{mdfe}-{lt}"

        logger.info(f"[Worker Manifesto] Processando MDFe {mdfe} (Linha {row}) - LT: {lt}")

        try:
            _capturar_debug(self.page, "inicio_worker", lt)
        except Exception:
            pass

        from utils.manifesto_utils import navegar_e_validar_mdfe
        logger.debug(f"[Worker Manifesto] Chamando navegar_e_validar_mdfe para LT: {lt}")
        resultado = navegar_e_validar_mdfe(self.page, lt)

        if not resultado:
            logger.error(f"[Worker Manifesto] Não foi possível navegar/validar MDFe {mdfe}.")
            logger.debug(f"[Worker Manifesto] URL no erro: {self.page.url}")

            try:
                _capturar_debug(self.page, "erro_navegar_validar", lt)
            except Exception:
                pass

            enviar_job_append_erro(self.redis, self.config, mdfe, "Erro Navegação/Validação", "Card não encontrado")
            return

        status_mdfe = resultado.get("status_mdfe")
        logger.debug(f"[Worker Manifesto] Status MDFe: {status_mdfe}")

        if status_mdfe == "encerrado":
            logger.warning(f"[Worker Manifesto] MDFe {mdfe} já encerrado.")
            enviar_job_update(self.redis, self.config, row, ["MDF-e Baixado ?"], ["SIM"])
            return
        elif status_mdfe != "autorizado":
            logger.warning(f"[Worker Manifesto] MDFe {mdfe} não está autorizado (Status: {status_mdfe}). Pulando.")
            return

        # Encerramento do MDF-e
        try:
            import re
            card_locator = self.page.locator(".MuiGrid-root.MuiGrid-item.MuiGrid-grid-xs-12.MuiGrid-grid-sm-6").filter(
                has_text=re.compile(rf"DT:\s*{re.escape(lt)}")
            )
            card = card_locator.first
        except Exception as e:
            logger.error(f"[Worker Manifesto] Erro ao localizar card: {e}")

        try:
            card.locator("span", has_text=re.compile(r"^\s*MDF-e\s*$")).first.locator("xpath=../..").locator("button").first.click()
            self.page.get_by_role("checkbox").first.check()
            self.page.get_by_text("Opções").first.click()
            
            opcao_encerrar = self.page.get_by_role("menuitem", name="Encerrar").first
            opcao_encerrar.wait_for(state="visible", timeout=3000)
            opcao_encerrar.click()
            
            agora = datetime.datetime.now()
            data_encerramento = agora.strftime('%d/%m/%Y %H:%M')
            
            campo_data = self.page.locator('input[name="dataEncerramento"]')
            campo_data.wait_for(state="visible", timeout=5000)
            campo_data.click()
            campo_data.fill(data_encerramento)
            time.sleep(2)
            
            self.page.get_by_text("Confirmar").first.click()
            time.sleep(2)
            
            modal_confirmacao = self.page.locator('div[role="dialog"]').filter(has_text="Confirmação de encerramento MDF-e")
            botao_final = modal_confirmacao.get_by_role("button", name="Confirmar")
            botao_final.wait_for(state="visible", timeout=5000)
            botao_final.click()
            
            alerta = self.page.locator(".MuiAlert-message").first
            texto_alerta = alerta.inner_text(timeout=10000)
            
            if "sucesso" in texto_alerta.lower() or "emitido" in texto_alerta.lower():
                logger.success(f"[Worker Manifesto] Encerramento realizado: {texto_alerta}")
                enviar_job_update(self.redis, self.config, row, ["MDF-e Baixado ?"], ["SIM"])
            else:
                logger.warning(f"[Worker Manifesto] Alerta sem texto de sucesso: {texto_alerta}")

        except TimeoutError:
            logger.error(f"[Worker Manifesto] [LT {lt}] Timeout aguardando campos ou alerta.")
        except Exception as e:
            logger.error(f"[Worker Manifesto] [LT {lt}] Erro no modal: {e}")


# === FUNÇÃO DE ENTRADA (compatibilidade) ===
def fluxo_encerrar_manifesto_worker(page: Page, config: dict):
    """Função de entrada - mantida para compatibilidade com o main.py"""
    worker = ManifestoWorker(config, "manifesto", page)
    worker.executar()