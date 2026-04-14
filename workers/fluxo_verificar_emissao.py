import pandas as pd
import json
import datetime
from loguru import logger
from playwright.sync_api import Page
from utils.fluxo_utils import goto_cards, analisar_status_emissao, identificar_tipo_card
from utils.filtros import filtro_cards
from fluxos.revisar import revisar_lt
from fluxos.preencher_cte import preencher_cte
from fluxos.preencher_mdfe import preencher_mdfe
from utils.watchdog import TimeoutDetector
from utils.timeouts import get_page_reload_timeout
from utils.config import Config

from workers.base import BaseWorker

PAGE_RELOAD_TIMEOUT = get_page_reload_timeout()


def enviar_job_update(r_client, config: dict, row: int, colunas: list, valores: list):
    """Envia um job de ATUALIZAÇÃO para a fila do Writer."""
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
        logger.debug(f"[Worker Emissão] Job UPDATE (Linha {row}) enviado ao Writer: {colunas} = {valores}")
    except Exception as e:
        logger.error(f"[Worker Emissão] Falha ao enviar job UPDATE (Linha {row}) para o Redis: {e}")


class EmissaoWorker(BaseWorker):
    """Worker para verificação e emissão de CT-e/MDF-e."""
    
    def __init__(self, config: dict, nome_worker: str, page: Page):
        super().__init__(config, nome_worker)
        self.page = page
    
    @property
    def queue_name(self) -> str:
        return Config().emission_queue
    
    @property
    def control_set(self) -> str:
        return Config().control_set
    
    @property
    def tipo_job(self) -> str:
        return "emissao"
    
    def _processar_job(self, data: dict, row: int):
        """Lógica específica de emissão."""
        numero_lt = (data.get("N° Carga") or "").strip()
        cte_valor = (data.get("CTE") or "").strip()
        mdfe_valor = (data.get("MDFe") or "").strip()
        status_transporte = (data.get("Status") or "").strip()
        id = (data.get("ID 3ZX") or "").strip() or f"{numero_lt}-{row}"
        
        if not numero_lt:
            logger.warning(f"[Worker Emissão] Linha {row} pulada: sem 'N° Carga'")
            return
        
        cte_preenchido = pd.notna(cte_valor) and str(cte_valor).strip() != ""
        mdfe_preenchido = pd.notna(mdfe_valor) and str(mdfe_valor).strip() != ""
        data_agora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        # Se já está tudo preenchido, marca como Finalizado
        if cte_preenchido and mdfe_preenchido:
            logger.info(f"[Worker Emissão] LT {numero_lt}: CT-e e MDF-e já preenchidos.")
            if str(cte_valor).strip() in ["NFS", "Nota de Serviço"]:
                enviar_job_update(self.redis, self.config, row, ["Status de emissão"], ["Nota de Serviço"])
            else:
                enviar_job_update(self.redis, self.config, row, ["Status de emitido"], ["Finalizado"])
            return

        logger.info(f"[Worker Emissão] Iniciando RPA para LT: {numero_lt} (Linha {row})")
        
        from utils.manifesto_utils import navegar_e_validar_mdfe
        resultado = navegar_e_validar_mdfe(self.page, numero_lt)
        if not resultado:
            logger.error(f"[Worker Emissão] Não foi possível encontrar card para LT {numero_lt}.")
            return

        card = resultado.get("card")
        analise = resultado.get("analise")
        status_card = analise.get("status_card") if analise else None
        status_mdfe = resultado.get("status_mdfe")

        if not card or not status_card:
            logger.error(f"[Worker Emissão] Card ou status não encontrado para LT {numero_lt}.")
            return

        colunas_update = []
        valores_update = []

        # Processamento por status
        if status_card == "ag._revisão":
            tipo_card = identificar_tipo_card(card)
            
            if tipo_card == "cte":
                logger.info(f"[Worker Emissão] [LT {numero_lt}] Status 'ag._revisão' (CTE). Executando revisão...")
                with TimeoutDetector("Revisar LT", max_seconds=30, job_id=numero_lt):
                    resultado_rpa = revisar_lt(self.page, numero_lt)
                
                if resultado_rpa["status"] == "sucesso":
                    logger.success(f"[Worker Emissão] [LT {numero_lt}] Revisão concluída.")
                else:
                    logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha na revisão: {resultado_rpa.get('motivo')}")
            
            elif tipo_card == "nfs":
                logger.info(f"[Worker Emissão] [LT {numero_lt}] É uma Nota de Serviço (NFS).")
                colunas_update.extend(["Status de emissão", "CTE"])
                valores_update.extend(["Nota de Serviço", "Nota de Serviço"])

        elif status_card in ["liberado", "inconsistente", "ag._emissão"]:
            # Processa CT-e
            if not cte_preenchido:
                if analise["status_cte"] == "autorizado":
                    logger.info(f"[Worker Emissão] [LT {numero_lt}] CT-e 'Autorizado'. Extraindo...")
                    with TimeoutDetector("Preencher CT-e", max_seconds=30, job_id=numero_lt):
                        resultado_cte = preencher_cte(self.page, card, numero_lt)
                    
                    if resultado_cte["status"] == "sucesso":
                        cte_preenchido = True
                        colunas_update.extend(["CTE", "$ Transportado"])
                        valores_update.extend([resultado_cte["numeros_ctes"], resultado_cte["valor_total"]])
                    elif resultado_cte["status"] == "sem_dados":
                        logger.warning(f"[Worker Emissão] [LT {numero_lt}] Nenhum CT-e extraído.")
                    elif resultado_cte["status"] == "falha_rpa":
                        logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha RPA: {resultado_cte.get('motivo')}")
                
                elif analise["status_cte"] == "rejeitado":
                    logger.warning(f"[Worker Emissão] [LT {numero_lt}] CT-e 'Rejeitado'.")
                    colunas_update.append("Status de emissão")
                    valores_update.append("Arquivo c/ Erro")
                    cte_preenchido = True

            # Processa MDF-e
            if not mdfe_preenchido:
                if status_mdfe == "autorizado":
                    logger.info(f"[Worker Emissão] [LT {numero_lt}] MDF-e 'Autorizado'. Extraindo...")
                    with TimeoutDetector("Preencher MDF-e", max_seconds=30, job_id=numero_lt):
                        resultado_mdfe = preencher_mdfe(self.page, card, numero_lt)
                    
                    if resultado_mdfe["status"] == "sucesso":
                        mdfe_preenchido = True
                        colunas_update.extend(["MDFe", "Chave"])
                        valores_update.extend([resultado_mdfe["numeros_mdfes"], resultado_mdfe["chaves"]])
                    elif resultado_mdfe["status"] == "falha_rpa":
                        logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha RPA MDFe: {resultado_mdfe.get('motivo')}")
                
                elif status_mdfe == "-" or status_transporte in ["ENTREGA FINALIZADA", "AGUARDANDO DESCARGA"]:
                    logger.info(f"[Worker Emissão] [LT {numero_lt}] MDF-e não necessário.")
                    mdfe_preenchido = True

            # Finaliza se tudo OK
            if cte_preenchido and mdfe_preenchido:
                logger.success(f"[Worker Emissão] [LT {numero_lt}] Ambos preenchidos.")
                colunas_update.append("Status de emissão")
                valores_update.append("Finalizado")

        else:
            logger.warning(f"[Worker Emissão] [LT {numero_lt}] Status '{status_card}' não tratado.")

        if len(colunas_update) > 1:
            enviar_job_update(self.redis, self.config, row, colunas_update, valores_update)
        else:
            logger.info(f"[Worker Emissão] [LT {numero_lt}] Nenhuma atualização necessária.")


# === FUNÇÃO DE ENTRADA (compatibilidade) ===
def fluxo_verificar_emissao_worker(page: Page, config: dict):
    """Função de entrada - mantida para compatibilidade com o main.py"""
    worker = EmissaoWorker(config, "emissao", page)
    worker.executar()