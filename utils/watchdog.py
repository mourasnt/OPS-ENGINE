"""
OPS ENGINE - Watchdog de Travamentos

Monitora jobs em execução e detecta travamentos ou loops infinitos.
Se um job exceder o tempo máximo permitido, o watchdog emite alertas
e sinaliza o encerramento da thread problemática.
"""

import threading
import time
import json
import traceback
from typing import Dict
from datetime import datetime
import redis
from loguru import logger


class JobWatchdog:
    """
    Monitora jobs RPA (Web e API) e detecta travamentos.
    """
    
    def __init__(
        self,
        redis_client: redis.Redis,
        max_job_duration: int = 300,   # 5 minutos
        check_interval: int = 30,      # Verificar a cada 30 segundos
        clear_on_start: bool = True    # Limpa jobs fantasmas ao iniciar a OPS_ENGINE
    ):
        self.redis_client = redis_client
        self.max_job_duration = max_job_duration
        self.check_interval = check_interval
        
        # Dicionário local em memória
        self.jobs_em_progresso: Dict[str, dict] = {}
        self.lock = threading.Lock()
        
        self.running = False
        self.thread_monitor = None

        # Limpa o estado antigo do Redis para evitar falsos positivos de crashes anteriores
        if clear_on_start:
            try:
                self.redis_client.delete("watchdog:jobs_em_progresso")
                self.redis_client.delete("watchdog:kill_workers")
                logger.debug("[Watchdog] Cache de travamentos limpo com sucesso.")
            except Exception as e:
                logger.error(f"[Watchdog] Erro ao limpar cache de inicialização: {e}")

    def registrar_job(self, job_id: str, worker_id: str, tipo_job: str):
        """Registra um job como iniciado."""
        with self.lock:
            self.jobs_em_progresso[job_id] = {
                "inicio": datetime.now(),
                "worker_id": worker_id,
                "tipo": tipo_job,
                "duracao_segundos": 0
            }
        
        # Persiste no Redis (Útil caso o manager queira ler o estado global)
        try:
            payload = {
                "inicio": datetime.now().isoformat(),
                "worker_id": worker_id,
                "tipo": tipo_job
            }
            self.redis_client.hset("watchdog:jobs_em_progresso", job_id, json.dumps(payload))
        except Exception as e:
            logger.error(f"[Watchdog] Erro ao persistir job '{job_id}' no Redis: {e}")
    
    def finalizar_job(self, job_id: str):
        """Remove um job da lista de progresso (completado ou falhou)."""
        with self.lock:
            if job_id in self.jobs_em_progresso:
                duracao = (datetime.now() - self.jobs_em_progresso[job_id]["inicio"]).total_seconds()
                self.jobs_em_progresso[job_id]["duracao_segundos"] = duracao
                del self.jobs_em_progresso[job_id]
        
        try:
            self.redis_client.hdel("watchdog:jobs_em_progresso", job_id)
        except Exception as e:
            logger.error(f"[Watchdog] Erro ao remover job '{job_id}' do Redis: {e}")
    
    def detectar_travamentos(self) -> list:
        """Verifica quais jobs estão travados (excederam max_job_duration)."""
        agora = datetime.now()
        jobs_travados = []
        
        with self.lock:
            for job_id, info in self.jobs_em_progresso.items():
                duracao = (agora - info["inicio"]).total_seconds()
                
                if duracao > self.max_job_duration:
                    jobs_travados.append({
                        "job_id": job_id,
                        "worker_id": info["worker_id"],
                        "tipo": info["tipo"],
                        "duracao": duracao,
                        "inicio": info["inicio"]
                    })
        
        return jobs_travados
    
    def monitorar(self):
        """Loop principal do watchdog em background."""
        logger.info(f"[Watchdog] Ativo. Tolerância: {self.max_job_duration}s | Verificação: {self.check_interval}s")
        
        while self.running:
            try:
                time.sleep(self.check_interval)
                if not self.running:
                    break
                
                jobs_travados = self.detectar_travamentos()
                
                if jobs_travados:
                    for job in jobs_travados:
                        self._processar_job_travado(job)
                
            except Exception as e:
                logger.error(f"[Watchdog] Erro crítico no monitor: {e}\n{traceback.format_exc()}")
    
    def _processar_job_travado(self, job_info: dict):
        """Processa e sinaliza um job detectado como travado."""
        job_id = job_info["job_id"]
        duracao = job_info["duracao"]
        worker_id = job_info["worker_id"]
        tipo = job_info["tipo"]
        
        # Ícone dinâmico dependendo se é web ou api
        icone = "🌐" if worker_info.get("tipo", "") in ["conferencia", "emissao", "manifesto"] else "🔌"
        
        logger.critical(
            f"[Watchdog] 🚨 TRAVAMENTO DETECTADO!\n"
            f"  {icone} Worker: {worker_id} ({tipo})\n"
            f"  📦 Job ID: {job_id}\n"
            f"  ⏳ Duração: {duracao:.1f}s (Máximo era {self.max_job_duration}s)\n"
            f"  🕒 Iniciado em: {job_info['inicio'].strftime('%H:%M:%S')}"
        )
        
        # Registra o histórico de travamento no Redis
        try:
            travamento = {
                "job_id": job_id,
                "worker_id": worker_id,
                "tipo": tipo,
                "duracao_segundos": duracao,
                "detectado_em": datetime.now().isoformat(),
                "inicio": job_info["inicio"].isoformat()
            }
            self.redis_client.rpush("watchdog:jobs_travados", json.dumps(travamento))
        except Exception as e:
            logger.error(f"[Watchdog] Erro ao registrar histórico de travamento: {e}")
        
        # Envia Kill Signal
        try:
            kill_signal = json.dumps({
                "worker_id": worker_id,
                "tipo": tipo,
                "job_id": job_id,
                "timestamp": datetime.now().isoformat(),
                "motivo": "timeout_travamento"
            })
            self.redis_client.sadd("watchdog:kill_workers", kill_signal)
            logger.warning(f"[Watchdog] 💀 Sinal de encerramento enviado para thread {worker_id}")
        except Exception as e:
            logger.error(f"[Watchdog] Erro ao enviar kill signal: {e}")
        
        # Remove do watchdog para não apitar duas vezes sobre o mesmo defunto
        self.finalizar_job(job_id)
    
    def iniciar(self):
        """Inicia a thread do watchdog."""
        if self.running:
            return
        
        self.running = True
        self.thread_monitor = threading.Thread(
            target=self.monitorar,
            daemon=True,
            name="OPS-Watchdog"
        )
        self.thread_monitor.start()
    
    def parar(self):
        """Para graciosamente o watchdog."""
        self.running = False
        if self.thread_monitor:
            self.thread_monitor.join(timeout=2)
        logger.info("[Watchdog] Finalizado.")


class TimeoutDetector:
    """
    Detector de lentidão para etapas individuais do fluxo.
    Seguro para uso com Playwright (não cria threads paralelas).
    
    Uso:
        with TimeoutDetector("Buscando botão", max_seconds=10, job_id=lt):
            page.locator("button").click()
    """
    
    def __init__(self, etapa: str, max_seconds: int = 30, job_id: str = ""):
        self.etapa = etapa
        self.max_seconds = max_seconds
        self.job_id = job_id
        self.inicio = None
        self.fim = None
    
    def __enter__(self):
        self.inicio = time.time()
        logger.debug(f"⏱️ Iniciando: {self.etapa} (Lim: {self.max_seconds}s) [Job: {self.job_id}]")
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.fim = time.time()
        duracao = self.fim - self.inicio
        
        if exc_type:
            logger.error(f"❌ ERRO em '{self.etapa}' após {duracao:.1f}s: {exc_val}")
        elif duracao > self.max_seconds:
            logger.warning(f"⚠️ LENTIDÃO: '{self.etapa}' levou {duracao:.1f}s (Lim: {self.max_seconds}s) [Job: {self.job_id}]")
        else:
            logger.debug(f"✓ OK: '{self.etapa}' levou {duracao:.1f}s")
            
        return False  # Permite que exceções quebrem o fluxo nativamente