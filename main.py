import os
import threading
import time
import sys
import traceback
from typing import Dict, Any

from playwright.sync_api import sync_playwright
from loguru import logger

from utils.helpers import carregar_config 
from utils.redis_client import get_redis
from utils.thread_util import ThreadPoolManager
from utils.watchdog import JobWatchdog
from utils.status_display import StatusDisplay

from workers.fluxo_conferencia import fluxo_conferencia_worker
from workers.fluxo_verificar_emissao import fluxo_verificar_emissao_worker
from workers.fluxo_encerrar_manifesto import fluxo_encerrar_manifesto_worker
from workers.fluxo_sm import fluxo_sm_worker
from fluxos.fluxo_login import fluxo_login

# ===================================================================
# REGISTRO DE WORKERS (Adicione novos robôs apenas aqui)
# ===================================================================
WORKERS_REGISTRY = {
    "conferencia": {"func": fluxo_conferencia_worker, "tipo": "web"},
    "emissao": {"func": fluxo_verificar_emissao_worker, "tipo": "web"},
    "manifesto": {"func": fluxo_encerrar_manifesto_worker, "tipo": "web"},
    "gerenciamento_risco": {"func": fluxo_sm_worker, "tipo": "api"}
}

# ===================================================================
# CONFIGURAÇÃO DE LOGS
# ===================================================================
logger.remove()

def filtro_logs_importantes(record):
    level_name = record["level"].name
    return level_name in ["WARNING", "ERROR", "CRITICAL", "SUCCESS", "INFO"]

logger.add(
    sink=sys.stdout, 
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level="INFO",  
    filter=filtro_logs_importantes,
    enqueue=True
)
logger.add(
    "logs/main_rpa.log", 
    rotation="10 MB", 
    retention="5 days", 
    level="DEBUG",
    format="{time:DD-MM-YYYY HH:mm:ss} | {level:<7} | {file}:{line} | {message}",
    enqueue=True
)

USUARIO = os.environ.get('RPA_USUARIO')
SENHA = os.environ.get('RPA_SENHA')

if not USUARIO or not SENHA:
    logger.critical("Variáveis RPA_USUARIO/RPA_SENHA não configuradas. Defina-as no ambiente.")
    sys.exit(1)


# ===================================================================
# EXECUTORES BASE
# ===================================================================
def executar_fluxo(nome_fluxo: str, funcao_fluxo, config: Dict[str, Any]): 
    """Executa um worker em seu próprio contexto e instância do Playwright (WEB)."""
    context = None
    browser = None 
    
    with sync_playwright() as playwright:
        try:
            logger.info(f"[{nome_fluxo}] Iniciando thread e navegador...")
            browser = playwright.firefox.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://portal.emiteai.com.br/#/login")

            login_ok = False
            login_attempt = 0
            while not login_ok:
                login_attempt += 1
                if login_attempt > 1:
                    wait_time = min(60, 30 * (login_attempt - 1))
                    logger.info(f"[{nome_fluxo}] Aguardando {wait_time}s antes de tentar novamente...")
                    time.sleep(wait_time)
                    try:
                        page.goto("https://portal.emiteai.com.br/#/login", timeout=45000)
                    except Exception as nav_err:
                        logger.error(f"[{nome_fluxo}] Erro ao recarregar página: {nav_err}")
                
                logger.info(f"[{nome_fluxo}] Tentativa de login {login_attempt}...")
                login_ok = fluxo_login(page=page, usuario=USUARIO, senha=SENHA)
                if login_ok:
                    logger.success(f"[{nome_fluxo}] Login realizado com sucesso.")
                else:
                    logger.warning(f"[{nome_fluxo}] Login falhou na tentativa {login_attempt}. Retentando...")
            
            funcao_fluxo(page, config) 
            logger.info(f"[{nome_fluxo}] Loop de consumo encerrado.")

        except Exception as e:
            logger.critical(f"[{nome_fluxo}] Erro fatal: {e}\n{traceback.format_exc()}")
        finally:
            if context: context.close()
            if browser: browser.close()
            logger.info(f"[{nome_fluxo}] Thread finalizada e recursos liberados.")


def executar_fluxo_api(nome_fluxo: str, funcao_fluxo, config: Dict[str, Any]):
    """Executa um worker baseado puramente em API, sem inicializar navegadores."""
    try:
        logger.info(f"[{nome_fluxo}] Iniciando thread de API...")
        funcao_fluxo(config) 
        logger.info(f"[{nome_fluxo}] Loop de consumo encerrado.")
    except Exception as e:
        logger.critical(f"[{nome_fluxo}] Erro fatal: {e}\n{traceback.format_exc()}")
    finally:
        logger.info(f"[{nome_fluxo}] Thread finalizada.")


def executor_inteligente(nome_fluxo: str, arg2, arg3=None):
    """
    Roteador dinâmico: Decide qual executor base usar lendo o REGISTRY.
    Normaliza nomes vindos do ThreadPoolManager para encontrar a chave correta.
    """
    # Normalização: "conferencia_worker_1" ou "conferencia_worker_recovery_123" -> "conferencia"
    nome_normalizado = nome_fluxo.lower().strip()
    
    if "gerenciamento_risco" in nome_normalizado:
        nome_base = "gerenciamento_risco"
    elif "conferencia" in nome_normalizado:
        nome_base = "conferencia"
    elif "emissao" in nome_normalizado:
        nome_base = "emissao"
    elif "manifesto" in nome_normalizado:
        nome_base = "manifesto"
    else:
        nome_base = nome_fluxo.split("_")[0]

    worker_info = WORKERS_REGISTRY.get(nome_base)
    if not worker_info:
        logger.error(f"Tentativa de iniciar um fluxo desconhecido: {nome_fluxo} (Base: {nome_base})")
        return

    # Tratamento flexível de argumentos (Manual vs Manager)
    if callable(arg2):
        funcao_fluxo = arg2
        config = arg3
    else:
        config = arg2
        funcao_fluxo = worker_info["func"]

    if worker_info["tipo"] == "api":
        executar_fluxo_api(nome_fluxo, funcao_fluxo, config)
    else:
        executar_fluxo(nome_fluxo, funcao_fluxo, config)


# ===================================================================
# MAIN (Orquestrador central)
# ===================================================================
def main():
    config = carregar_config()
    if not config:
        logger.critical("Não foi possível carregar o config.json. Encerrando.")
        sys.exit(1)
    
    logger.info("Iniciando Orquestrador da OPS_ENGINE...")

    # --- Inicialização do Redis ---
    redis_cfg = config.get('redis_settings', {})
    try:
        redis_host = os.environ.get('REDIS_HOST', redis_cfg.get('host', 'localhost'))
        redis_port = int(os.environ.get('REDIS_PORT', redis_cfg.get('port', 6379)))
        redis_db = redis_cfg.get('db_fila')
        
        if redis_db is None:
            logger.critical("O banco 'db_fila' não está definido no config.json.")
            sys.exit(1)
            
        redis_client = get_redis(host=redis_host, port=redis_port, db=int(redis_db))
        logger.success(f"Conectado ao Redis {redis_host}:{redis_port} (db={redis_db})")
    except Exception as e:
        logger.critical(f"Erro ao conectar ao Redis: {e}")
        sys.exit(1)
    
    # --- Inicialização de Serviços Base ---
    status_display = None
    watchdog = None
    pool_manager = None
    
    try:
        watchdog = JobWatchdog(redis_client=redis_client, max_job_duration=300, check_interval=30)
        watchdog.iniciar()
        config['watchdog'] = watchdog
        
        mapa_de_filas = {
            "conferencia": redis_cfg.get('conference_queue', 'fila:conferencia'),
            "emissao": redis_cfg.get('emission_queue', 'fila:emissao'),
            "manifesto": redis_cfg.get('manifesto_queue', 'fila:manifesto'),
            "gerenciamento_risco": redis_cfg.get('pre_sm_queue', 'fila:pre_sm') 
        }

        status_display = StatusDisplay(
            redis_client=redis_client, 
            queues_to_monitor=mapa_de_filas,
            update_interval=5
        )
        status_display.iniciar()

        pool_manager = ThreadPoolManager(
            redis_client=redis_client,
            config=config,
            ejecutor_function=executor_inteligente, 
            usuario=USUARIO,
            senha=SENHA,
            rebalance_interval=60, 
            max_threads_per_type=10, 
            max_total_threads=20, 
            status_display=status_display, 
        )
        config['thread_pool_manager'] = pool_manager

        pool_manager.iniciar()
        
        logger.info("Sistema em execução. Pressione Ctrl+C para parar.")
        pool_manager.aguardar_encerramento()

    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário (Ctrl+C). Encerrando...")
    except Exception as e:
        logger.critical(f"Erro fatal no Orquestrador: {e}\n{traceback.format_exc()}")
    finally:
        if status_display: status_display.parar()
        if watchdog: watchdog.parar()
        if pool_manager: pool_manager.parar()
        logger.info("OPS_ENGINE finalizada com sucesso.")

if __name__ == "__main__":
    main()