from playwright.sync_api import Page, Locator
from loguru import logger
from utils.fluxo_utils import goto_cards, analisar_status_emissao, _capturar_debug
from utils.filtros import filtro_cards

def navegar_e_validar_mdfe(page: Page, numero_lt: str) -> dict:
    """Navega até o card da LT, filtra, analisa status e retorna dict com status e locator."""
    logger.debug(f"[navegar_e_validar_mdfe] INICIO - LT: {numero_lt}")
    try:
        logger.debug(f"[navegar_e_validar_mdfe] Chamando goto_cards...")
        goto_cards(page, numero_lt)

        logger.debug(f"[navegar_e_validar_mdfe] Chamando filtro_cards para {numero_lt}...")
        filtro_cards(page, numero_lt)

        try:
            _capturar_debug(page, "apos_filtro", numero_lt)
        except Exception:
            pass

        logger.debug(f"[navegar_e_validar_mdfe] Chamando analisar_status_emissao para {numero_lt}...")
        analise = analisar_status_emissao(page, numero_lt)

        if not analise:
            logger.error(f"[ManifestoUtils] Não foi possível encontrar o card ou analisar o status para LT {numero_lt}.")
            try:
                _capturar_debug(page, "card_nao_encontrado", numero_lt)
            except Exception:
                pass
            return None

        card = analise.get("card")
        status_mdfe = analise.get("status_mdfe")
        logger.debug(f"[navegar_e_validar_mdfe] SUCESSO - status_mdfe: {status_mdfe}")
        return {"card": card, "status_mdfe": status_mdfe, "analise": analise}
    except Exception as e:
        logger.error(f"[ManifestoUtils] Erro ao navegar e validar MDFe para LT {numero_lt}: {e}")
        try:
            _capturar_debug(page, "erro_navegar", numero_lt)
        except Exception:
            pass
        return None
