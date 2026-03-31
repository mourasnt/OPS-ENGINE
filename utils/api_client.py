import urllib3
import requests
from typing import Any, Dict
from loguru import logger

# 1. Configuração global (roda apenas uma vez)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# 2. Regra de ouro para retries: só retente se o problema não for você!
def is_transient_error(exception: BaseException) -> bool:
    if isinstance(exception, requests.exceptions.HTTPError):
        # Retenta só se a API der erro 500 pra cima (erro deles)
        return exception.response.status_code >= 500
    # Retenta se a internet cair, der timeout, etc.
    return isinstance(exception, requests.exceptions.RequestException)


class RasterService:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        logger.info(f"🌐 API Client configurado para {self.base_url}")

    def _headers(self) -> Dict[str, str]:
        return {'Content-Type': 'application/json'}
    
    def status_job(self, job_id: str) -> requests.Response:
        url = f"{self.base_url}/sm/status/{job_id}"
        logger.debug(f'🔍 Verificando status do job: {job_id}')
        
        # Timeout reduzido para 30s
        resp = requests.get(url, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        return resp


class PreSMClient(RasterService):
    def criar_lote(self, payload: Any) -> requests.Response:
        url = f"{self.base_url}/sm/criar"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de criação de Pré-SM criado! JobID retornado.')
        return resp
    def cancelar_pre_sm(self, cod_pre_sm: str) -> requests.Response:
        url = f"{self.base_url}/sm/cancelar-pre-sm"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json={'cod_pre_sm': cod_pre_sm}, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de cancelamento de Pré-SM criado! JobID retornado.')
        return resp
    def refazer_pre_sm(self, cod_pre_sm: str, payload: Any) -> requests.Response:
        url = f"{self.base_url}/sm/refazer-pre-sm"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json={'cod_pre_sm': cod_pre_sm, 'payload': payload}, headers=self._headers(), timeout=30, verify=False)
        print(resp.text)
        resp.raise_for_status()
        logger.success('✅ Lote de atualização de Pré-SM criado! JobID retornado.')
        return resp

class SMClient(RasterService):
    def efetivar_lote(self, payload: Any) -> requests.Response:
        url = f"{self.base_url}/sm/efetivar"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json=payload, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de efetivação de SM criado! JobID retornado.')
        return resp
    def cancelar_sm(self, cod_sm: str, motivo: str) -> requests.Response:
        url = f"{self.base_url}/sm/cancelar-sm"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json={'cod_sm': cod_sm, 'motivo': motivo}, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de cancelamento de SM criado! JobID retornado.')
        return resp
    def finalizar_sm(self, cod_sm: str) -> requests.Response:
        url = f"{self.base_url}/sm/finalizar-sm"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json={'cod_sm': cod_sm}, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de finalização de SM criado! JobID retornado.')
        return resp
    def refazer_sm(self, cod_sm: str, payload: Any) -> requests.Response:
        url = f"{self.base_url}/sm/refazer-sm"
        logger.debug(f'📤 POST {url}')
        
        resp = requests.post(url, json={'cod_sm': cod_sm, 'payload': payload}, headers=self._headers(), timeout=30, verify=False)
        resp.raise_for_status()
        logger.success('✅ Lote de atualização de SM criado! JobID retornado.')
        return resp