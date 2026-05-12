from playwright.sync_api import TimeoutError, Page, expect
import time
import re
from loguru import logger
from typing import Dict, Any


def revisar_lt(page: Page, numero_lt: str) -> Dict[str, Any]:
    try:
        
        # 1. LOCALIZAR O CARD
        card_locator = page.locator(".MuiGrid-root.MuiGrid-item.MuiGrid-grid-xs-12.MuiGrid-grid-sm-6").filter(
            has_text=re.compile(rf"DT:\s*{re.escape(numero_lt)}")
        )

        count = card_locator.count()
        if count == 0:
            motivo = f"Nenhum card encontrado para DT: {numero_lt}"
            logger.error(f"[Worker Emissão] [LT {numero_lt}] {motivo}")
            return {"status": "falha_rpa", "motivo": motivo}
        elif count > 1:
            logger.warning(f"[Worker Emissão] [LT {numero_lt}] Mais de um card encontrado, usando o primeiro.")

        # 2. EXECUTAR A CADEIA DE CLIQUES
        print("chegou")
        card_locator.nth(0).locator("button").first.click()
        time.sleep(1)

        page.get_by_role("menuitem", name="Conferir Carga").click()
        page.get_by_role("button", name="Próximo").click()
        page.get_by_role("button", name="Próximo").click()
        page.get_by_role("button", name="Próximo").click()
        page.get_by_role("textbox", name="Componente").click()

        expect(page.get_by_role("textbox", name="Componente")).to_be_visible(timeout=10000)
        page.get_by_role("textbox", name="Componente").type("gris")
        page.get_by_role("option", name="GRIS").click()
        page.get_by_role("button", name="Próximo").click()
        page.get_by_role("button", name="EmiteAí!").click()

        time.sleep(2) # Mantido do original
        
        # Tenta fechar o modal/popup (opcional)
        try:
            page.get_by_role("img").first.click(timeout=3000) # Adicionado timeout
        except Exception:
            logger.debug(f"[Worker Emissão] [LT {numero_lt}] Modal de sucesso não fechado (opcional), ignorando.")
            pass
        return {"status": "sucesso"}

    except TimeoutError as e:
        detalhe_erro = str(e).split('\n')[0]
        motivo = f"Timeout ao revisar LT: {detalhe_erro}"
        logger.error(f"[Worker Emissão] [LT {numero_lt}] {motivo}")

        # Tenta se recuperar (lógica original)
        for _ in range(3):
            page.keyboard.press("Escape")
            time.sleep(0.2)
        return {"status": "falha_rpa", "motivo": motivo}
    
    except Exception as e:
        motivo = f"Erro inesperado ao revisar LT: {e}"
        logger.critical(f"[Worker Emissão] [LT {numero_lt}] {motivo}")

        # Tenta se recuperar (lógica original)
        for _ in range(3):
            page.keyboard.press("Escape")
            time.sleep(0.2)
        return {"status": "falha_rpa", "motivo": motivo}