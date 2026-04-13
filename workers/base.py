"""
Base Worker Abstrato para OPS Engine.

Fornece estrutura padrão para todos os workers (WEB e API).
Elimina código duplicado em cada worker individual.
"""
import os
import json
import time
import threading
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
import redis
from loguru import logger

from utils.redis_client import get_redis
from utils.config import Config
from utils.timeouts import QUEUE_POP_TIMEOUT_SECONDS


class BaseWorker(ABC):
    """
    Classe abstrata base para workers.
    
    Fornece:
    - Conexão Redis automatizada
    - Loop de consumo de fila padronizado
    - Verificação de downscaling
    - Registro no watchdog
    - Limpeza de controle
    
    Subclasses devem implementar:
    - queue_name (propriedade)
    - control_set (propriedade)
    - _processar_job (método)
    """
    
    def __init__(self, config: Dict[str, Any], nome_worker: str):
        """
        Inicializa o worker.
        
        Args:
            config: Configuração global da aplicação
            nome_worker: Nome identificador (ex: "conferencia", "emissao")
        """
        self.config = config
        self.nome_worker = nome_worker
        self._thread_name = threading.current_thread().name
        
        # Inicialização de dependências
        self._init_redis_client()
        self._init_watchdog()
        self._init_pool_manager()
        
        logger.info(f"[{self.nome_worker}] Worker inicializado")

    def _init_redis_client(self):
        """Inicializa conexão Redis para filas."""
        cfg = Config()
        try:
            self.redis = get_redis(
                host=cfg.redis_host,
                port=cfg.redis_port,
                db=cfg.redis_db_fila
            )
            logger.debug(f"[{self.nome_worker}] Redis conectado")
        except Exception as e:
            logger.critical(f"[{self.nome_worker}] Falha ao conectar Redis: {e}")
            raise

    def _init_watchdog(self):
        """Inicializa referência ao watchdog."""
        self.watchdog = self.config.get('watchdog', None)

    def _init_pool_manager(self):
        """Inicializa referência ao pool manager."""
        self.pool_manager = self.config.get('thread_pool_manager', None)

    # =========================================================================
    # PROPRIEDADES ABSTRATAS (override em cada worker)
    # =========================================================================
    @property
    @abstractmethod
    def queue_name(self) -> str:
        """Nome da fila Redis para este worker."""
        pass

    @property
    @abstractmethod
    def control_set(self) -> str:
        """Nome do set de controle Redis para este worker."""
        pass

    @property
    def tipo_job(self) -> str:
        """Tipo de job para logging e watchdog (pode sobrescrever)."""
        return self.nome_worker

    # =========================================================================
    # LOOP PRINCIPAL
    # =========================================================================
    def executar(self):
        """
        Loop principal padronizado de consumo de jobs.
        
        Fluxo:
        1. Verifica downscaling
        2. Espera job na fila (blpop com timeout)
        3. Verifica kill signal ( watchdog)
        4. Processa job
        5. Limpa controle
        """
        logger.info(f"[{self.nome_worker}] Iniciando loop de consumo...")
        
        while True:
            # 1. Verifica se deve morir por downscaling
            if self._verificar_deve_morrer():
                logger.warning(f"[{self.nome_worker}] Downscaling detectado. Encerrando thread.")
                break
            
            # 2. Espera job na fila
            try:
                resultado = self.redis.blpop([self.queue_name], timeout=QUEUE_POP_TIMEOUT_SECONDS)
            except redis.exceptions.ConnectionError as e:
                logger.error(f"[{self.nome_worker}] Erro de conexão Redis: {e}. Reconnecting...")
                time.sleep(5)
                continue
            
            if resultado is None:
                logger.debug(f"[{self.nome_worker}] Timeout na fila. Continuando...")
                continue
            
            # 3. Processa job recebido
            _, job_json = resultado
            try:
                job = json.loads(job_json)
            except json.JSONDecodeError as e:
                logger.error(f"[{self.nome_worker}] Erro ao decodificar job: {e}")
                continue

            row = job.get('row')
            data = job.get('data', {})
            job_id = data.get('ID 3ZX') or data.get('N° Carga') or f"row-{row}"
            
            # 4. Registro watchdog
            if self.watchdog:
                self.watchdog.registrar_job(job_id, worker_id=self._thread_name, tipo_job=self.tipo_job)

            # 5. Processa o job
            numero_lt = data.get('N° Carga', 'DESCONHECIDO')
            logger.info(f"[{self.nome_worker}] Processando job: {numero_lt} (Linha {row})")
            
            try:
                self._processar_job(data, row)
                logger.debug(f"[{self.nome_worker}] Job {job_id} processado com sucesso")
            except Exception as e:
                logger.error(f"[{self.nome_worker}] Erro ao processar job {job_id}: {e}")
            finally:
                # 6. Limpa controle (remove cadeado)
                self._limpar_controle(data, job_id)
                
                # 7. Finaliza watchdog
                if self.watchdog:
                    self.watchdog.finalizar_job(job_id)

        logger.info(f"[{self.nome_worker}] Loop de consumo encerrado.")

    @abstractmethod
    def _processar_job(self, data: Dict[str, Any], row: int):
        """
        Processa um job específico.
        
        Este método deve ser sobrescrito por cada worker concreto.
        
        Args:
            data: Dados da linha da planilha
            row: Número da linha
            
        Raises:
            exception: Qualquer erro deve ser tratado e logado
        """
        pass

    def _limpar_controle(self, data: Dict[str, Any], job_id: str):
        """Remove o job do set de controle."""
        try:
            id_3zx = data.get('ID 3ZX', '').strip() or job_id
            if id_3zx:
                self.redis.srem(self.control_set, id_3zx)
                logger.debug(f"[{self.nome_worker}] Controle limpo: {id_3zx}")
        except Exception as e:
            logger.error(f"[{self.nome_worker}] Erro ao limpar controle: {e}")

    # =========================================================================
    # HELPERS
    # =========================================================================
    def _verificar_deve_morrer(self) -> bool:
        """Verifica se a thread foi marcada para morte por downscaling."""
        try:
            if self.pool_manager:
                return self.pool_manager.thread_deve_morrer(self.nome_worker)
        except Exception as e:
            logger.error(f"[{self.nome_worker}] Erro ao verificar downscaling: {e}")
        return False

    def _verificar_kill_signal(self, job_id: str) -> bool:
        """Verifica se há kill signal do watchdog para este job."""
        try:
            kill_signals = self.redis.smembers("watchdog:kill_workers")
            for signal_json in kill_signals:
                try:
                    signal = json.loads(signal_json)
                    if signal.get("job_id") == job_id:
                        self.redis.srem("watchdog:kill_workers", signal_json)
                        logger.warning(f"[{self.nome_worker}] Kill signal detectado para {job_id}")
                        return True
                except json.JSONDecodeError:
                    continue
        except Exception as e:
            logger.error(f"[{self.nome_worker}] Erro ao verificar kill signal: {e}")
        return False


class BaseWebWorker(BaseWorker):
    """
    Base Worker para automações que usam Playwright (WEB).
    
    Fornece además:
    - Página playwright
    - Login automático
    - Gestão de sessão
    """
    
    def __init__(self, config: Dict[str, Any], nome_worker: str, page, browser_context):
        super().__init__(config, nome_worker)
        self.page = page
        self.context = browser_context
    
    @property
    def tipo_job(self) -> str:
        return "web"


class BaseAPIWorker(BaseWorker):
    """
    Base Worker para automações que usam apenas API (sem browser).
    
    Fornece client de API pré-configurado.
    """
    
    @property
    def tipo_job(self) -> str:
        return "api"


def criar_worker_instance(
    worker_class: type,
    config: Dict[str, Any],
    nome_worker: str,
    **kwargs
) -> BaseWorker:
    """
    Factory para criar instâncias de worker.
    
    Args:
        worker_class: Classe do worker (subclasse de BaseWorker)
        config: Configuração global
        nome_worker: Nome do worker
        **kwargs: args adicionais para o worker
        
    Returns:
        Instância do worker
    """
    return worker_class(config, nome_worker, **kwargs)