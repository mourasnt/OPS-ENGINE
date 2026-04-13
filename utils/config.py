import json
import os
from typing import Any, Dict, Optional
from pathlib import Path


class Config:
    """
    Singleton para configuração centralizada da OPS Engine.
    Substitui as múltiplas leituras de config.jsonspread pelo código.
    """
    _instance = None
    _data: Dict[str, Any] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._load()
        return cls._instance

    def __init__(self):
        pass

    def _load(self):
        """Carrega e valida configuração na inicialização."""
        config_path = os.environ.get('CONFIG_PATH', 'utils/config.json')
        
        if not os.path.exists(config_path):
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r', encoding='utf-8') as f:
            self._data = json.load(f)

        self._validate()

    def _validate(self):
        """Valida seções obrigatórias da configuração."""
        required_sections = ['redis_settings', 'main_sheet', 'poller_settings']
        missing = [s for s in required_sections if s not in self._data]
        
        if missing:
            raise ValueError(f"Config sections missing: {missing}")

    # =========================================================================
    # TIMEOUT SETTINGS
    # =========================================================================
    @property
    def page_reload_timeout(self) -> int:
        return self._data.get("timeout_settings", {}).get("page_reload_ms", 45000)

    @property
    def login_navigation_timeout(self) -> int:
        return self._data.get("timeout_settings", {}).get("login_navigation_ms", 45000)

    @property
    def default_timeout(self) -> int:
        return self._data.get("timeout_settings", {}).get("default_ms", 30000)

    @property
    def api_timeout(self) -> int:
        return self._data.get("timeout_settings", {}).get("api_timeout_seconds", 30)

    # =========================================================================
    # REDIS SETTINGS
    # =========================================================================
    @property
    def redis_settings(self) -> Dict[str, Any]:
        return self._data.get("redis_settings", {})

    @property
    def redis_host(self) -> str:
        return os.environ.get('REDIS_HOST', self.redis_settings.get('host', 'localhost'))

    @property
    def redis_port(self) -> int:
        return int(os.environ.get('REDIS_PORT', self.redis_settings.get('port', 6379)))

    @property
    def redis_db_fila(self) -> int:
        return self.redis_settings.get('db_fila', 0)

    @property
    def redis_db_state(self) -> int:
        return self.redis_settings.get('db_state', 1)

    @property
    def redis_db_bases(self) -> int:
        return self.redis_settings.get('db_bases', 2)

    @property
    def conference_queue(self) -> str:
        return self.redis_settings.get('conference_queue', 'fila:conferencia')

    @property
    def emission_queue(self) -> str:
        return self.redis_settings.get('emission_queue', 'fila:emissao')

    @property
    def manifesto_queue(self) -> str:
        return self.redis_settings.get('manifesto_queue', 'fila:manifesto')

    @property
    def pre_sm_queue(self) -> str:
        return self.redis_settings.get('pre_sm_queue', 'fila:pre_sm')

    @property
    def results_queue(self) -> str:
        return self.redis_settings.get('results_queue', 'fila:resultados')

    @property
    def control_set(self) -> str:
        return self.redis_settings.get('control_set', 'jobs_em_progresso')

    @property
    def manifesto_set(self) -> str:
        return self.redis_settings.get('manifesto_set', 'jobs_manifesto_em_progresso')

    # =========================================================================
    # GOOGLE SHEETS SETTINGS
    # =========================================================================
    @property
    def main_sheet(self) -> Dict[str, Any]:
        return self._data.get("main_sheet", {})

    @property
    def spreadsheet_id(self) -> str:
        return self.main_sheet.get('spreadsheet_id', '')

    @property
    def worksheet_name(self) -> str:
        return self.main_sheet.get('worksheet_name', '')

    @property
    def header_row_number(self) -> int:
        return self.main_sheet.get('header_row_number', 3)

    @property
    def error_log_sheet(self) -> Dict[str, Any]:
        return self._data.get("error_log_sheet", {})

    @property
    def locations_sheet(self) -> Dict[str, Any]:
        return self._data.get("locations_sheet", {})

    # =========================================================================
    # POLLER SETTINGS
    # =========================================================================
    @property
    def poller_settings(self) -> Dict[str, Any]:
        return self._data.get("poller_settings", {})

    @property
    def poll_interval_seconds(self) -> int:
        return self.poller_settings.get('poll_interval_seconds', 300)

    @property
    def statusConferir(self) -> list:
        return self.poller_settings.get('statusConferir', [])

    # =========================================================================
    # SM SETTINGS
    # =========================================================================
    @property
    def sm_settings(self) -> Dict[str, Any]:
        return self._data.get("sm_settings", {})

    @property
    def status_array_pre_sm(self) -> list:
        return self.sm_settings.get('status_array_pre_sm', [])

    @property
    def status_array_efetivacao(self) -> list:
        return self.sm_settings.get('status_array_efetivacao', [])

    @property
    def hr_antes_eta(self) -> int:
        return self.sm_settings.get('HR_ANTES_ETA', 7)

    @property
    def api_base_url(self) -> str:
        return os.environ.get('API_BASE_URL', self.sm_settings.get('api_base_url', ''))

    # =========================================================================
    # WRITER SETTINGS
    # =========================================================================
    @property
    def writer_settings(self) -> Dict[str, Any]:
        return self._data.get("writer_settings", {})

    @property
    def batch_max_size_cells(self) -> int:
        return self.writer_settings.get('batch_max_size_cells', 200)

    @property
    def batch_max_size_rows(self) -> int:
        return self.writer_settings.get('batch_max_size_rows', 50)

    @property
    def batch_max_wait_seconds(self) -> int:
        return self.writer_settings.get('batch_max_wait_seconds', 5)

    # =========================================================================
    # THREAD POOL SETTINGS
    # =========================================================================
    @property
    def thread_pool_settings(self) -> Dict[str, Any]:
        return self._data.get("thread_pool_settings", {})

    @property
    def min_threads_per_type(self) -> int:
        return self.thread_pool_settings.get('min_threads_per_type', 1)

    @property
    def jobs_per_thread_ratio(self) -> int:
        return self.thread_pool_settings.get('jobs_per_thread_ratio', 50)

    # =========================================================================
    # WATCHDOG SETTINGS
    # =========================================================================
    @property
    def watchdog_settings(self) -> Dict[str, Any]:
        return self._data.get("watchdog_settings", {})

    @property
    def max_job_duration(self) -> int:
        return self.watchdog_settings.get('max_job_duration_seconds', 300)

    @property
    def watchdog_check_interval(self) -> int:
        return self.watchdog_settings.get('check_interval_seconds', 30)

    # =========================================================================
    # UTILITIES
    # =========================================================================
    def get(self, key: str, default: Any = None) -> Any:
        """Acesso genérico a config."""
        return self._data.get(key, default)

    def get_creds_path(self) -> str:
        """Retorna path das credenciais Google."""
        return os.environ.get('GOOGLE_APPLICATION_CREDENTIALS', '')

    def get_rpa_usuario(self) -> str:
        """Retorna usuário RPA."""
        return os.environ.get('RPA_USUARIO', '')

    def get_rpa_senha(self) -> str:
        """Retorna senha RPA."""
        return os.environ.get('RPA_SENHA', '')


def carregar_config() -> Optional[Dict[str, Any]]:
    """Função de compatibilidade - prefer usar Config() singleton."""
    try:
        return Config()._data
    except Exception:
        return None