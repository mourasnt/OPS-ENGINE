import json
import os
from typing import Dict, Any, Optional
from datetime import datetime, time, timedelta
from loguru import logger

from utils.redis_client import get_redis
from utils.api_client import PreSMClient, SMClient
from utils.job_history import JobHistory


class WorkerSM:
    def __init__(self, config_dict: Dict[str, Any]):
        """
        Inicializa o Worker de Gerenciamento de Risco (API).
        Puro executor: Lê a fila, bate na API, salva estado no Redis e dorme.
        Quem verifica pendências agora é o Poller.
        """
        self.config_dict = config_dict
        
        # --- Configurações do Redis ---
        redis_cfg = config_dict.get('redis_settings', {})
        r_host = os.environ.get('REDIS_HOST', redis_cfg.get('host', 'localhost'))
        r_port = int(os.environ.get('REDIS_PORT', redis_cfg.get('port', 6379)))
        
        # DB 3 (Padrão): Filas de trabalho e comunicação com o Writer
        r_db_filas = int(os.environ.get('REDIS_DB', redis_cfg.get('db', 3)))
        # DB 5 (Bases): Cache alimentado pelo Poller
        r_db_bases = redis_cfg.get('db_bases', 5) 
        
        self.r_filas = get_redis(host=r_host, port=r_port, db=r_db_filas)
        self.r_bases = get_redis(host=r_host, port=r_port, db=r_db_bases)

        # Filas extraídas do config.json
        self.q_pre_sm = redis_cfg.get('pre_sm_queue', 'fila:pre_sm')
        self.q_efetivacao = redis_cfg.get('efetivacao_queue', 'fila:efetivacao_sm')
        self.fila_resultados = redis_cfg.get('results_queue', 'fila:resultados') 

        # --- Clientes de API ---
        sm_cfg = config_dict.get('sm_settings', {})
        api_base_url = os.environ.get('API_BASE_URL', sm_cfg.get('api_base_url', ''))
        
        self.api = PreSMClient(api_base_url)
        self.api_efetivacao = SMClient(api_base_url)
        
        # Histórico de Jobs 
        try:
            self.history = JobHistory(redis_client=self.r_filas)
        except TypeError:
            self.history = JobHistory()


    # -------------------------
    # Comunicação com o Writer
    # -------------------------
    def enviar_update_writer(self, rownum: int, coluna: str, valor: Any):
        """Envia um job para a fila do Writer atualizar a planilha (usado em erros imediatos)."""
        if not rownum:
            logger.warning(f"Tentativa de update na coluna '{coluna}' falhou: rownum inválido.")
            return

        job_writer = {
            "tipo_job": "UPDATE_SHEET",
            "payload": {
                "row": int(rownum),
                "colunas": [coluna],
                "novos_valores": [str(valor)]
            }
        }
        self.r_filas.rpush(self.fila_resultados, json.dumps(job_writer))
        logger.debug(f"Enviado para Writer: Linha {rownum} | Coluna '{coluna}' | Valor '{valor}'")


    # -------------------------
    # Helpers Reutilizados
    # -------------------------
    def _parse_datetime(self, s: str) -> Optional[datetime]:
        if not s: return None
        s = s.strip()
        try:
            if "T" in s: return datetime.fromisoformat(s)
            return datetime.strptime(s, "%d/%m/%Y %H:%M")
        except Exception:
            try: return datetime.strptime(s, "%d/%m/%Y %H:%M:%S")
            except Exception: return None

    def map_location(self, name: str) -> Dict[str, Any]:
        """Busca os dados do local (CNPJ, IBGE) no DB 5 do Redis."""
        if not name: return {}
        
        bases_str = self.r_bases.get("cache:bases")
        if not bases_str:
            logger.error("Cache de bases não encontrado no Redis DB 5.")
            return {}
        
        try:
            locations_records = json.loads(bases_str)
        except Exception as e:
            logger.error(f"Erro ao ler cache de bases: {e}")
            return {}

        name_norm = str(name).strip().lower()
        
        for rec in locations_records:
            base = str(rec.get("BASE", "")).strip()
            if base and base.lower() == name_norm:
                try: ibge = int(rec.get("IBGE") or 0)
                except Exception: ibge = 0
                return {
                    "CNPJ": str(rec.get("CNPJ", "")).strip().replace(".", "").replace("-", "").replace("/", ""),
                    "CodIBGECidade": ibge,
                    "Razao": base,
                    "Fantasia": base,
                    "Cidade": rec.get("CIDADE", ""),
                    "Endereco": f"{rec.get('CIDADE','')}-{rec.get('UF','')}"
                }

        clean_name = name_norm.replace("lm hub_", "").replace("soc_", "")
        for rec in locations_records:
            base = str(rec.get("BASE", "")).strip()
            base_norm = base.lower().replace("lm hub_", "").replace("soc_", "")
            if base_norm == clean_name:
                return self.map_location(base)
                
        return {}


    # -------------------------
    # Construtores de Payloads
    # -------------------------
    def build_payload_pre_sm(self, row: Dict[str, Any]):
        id_3zx = row.get("ID 3ZX")
        rownum = row.get("original_row_number")

        origem = self.map_location(row.get("Origem", ""))
        destino = self.map_location(row.get("Destino", ""))

        if not origem.get("CodIBGECidade") or not origem.get("CNPJ"):
            self.enviar_update_writer(rownum, "PRÉ SM", "ERRO: Origem não registrada na base")
            return None

        if not destino.get("CodIBGECidade") or not destino.get("CNPJ"):
            self.enviar_update_writer(rownum, "PRÉ SM", "ERRO: Destino não registrado na base")
            return None

        eta_origem = self._parse_datetime(row.get("ETA Origem", ""))
        eta_destino = self._parse_datetime(row.get("ETA Destino", ""))
        cpt_origem = self._parse_datetime(row.get("CPT", ""))

        if not eta_origem or not eta_destino or not cpt_origem:
            self.enviar_update_writer(rownum, "PRÉ SM", "ERRO: Datas inválidas")
            return None

        detalhe = {
            "ColetasEntregas": [
                {
                    "Tipo": "COLETA", "CodIBGECidade": origem.get("CodIBGECidade"),
                    "Cliente": { "Codigo": 0, "Razao": origem.get("Razao"), "Fantasia": origem.get("Fantasia"), "CNPJ": origem.get("CNPJ"), "CodIBGECidade": origem.get("CodIBGECidade"), "Cidade": origem.get("Cidade"), "Endereco": origem.get("Endereco", ""), "Numero": "", "Complemento": "", "Bairro": "", "Latitude": "", "Longitude": "" },
                    "DataHoraChegada": eta_origem.isoformat(), "DataHoraSaida": cpt_origem.isoformat(), "Observacao": "", "Produtos": [{"CodProduto": "2134", "Produto": "00 - PRODUTOS DIVERSOS", "Valor": 1000000}]
                },
                {
                    "Tipo": "ENTREGA", "CodIBGECidade": destino.get("CodIBGECidade"),
                    "Cliente": { "Codigo": 0, "Razao": destino.get("Razao"), "Fantasia": destino.get("Fantasia"), "CNPJ": destino.get("CNPJ"), "CodIBGECidade": destino.get("CodIBGECidade"), "Cidade": destino.get("Cidade"), "Endereco": destino.get("Endereco", ""), "Numero": "", "Complemento": "", "Bairro": "", "Latitude": "", "Longitude": "" },
                    "DataHoraChegada": eta_destino.isoformat(), "DataHoraSaida": (eta_destino + timedelta(hours=2)).isoformat(), "Observacao": "", "Produtos": []
                }
            ]
        }

        payload = {
            "id": id_3zx,
            "PreSM": {
                "Engate": { "CodFilial": "007576", "PlacaVeiculo": row.get("Placa"), "VincVeiculo": "A", "CodPerfilSeguranca": 15379, "CPFMotorista1": str(row.get("CPF", "")).replace(".", "").replace("-", "").strip(), "VincMotorista1": "A", "PlacaCarreta1": row.get("Placa 2"), "VincCarreta1": "A" },
                "Detalhamento": detalhe, "Rota": {"CodRota": 0}, "LiberacaoEngate": {"SolicitarPesquisa": "NAO"}
            }
        }
        return payload

    def build_payload_efetivacao(self, row: Dict[str, Any]):
        codigo_pre_sm = row.get("PRÉ SM")
        return {
            "id": row.get("ID 3ZX"), 
            "PreSM": int(codigo_pre_sm) if str(codigo_pre_sm).isdigit() else codigo_pre_sm
        }


    # -------------------------
    # Processadores Unitários 
    # -------------------------
    def processar_single_pre_sm(self, row: Dict[str, Any]):
        logger.info(f"[PRÉ-SM] Processando Job para ID: {row.get('ID 3ZX')}")
        payload = self.build_payload_pre_sm(row)
        if not payload: return 

        resp = self.api.criar_lote([payload])
        self._tratar_resposta_api(resp, row, "PRÉ SM")

    def processar_single_efetivacao(self, row: Dict[str, Any]):
        logger.info(f"[EFETIVAÇÃO] Processando Job para ID: {row.get('ID 3ZX')}")
        payload = self.build_payload_efetivacao(row)
        
        resp = self.api_efetivacao.efetivar_lote([payload])
        self._tratar_resposta_api(resp, row, "SM EFET.")

    def _tratar_resposta_api(self, resp, row: Dict[str, Any], target_col: str):
        if not getattr(resp, "ok", False):
            logger.error(f"[API] Erro Http: {getattr(resp, 'status_code', '??')}")
            return
        
        resp_data = resp.json()
        if not resp_data: return
        job = resp_data[0] 
        
        id_3zx = job.get("id") or row.get("ID 3ZX")
        rownum = row.get("original_row_number")
        job_id = job.get("job_id") or f"NO_JOB_{id_3zx}"
        job_type = job.get("type", "criar_pre_sm" if target_col == "PRÉ SM" else "efetivar_sm")

        if job.get("status") == "accepted" or job.get("sucesso"):
            # Tudo certo. Anota no histórico que tá processando. O Poller resolve o resto.
            self.history.add_job(id_3zx, job_id, rownum, job_type)
            logger.info(f"Job {job_id} ({job_type}) enviado para API com sucesso. Aguardando Poller verificar status.")
        else:
            # Erro síncrono (ex: CPF inválido retornado na hora). Anota erro e manda o Writer preencher.
            err = job.get("erro") or str(job)
            self.enviar_update_writer(rownum, target_col, f"ERRO: {err}")
            self.history.add_job(id_3zx, job_id, rownum, job_type)
            self.history.update_job_status(id_3zx, job_id, rownum, status="ERROR", error=err)
            logger.warning(f"Job {job_type} recusado imediatamente pela API: {err}")


    # -------------------------
    # Loop Principal (Consumo da Fila)
    # -------------------------
    def iniciar_consumo(self):
        logger.info("="*60)
        logger.info(f"Worker API (Músculo) iniciado.")
        logger.info(f"Ouvindo filas: {self.q_pre_sm} e {self.q_efetivacao}")
        logger.info("="*60)

        while True:
            try:
                # Ouve filas bloqueando infinitamente (timeout=0) até chegar trabalho.
                item = self.r_filas.blpop([self.q_pre_sm, self.q_efetivacao], timeout=0)

                if item:
                    fila_origem, payload_str = item
                    job_data = json.loads(payload_str)
                    linha_planilha = job_data.get('data', {})
                    
                    if fila_origem == self.q_pre_sm:
                        self.processar_single_pre_sm(linha_planilha)
                    elif fila_origem == self.q_efetivacao:
                        self.processar_single_efetivacao(linha_planilha)

            except json.JSONDecodeError:
                logger.error("Erro ao decodificar JSON da fila do Redis.")
            except Exception as e:
                logger.error(f"[WORKER SM] Erro crítico no loop de consumo: {e}")
                time.sleep(5) 


# ===================================================================
# FUNÇÃO DE ENTRADA EXPORTADA
# ===================================================================
def fluxo_sm_worker(config: Dict[str, Any]):
    worker = WorkerSM(config)
    worker.iniciar_consumo()