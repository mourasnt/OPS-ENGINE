import os
import threading
import time
import sys
import json
from playwright.sync_api import sync_playwright
from loguru import logger
from typing import Dict, Any

try:
    from utils.helpers import carregar_config 
except ImportError:
    logger.critical("Não foi possível encontrar 'utils.helpers.carregar_config'.")
    exit(1)

try:
    from utils.redis_client import get_redis
except ImportError:
    logger.critical("Não foi possível encontrar 'utils.redis_client.get_redis'.")
    exit(1)

try:
    from utils.fluxo_utils import ThreadPoolManager
except ImportError:
    logger.critical("Não foi possível encontrar 'utils.fluxo_utils.ThreadPoolManager'.")
    exit(1)

try:
    from utils.watchdog import JobWatchdog
except ImportError:
    logger.critical("Não foi possível encontrar 'utils.watchdog.JobWatchdog'.")
    exit(1)

try:
    from utils.status_display import StatusDisplay
except ImportError:
    logger.critical("Não foi possível encontrar 'utils.status_display.StatusDisplay'.")
    exit(1)


from workers.fluxo_conferencia import fluxo_conferencia_worker
from workers.fluxo_verificar_emissao import fluxo_verificar_emissao_worker
from workers.fluxo_encerrar_manifesto import fluxo_encerrar_manifesto_worker
from fluxos.fluxo_login import fluxo_login

try:
    from workers.fluxo_sm import fluxo_sm_worker
except ImportError:
    logger.warning("Não foi possível encontrar 'workers.fluxo_sm.fluxo_sm_worker'. Crie este arquivo em breve.")

logger.remove()

def filtro_logs_importantes(record):
    """Filtra para mostrar apenas WARNING, ERROR, CRITICAL e SUCCESS (que é INFO)."""
    level_name = record["level"].name
    return level_name in ["WARNING", "ERROR", "CRITICAL", "SUCCESS"]

# Logs IMPORTANTES para stdout (Docker)
logger.add(
    sink=sys.stdout, 
    format="{time:HH:mm:ss} | {level:<8} | {message}",
    level="DEBUG",  # Captura tudo, mas o filtro seleciona
    filter=filtro_logs_importantes,
    enqueue=True
)
# Logs para arquivo
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
    exit(1)

# ===================================================================
# FUNÇÃO DE EXECUÇÃO DE FLUXO (Alvo da Thread - RPA Web/Playwright)
# ===================================================================
def executar_fluxo(nome_fluxo: str, funcao_fluxo, config: Dict[str, Any]): 
    """
    Executa um único worker de automação em seu próprio contexto E
    em sua própria instância do Playwright.
    """
    context = None
    browser = None 
    
    with sync_playwright() as playwright:
        try:
            logger.info(f"Iniciando thread e navegador para o worker: '{nome_fluxo}'")
            
            browser = playwright.firefox.launch(headless=True)
            context = browser.new_context()
            page = context.new_page()
            page.goto("https://portal.emiteai.com.br/#/login")

            # Tenta login com retry e backoff exponencial
            login_ok = False
            for login_attempt in range(1, 4):  # 3 tentativas
                logger.info(f"Tentativa de login {login_attempt}/3 para worker '{nome_fluxo}'...")
                login_ok = fluxo_login(page=page, usuario=USUARIO, senha=SENHA)
                if login_ok:
                    logger.success(f"Login realizado com sucesso para '{nome_fluxo}' na tentativa {login_attempt}.")
                    break
                
                logger.warning(f"Login falhou na tentativa {login_attempt}/3 para '{nome_fluxo}'.")
                if login_attempt < 3:
                    wait_time = 30 * login_attempt  # 30s, 60s
                    logger.info(f"Aguardando {wait_time}s antes da próxima tentativa...")
                    time.sleep(wait_time)
                    # Recarrega a página para tentar novamente
                    try:
                        page.goto("https://portal.emiteai.com.br/#/login", timeout=45000)
                    except Exception as nav_err:
                        logger.error(f"Erro ao navegar para login na tentativa {login_attempt + 1}: {nav_err}")
            
            if not login_ok:
                logger.critical(f"Todas as tentativas de login falharam para o worker '{nome_fluxo}'. A thread será encerrada.")
                return
            
            funcao_fluxo(page, config) 
            
            logger.info(f"Worker '{nome_fluxo}' encerrou seu loop de consumo. (Pode ser downscaling ou encerramento normal)")

        except Exception as e:
            import traceback
            mensagem_erro = f"Erro fatal no worker '{nome_fluxo}': {e}"
            logger.critical(mensagem_erro)
            logger.critical(f"Stack trace completo:\n{traceback.format_exc()}")
        
        finally:
            # Garante que tudo criado na thread seja fechado nela
            if context:
                context.close()
            if browser:
                browser.close()
            logger.info(f"Thread do worker '{nome_fluxo}' foi finalizada e recursos liberados.")

# ===================================================================
# FUNÇÃO DE EXECUÇÃO DE FLUXO PARA API (Sem Playwright)
# ===================================================================
def executar_fluxo_api(nome_fluxo: str, funcao_fluxo, config: Dict[str, Any]):
    """
    Executa um worker baseado puramente em API (Background Jobs).
    Não inicializa o Playwright, poupando memória e CPU consideravelmente.
    """
    try:
        logger.info(f"Iniciando thread de API para o worker: '{nome_fluxo}'")
        
        # Como não tem navegador (page), passamos apenas o config
        funcao_fluxo(config) 
        
        logger.info(f"Worker de API '{nome_fluxo}' encerrou seu loop de consumo.")

    except Exception as e:
        import traceback
        mensagem_erro = f"Erro fatal no worker de API '{nome_fluxo}': {e}"
        logger.critical(mensagem_erro)
        logger.critical(f"Stack trace completo:\n{traceback.format_exc()}")
    finally:
        logger.info(f"Thread do worker de API '{nome_fluxo}' foi finalizada.")


# ===================================================================
# MAIN (Orquestrador com ThreadPoolManager)
# ===================================================================
def main():
    config = carregar_config()
    if not config:
        logger.critical("Não foi possível carregar o config.json. Encerrando.")
        return
    
    logger.info("Iniciando Orquestrador de Workers RPA com ThreadPoolManager...")
    logger.warning("Lembre-se de iniciar o 'poller.py' e o 'writer.py' em terminais separados.")

    # Inicializa cliente Redis usando configurações do config.json
    redis_cfg = config.get('redis_settings', {})
    try:
        redis_host = os.environ.get('REDIS_HOST', redis_cfg.get('host', 'redis-emiteai'))
        redis_port = int(os.environ.get('REDIS_PORT', redis_cfg.get('port', 6379)))
        redis_db = int(os.environ.get('REDIS_DB', redis_cfg.get('db', 0)))
        
        redis_client = get_redis(host=redis_host, port=redis_port, db=redis_db)
        logger.success(f"Conectado ao Redis em {redis_host}:{redis_port} (db={redis_db})")
    except Exception as e:
        logger.critical(f"Erro ao conectar ao Redis: {e}")
        return
    
    try:
        # Inicializa o Watchdog para detectar travamentos
        watchdog = JobWatchdog(
            redis_client=redis_client,
            max_job_duration=300,      # 5 minutos máximo por job
            check_interval=30          # Verificar a cada 30 segundos
        )
        watchdog.iniciar()
        logger.success("Watchdog de travamentos iniciado")
        
        # Adiciona watchdog ao config para os workers acessarem
        config['watchdog'] = watchdog
        
        # Inicializa display de status em tempo real
        status_display = StatusDisplay(
            redis_client=redis_client,
            update_interval=5  # Atualiza a cada 5 segundos
        )
        status_display.iniciar()
        logger.success("Status display iniciado")
        

        # Cria o gerenciador de thread pool dinâmico
        pool_manager = ThreadPoolManager(
            redis_client=redis_client,
            config=config,
            ejecutor_function=executar_fluxo,
            usuario=USUARIO,
            senha=SENHA,
            rebalance_interval=60,  # Verifica a cada 60 segundos
            max_threads_per_type=10,  # Máximo 10 threads por tipo
            max_total_threads=20,  # Máximo 20 threads no total
            status_display=status_display,  # Passar o status display
        )

        # Adiciona pool_manager ao config para os workers acessarem
        config['thread_pool_manager'] = pool_manager

        # Inicia os workers em threads separadas
        threads = []
        # Workers Web (com Playwright)
        threads.append(threading.Thread(target=executar_fluxo, args=("conferencia", fluxo_conferencia_worker, config), daemon=True))
        threads.append(threading.Thread(target=executar_fluxo, args=("emissao", fluxo_verificar_emissao_worker, config), daemon=True))
        threads.append(threading.Thread(target=executar_fluxo, args=("manifesto", fluxo_encerrar_manifesto_worker, config), daemon=True))
        
        # Workers de API (Sem Playwright)
        if 'fluxo_sm_worker' in globals():
            threads.append(threading.Thread(target=executar_fluxo_api, args=("gerenciamento_risco", fluxo_sm_worker, config), daemon=True))
        else:
            logger.warning("Worker de SM (Gerenciamento Risco) ignorado na inicialização pois o import falhou.")

        for t in threads:
            t.start()

        # Inicia o gerenciador
        pool_manager.iniciar()

        # Loop de monitoramento principal
        logger.info("ThreadPoolManager em execução. Pressione Ctrl+C para parar.")
        pool_manager.aguardar_encerramento()

    except KeyboardInterrupt:
        logger.warning("Execução interrompida pelo usuário (Ctrl+C). Encerrando...")
        if 'status_display' in locals():
            status_display.parar()
        if 'watchdog' in locals():
            watchdog.parar()
        if 'pool_manager' in locals():
            pool_manager.parar()
    
    except Exception as e:
        mensagem_erro = f"Erro fatal no Orquestrador (main): {e}"
        logger.critical(mensagem_erro)
        if 'status_display' in locals():
            status_display.parar()
        if 'watchdog' in locals():
            watchdog.parar()
        if 'pool_manager' in locals():
            pool_manager.parar()
    
    finally:
        logger.info("Automação finalizada.")

if __name__ == "__main__":
    main()