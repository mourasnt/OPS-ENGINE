import os
from playwright.sync_api import TimeoutError, Page, expect
import datetime
import re
from loguru import logger
import time
from utils.timeouts import get_page_reload_timeout
from utils.fluxo_utils import _capturar_debug

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
    logger.debug(f"[filtro_cards] INICIO - LT: {numero_lt}")

    def ir_para_inicio_input(locator_name):
        for _ in range(10):
            page.locator(f"input[name=\"{locator_name}\"]").press("ArrowLeft")
        return

    try:
        filtrar_button = page.get_by_role("button", name="Filtrar")
        data_inicial_input = page.locator("input[name=\"dataInicial\"]")
        data_final_input = page.locator("input[name=\"dataFinal\"]")
        dt_input = page.get_by_role("textbox", name="DTs")
        valores_dt_input = page.get_by_role("textbox", name="Valores")
        salvar_dt_input = page.get_by_role("button", name="Salvar")
        pesquisar_button = page.get_by_role("button", name="Pesquisar")

        logger.debug(f"[filtro_cards]LOCALIZOU seleniums")
        expect(filtrar_button).to_be_visible(timeout=15000)
        logger.debug(f"[filtro_cards] Botao Filtrar visivel")

        if not data_inicial_input.is_visible():
            logger.debug(f"[filtro_cards]Abrindo painel de filtros...")
            filtrar_button.click()
            expect(data_inicial_input).to_be_visible(timeout=5000)
            logger.debug(f"[filtro_cards] Painel aberto")
        else:
            logger.debug(f"[filtro_cards] Painel ja estava aberto")

        data_final = datetime.datetime.now()
        data_inicial = data_final - datetime.timedelta(days=60)

        data_inicial_str = data_inicial.strftime("%m-%d-%YT00:00")
        data_final_str = data_final.strftime("%m-%d-%YT23:59")

        logger.debug(f"[filtro_cards] Preenchendo datas: {data_inicial_str} ate {data_final_str}")

        data_inicial_input.click()
        data_inicial_input.fill("")
        ir_para_inicio_input("dataInicial")
        data_inicial_input.type(data_inicial_str)

        data_final_input.click()
        data_final_input.fill("")
        ir_para_inicio_input("dataFinal")
        data_final_input.type(data_final_str)

        logger.debug(f"[filtro_cards] Preenchendo LT no campo Valores...")
        dt_input.click()
        valores_dt_input.press("Backspace")
        valores_dt_input.type(numero_lt)
        logger.debug(f"[filtro_cards] LT digitada: {numero_lt}")
        valores_dt_input.press("Enter")
        time.sleep(3)
        logger.debug(f"[filtro_cards] Clicando Salvar...")
        salvar_dt_input.click()

        logger.debug(f"[filtro_cards] Clicando Pesquisar...")
        pesquisar_button.click()
        logger.debug(f"[filtro_cards] Aguardando networkidle...")

        try:
            _capturar_debug(page, "antes_pesquisar", numero_lt)
        except Exception:
            pass

        page.wait_for_load_state("networkidle", timeout=30000)

        Cards_count = page.locator(".MuiGrid-root.MuiGrid-item.MuiGrid-grid-xs-12.MuiGrid-grid-sm-6").count()
        logger.debug(f"[filtro_cards] Apos pesquisa - Total cards: {Cards_count}")

        try:
            _capturar_debug(page, "apos_pesquisar", numero_lt)
        except Exception:
            pass

        if data_inicial_input.is_visible():
            logger.debug(f"[filtro_cards] Fechando painel...")
            filtrar_button.click()
            expect(data_inicial_input).to_be_hidden(timeout=5000)

        logger.debug(f"[filtro_cards] FIM - LT: {numero_lt}")

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