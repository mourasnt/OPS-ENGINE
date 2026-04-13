from playwright.sync_api import TimeoutError, Page, expect
import datetime
import re
from loguru import logger
import time
from utils.timeouts import get_page_reload_timeout

PAGE_RELOAD_TIMEOUT = get_page_reload_timeout()


def filtro_cargas(page: Page, numero_lt: str):
    logger.debug(f"[filtro_cargas] Iniciando filtro para LT {numero_lt}...")
    try:
        # --- 1. Seletores ---
        logger.debug(f"[filtro_cargas] Localizando seletores...")
        filtrar_button = page.get_by_role("button", name="Filtrar")
        data_inicial_input = page.locator("div").filter(has_text=re.compile(r"^Data Inicial$")).get_by_role("textbox")
        arquivo_input = page.locator("div").filter(has_text=re.compile(r"^Nome do arquivo$")).get_by_role("textbox")

        # --- 2. GARANTIR QUE A PÁGINA ESTÁ PRONTA ---
        logger.debug(f"[filtro_cargas] Aguardando botão Filtrar ficar visível...")
        filtrar_button.wait_for(state="visible", timeout=15000)

        # --- 3. Abrir o painel de filtros (se necessário) ---
        logger.debug(f"[filtro_cargas] Verificando se painel de filtros está aberto...")
        if not data_inicial_input.is_visible():
            logger.debug(f"[filtro_cargas] Abrindo painel de filtros...")
            filtrar_button.click()
            expect(data_inicial_input).to_be_visible(timeout=5000)

        # --- 4. Preenchimento do formulário ---
        logger.debug(f"[filtro_cargas] Preenchendo formulário de filtro...")
        data_final = datetime.datetime.now()
        data_inicial = data_final - datetime.timedelta(days=30)
        
        data_final_input = page.locator("div").filter(has_text=re.compile(r"^Data Final$")).get_by_role("textbox")
        
        data_inicial_input.fill(data_inicial.strftime("%d/%m/%Y"))
        data_final_input.fill(data_final.strftime("%d/%m/%Y"))
        arquivo_input.fill(numero_lt)

        # --- 5. Executar a pesquisa ---
        logger.debug(f"[filtro_cargas] Clicando em Pesquisar...")
        pesquisar_btn = page.get_by_role("button", name="Pesquisar")
        pesquisar_btn.click()

        logger.debug(f"[filtro_cargas] Aguardando networkidle (máx 20s)...")
        page.wait_for_load_state("networkidle", timeout=20000)
        logger.debug(f"[filtro_cargas] Pesquisa concluída!")

        # --- 6. Fechar o filtro ---
        if data_inicial_input.is_visible():
            logger.debug(f"[filtro_cargas] Fechando painel de filtros...")
            filtrar_button.click()
            expect(data_inicial_input).to_be_hidden(timeout=5000)
        
        logger.debug(f"[filtro_cargas] Filtro para LT {numero_lt} finalizado com sucesso!")

    except TimeoutError as e:
        detalhe_erro = str(e).split('\n')[0]
        logger.error(f"[Worker Conferência] [LT {numero_lt}] Timeout ao pesquisar: {detalhe_erro}")
        logger.debug(f"[Worker Conferência] [LT {numero_lt}] URL no momento do erro: {page.url}")
        try:
            page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded")
            logger.debug(f"[Worker Conferência] [LT {numero_lt}] URL após reload: {page.url}")
        except Exception as reload_err:
            logger.error(f"[Worker Conferência] [LT {numero_lt}] Falha ao recarregar: {reload_err}")
        raise

    except Exception as e:
        logger.critical(f"[Worker Conferência] [LT {numero_lt}] Erro inesperado ao pesquisar: {e}")
        try:
            page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded")
        except Exception as reload_err:
            logger.error(f"[Worker Conferência] [LT {numero_lt}] Falha ao recarregar: {reload_err}")
        raise

def filtro_cards(page: Page, numero_lt: str):

    def ir_para_inicio_input(locator_name):
        for _ in range(10):
            page.locator(f"input[name=\"{locator_name}\"]").press("ArrowLeft")
        return
    
    try:

        # 1. Seletores
        filtrar_button = page.get_by_role("button", name="Filtrar")
        data_inicial_input = page.locator("input[name=\"dataInicial\"]")
        data_final_input = page.locator("input[name=\"dataFinal\"]")
        dt_input = page.get_by_role("textbox", name="DTs")
        valores_dt_input = page.get_by_role("textbox", name="Valores")
        salvar_dt_input = page.get_by_role("button", name="Salvar")
        pesquisar_button = page.get_by_role("button", name="Pesquisar")

        # 2. Garante que a página está pronta
        expect(filtrar_button).to_be_visible(timeout=15000)

        # 3. Abre o painel de filtros
        if not data_inicial_input.is_visible():
            filtrar_button.click()
            expect(data_inicial_input).to_be_visible(timeout=5000)

        # 4. Preenche o formulário
        data_final = datetime.datetime.now()
        data_inicial = data_final - datetime.timedelta(days=60)
        
        data_inicial_str = data_inicial.strftime("%m-%d-%YT00:00")
        data_final_str = data_final.strftime("%m-%d-%YT23:59")

        data_inicial_input.click()
        data_inicial_input.fill("")
        ir_para_inicio_input("dataInicial")
        data_inicial_input.type(data_inicial_str)

        data_final_input.click()
        data_final_input.fill("")
        ir_para_inicio_input("dataFinal")
        data_final_input.type(data_final_str)

        dt_input.click()
        valores_dt_input.press("Backspace")
        valores_dt_input.type(numero_lt)
        valores_dt_input.press("Enter")
        time.sleep(3)
        salvar_dt_input.click()

        # 5. Executa a pesquisa
        pesquisar_button.click()
        page.wait_for_load_state("networkidle", timeout=30000)

        # 6. Fecha o painel
        if data_inicial_input.is_visible():
            filtrar_button.click()
            expect(data_inicial_input).to_be_hidden(timeout=5000)

    except TimeoutError as e:
        detalhe_erro = str(e).split('\n')[0]
        logger.error(f"[Worker Emissão] [LT {numero_lt}] Timeout na pesquisa de cards: {detalhe_erro}")
        try:
            page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded")
        except Exception as reload_err:
            logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha ao recarregar: {reload_err}")
        raise

    except Exception as e:
        logger.critical(f"[Worker Emissão] [LT {numero_lt}] Erro inesperado na pesquisa de cards: {e}")
        try:
            page.reload(timeout=PAGE_RELOAD_TIMEOUT, wait_until="domcontentloaded")
        except Exception as reload_err:
            logger.error(f"[Worker Emissão] [LT {numero_lt}] Falha ao recarregar: {reload_err}")
        raise