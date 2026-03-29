from playwright.sync_api import Page, Locator
from loguru import logger
from utils.fluxo_utils import goto_cards, analisar_status_emissao
from utils.filtros import filtro_cards

# --- Função compartilhada: Navega e valida status do MDFe ---
def navegar_e_validar_mdfe(page: Page, numero_lt: str) -> dict:
    """
    Navega até o card da LT, filtra, analisa status e retorna dict com status e locator.
    Retorna None se não encontrar card ou status.
    """
    try:
        goto_cards(page)
        filtro_cards(page, numero_lt)
        analise = analisar_status_emissao(page, numero_lt)
        if not analise:
            logger.error(f"[ManifestoUtils] Não foi possível encontrar o card ou analisar o status para LT {numero_lt}.")
            return None
        card = analise.get("card")
        status_mdfe = analise.get("status_mdfe")
        return {"card": card, "status_mdfe": status_mdfe, "analise": analise}
    except Exception as e:
        logger.error(f"[ManifestoUtils] Erro ao navegar e validar MDFe para LT {numero_lt}: {e}")
        return None
