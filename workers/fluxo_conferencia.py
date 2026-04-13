import redis
import json
import datetime
from loguru import logger
from playwright.sync_api import Page
from dados.dataclass import Carga
from fluxos.conferir import conferir_lt
from utils.fluxo_utils import obter_status_lt, garantir_pagina_consulta
from utils.filtros import filtro_cargas
from utils.watchdog import TimeoutDetector
from utils.timeouts import get_page_reload_timeout

from workers.base import BaseWorker
from utils.config import Config

PAGE_RELOAD_TIMEOUT = get_page_reload_timeout()


URL_CONSULTA = "https://portal.emiteai.com.br/#/ecommerce/shopee/consulta"
SELETOR_CHAVE_CONSULTA = 'button:has-text("Filtrar")'


# --- FUNÇÕES HELPER DE ENVIO DE RESULTADO ---
def enviar_job_update(r_client: redis.Redis, config: dict, row: int, colunas: list, valores: list):
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
        logger.debug(f"[Worker Conferência] Job UPDATE (Linha {row}) enviado ao Writer: {colunas} = {valores}")
    except Exception as e:
        logger.error(f"[Worker Conferência] Falha ao enviar job UPDATE (Linha {row}) para o Redis: {e}")


def enviar_job_append_erro(r_client: redis.Redis, config: dict, numero_lt: str, campo: str, valor: str):
    """Envia um job de ADIÇÃO DE ERRO para a fila do Writer."""
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
        logger.debug(f"[Worker Conferência] Job APPEND (LT {numero_lt}) enviado ao Writer: {campo} -> {valor}")
    except Exception as e:
        logger.error(f"[Worker Conferência] Falha ao enviar job APPEND (LT {numero_lt}) para o Redis: {e}")


class ConferenciaWorker(BaseWorker):
    """Worker para processamento de conferência de cargas."""
    
    def __init__(self, config: dict, nome_worker: str, page: Page):
        super().__init__(config, nome_worker)
        self.page = page
    
    @property
    def queue_name(self) -> str:
        return Config().conference_queue
    
    @property
    def control_set(self) -> str:
        return Config().control_set
    
    @property
    def tipo_job(self) -> str:
        return "conferencia"
    
    def _processar_job(self, data: dict, row: int):
        """Lógica específica de conferência."""
        
        # Validar página antes de processar
        pagina_esta_ok = garantir_pagina_consulta(
            page=self.page,
            url_alvo=URL_CONSULTA,
            seletor_chave=SELETOR_CHAVE_CONSULTA
        )
        if not pagina_esta_ok:
            logger.warning("[Worker Conferência] Página inacessível. Re-adicionando job à fila.")
            self.redis.rpush(self.queue_name, json.dumps({"row": row, "data": data}))
            return
        
        numero_lt = (data.get("N° Carga") or "").strip()
        id_job = (data.get("ID 3ZX") or "").strip() or f"{numero_lt}-{row}"
        
        # Recarregar página
        try:
            with TimeoutDetector("Recarregar página", max_seconds=20, job_id=numero_lt):
                self.page.reload(wait_until="domcontentloaded", timeout=PAGE_RELOAD_TIMEOUT)
        except Exception as reload_err:
            logger.error(f"[Worker Conferência] Falha ao recarregar página: {reload_err}")
            try:
                with TimeoutDetector("Navegar para consulta", max_seconds=20, job_id=numero_lt):
                    self.page.goto(URL_CONSULTA, timeout=PAGE_RELOAD_TIMEOUT)
            except Exception:
                logger.error(f"[Worker Conferência] Falha ao navegar para consulta")
                return
        
        carga = Carga.from_row(data)
        data_agora = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        if not carga:
            logger.warning(f"[Worker Conferência] LT {numero_lt} (Linha {row}) pulada: dados inválidos.")
            return

        if carga.status_emissao != "Pendente":
            logger.warning(f"[Worker Conferência] LT {numero_lt}: Status não é 'Pendente' ({carga.status_emissao}). Pulando.")
            return

        if not carga.numero_lt:
            logger.warning(f"[Worker Conferência] Linha {row} pulada: sem número de carga.")
            return

        status_validos = ["ENTREGA FINALIZADA", "EM TRANSITO", "AGUARDANDO DESCARGA"]
        if carga.status not in status_validos:
            logger.info(f"[Worker Conferência] LT {numero_lt}: Status '{carga.status}' não requer conferência.")
            return

        # === LÓGICA PRINCIPAL ===
        logger.info(f"[Worker Conferência] ▶️  Iniciando RPA para LT {numero_lt} (Linha {row})")
        
        logger.info(f"[Worker Conferência] [LT {numero_lt}] 📋 Passo 1/3: Aplicando filtro...")
        filtro_cargas(self.page, carga.numero_lt)
        logger.info(f"[Worker Conferência] [LT {numero_lt}] ✅ Filtro aplicado!")
        
        logger.info(f"[Worker Conferência] [LT {numero_lt}] 🔍 Passo 2/3: Obtendo status...")
        status_emiteai = obter_status_lt(self.page, carga.numero_lt)
        logger.info(f"[Worker Conferência] [LT {numero_lt}] ✅ Status obtido: {status_emiteai}")
        
        colunas_update = ["Data Conferência", "Status EmiteAI (coletado)"]
        valores_update = [data_agora, status_emiteai]

        if status_emiteai == "Aguardando Conferência":
            logger.info(f"[Worker Conferência] [LT {numero_lt}] 📝 Passo 3/3: Executando conferência...")
            resultado_rpa = conferir_lt(self.page, carga)
            logger.info(f"[Worker Conferência] [LT {numero_lt}] ✅ Conferência finalizada: {resultado_rpa.get('status')}")
            
            if resultado_rpa["status"] == "sucesso":
                logger.success(f"[Worker Conferência] LT {numero_lt} (Linha {row}) SUCESSO.")
                colunas_update.append("Status de emissão")
                valores_update.append("Verificar Emissão")
            
            elif resultado_rpa["status"] == "falha_cadastro":
                campo_falha = resultado_rpa["campo"]
                valor_falha = resultado_rpa["valor"]
                logger.error(f"[Worker Conferência] LT {numero_lt} FALHOU (Cadastro): {campo_falha} - {valor_falha}")
                enviar_job_append_erro(self.redis, self.config, numero_lt, campo_falha, valor_falha)
            
            elif resultado_rpa["status"] == "falha_rpa":
                motivo_falha = resultado_rpa.get("motivo") or f"{resultado_rpa.get('campo', 'Erro')}: {resultado_rpa.get('valor', 'Desconhecido')}"
                logger.error(f"[Worker Conferência] LT {numero_lt} FALHOU (RPA): {motivo_falha}")

        elif status_emiteai == "Carga Finalizada" or status_emiteai == "Aguardando Emissão":
            colunas_update.append("Status de emissão")
            valores_update.append("Verificar Emissão")
        
        elif status_emiteai == "não encontrado":
            colunas_update.append("Status de emissão")
            valores_update.append("Arquivo c/ Erro")

        else:
            logger.warning(f"[Worker Conferência] LT {numero_lt}: Status '{status_emiteai}' não tratado.")

        # Enviar resultado
        enviar_job_update(self.redis, self.config, row, colunas_update, valores_update)


# === FUNÇÃO DE ENTRADA (mantida para compatibilidade) ===
def fluxo_conferencia_worker(page: Page, config: dict):
    """Função de entrada - mantida para compatibilidade com o main.py"""
    worker = ConferenciaWorker(config, "conferencia", page)
    worker.executar()
