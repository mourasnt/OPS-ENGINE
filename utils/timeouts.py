"""
Timeouts centralizados para toda a aplicação.
Use estes valores em vez de hardcodar números pelo código.
"""

# =========================================================================
# PAGE TIMEOUTS (Playwright)
# =========================================================================
PAGE_RELOAD_MS = 45000
LOGIN_NAVIGATION_MS = 45000
DEFAULT_ELEMENT_WAIT_MS = 30000
DROPDOWN_WAIT_MS = 7000
BUTTON_CLICK_TIMEOUT_MS = 5000
MODAL_WAIT_MS = 5000
INPUT_FILL_DELAY_MS = 50

# =========================================================================
# API TIMEOUTS
# =========================================================================
API_REQUEST_TIMEOUT_SECONDS = 30
API_RETRY_MAX_SECONDS = 30

# =========================================================================
# POLLING TIMEOUTS
# =========================================================================
POLL_INTERVAL_SECONDS = 300
QUEUE_POP_TIMEOUT_SECONDS = 60

# =========================================================================
# WRITER TIMEOUTS
# =========================================================================
BATCH_MAX_WAIT_SECONDS = 5
REDIS_RECONNECT_BACKOFF_MAX = 32

# =========================================================================
# WATCHDOG TIMEOUTS
# =========================================================================
MAX_JOB_DURATION_SECONDS = 300
WATCHDOG_CHECK_INTERVAL_SECONDS = 30

# =========================================================================
# WORKER TIMEOUTS
# =========================================================================
THREAD_REBALANCE_INTERVAL_SECONDS = 60
STATUS_DISPLAY_UPDATE_INTERVAL_SECONDS = 5

# =========================================================================
# RPA STEP TIMEOUTS (individuais)
# =========================================================================
TIMEOUT_ENCONTRAR_LT_TABELA = 10
TIMEOUT_CLICAR_BOTAO_EDICAO = 5
TIMEOUT_AGUARDAR_FORMULARIO = 10
TIMEOUT_PREENCHER_CAMPO = 15
TIMEOUT_PREENCHER_COMPLEXO = 25
TIMEOUT_SUBMETER_FORMULARIO = 30


def get_page_reload_timeout() -> int:
    """Retorna timeout para reload de página (compatível com config)."""
    try:
        from utils.config import Config
        return Config().page_reload_timeout
    except Exception:
        return PAGE_RELOAD_MS


def get_login_timeout() -> int:
    """Retorna timeout para navegação de login."""
    try:
        from utils.config import Config
        return Config().login_navigation_timeout
    except Exception:
        return LOGIN_NAVIGATION_MS