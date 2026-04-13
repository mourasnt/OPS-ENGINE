"""
Sistema de display de status em tempo real.
Status é atualizado a cada 5 segundos em uma linha única (funciona em Docker).
Apenas logs IMPORTANTES são mostrados.
"""
import threading
import time
import sys
from loguru import logger
from typing import Dict, Any

class StatusDisplay:
    """
    Gerencia display de status em tempo real na mesma linha.
    Funciona em qualquer ambiente, incluindo Docker/Portainer.
    Totalmente dinâmico e mapeado.
    """
    
    def __init__(self, redis_client, queues_to_monitor: Dict[str, str], update_interval: int = 5):
        """
        Args:
            redis_client: Cliente Redis para contar jobs
            queues_to_monitor: Dict mapeando { "nome_do_fluxo": "nome_da_chave_no_redis" }
            update_interval: Intervalo em segundos para atualizar status (padrão: 5s)
        """
        self.redis_client = redis_client
        self.queues_to_monitor = queues_to_monitor
        self.update_interval = update_interval
        self.running = False
        self.monitor_thread = None
        
        # Estado compartilhado (thread-safe)
        self.lock = threading.Lock()
        
        # Inicializa dicionários dinamicamente com base nas filas passadas
        self.threads_status: Dict[str, int] = {k: 0 for k in self.queues_to_monitor.keys()}
        self.jobs_pending: Dict[str, int] = {k: 0 for k in self.queues_to_monitor.keys()}
    
    def atualizar_threads(self, tipo_job: str, quantidade: int):
        """Atualiza quantidade de threads ativas de um tipo específico."""
        with self.lock:
            # Se vier um job não mapeado, adiciona dinamicamente
            if tipo_job not in self.threads_status:
                self.threads_status[tipo_job] = 0
                self.jobs_pending[tipo_job] = 0
            
            self.threads_status[tipo_job] = quantidade
    
    def _formatar_status_linha(self) -> str:
        """Formata o status em UMA ÚNICA linha (para atualizar com \r)."""
        with self.lock:
            partes = []
            for nome in self.threads_status.keys():
                t = self.threads_status.get(nome, 0)
                j = self.jobs_pending.get(nome, 0)
                
                # Abrevia o nome para caber bonitinho na tela do Docker (Ex: "conferencia" vira "Conf")
                nome_abrev = nome[:4].capitalize()
                partes.append(f"{nome_abrev}: {t}🧵 ({j}📦)")
        
        # Junta tudo e coloca um padding de espaços para apagar rastros anteriores
        linha = "[STATUS] " + " | ".join(partes)
        return f"{linha:<120}"
    
    def _formatar_status_caixa(self) -> str:
        """Formata o status inicial em forma de caixa profissional para os logs."""
        with self.lock:
            linhas_box = [
                "\n╔════════════════════════════════════════════════════════════╗",
                "║ 🚀 OPS ENGINE - MONITOR DE STATUS EM TEMPO REAL            ║",
                "╠════════════════════════════════════════════════════════════╣"
            ]
            
            for nome in self.threads_status.keys():
                t = self.threads_status.get(nome, 0)
                j = self.jobs_pending.get(nome, 0)
                
                # Deixa o nome elegante (ex: "gerenciamento_risco" -> "Gerenciamento R")
                nome_display = nome.replace("_", " ").title()[:15]
                
                linhas_box.append(f"║ 👷 {nome_display:<15}: {t:>2} thread(s) │ 📦 Pendentes: {j:<12} ║")
            
            linhas_box.append("╚════════════════════════════════════════════════════════════╝\n")
            return "\n".join(linhas_box)
    
    def _monitorar_status(self):
        """Loop que atualiza o status periodicamente na mesma linha."""
        while self.running:
            try:
                # Atualiza contadores de jobs buscando no Redis
                with self.lock:
                    for nome, fila in self.queues_to_monitor.items():
                        try:
                            self.jobs_pending[nome] = self.redis_client.llen(fila)
                        except Exception:
                            self.jobs_pending[nome] = 0
                
                # Escreve status na mesma linha usando \r (carriage return)
                linha_status = self._formatar_status_linha()
                sys.stderr.write(f"\r{linha_status}")
                sys.stderr.flush()
                
                time.sleep(self.update_interval)
                
            except Exception as e:
                logger.error(f"Erro no monitor de status: {e}")
                time.sleep(self.update_interval)
    
    def iniciar(self):
        """Inicia o display de status em background."""
        self.running = True
        sys.stderr.write(self._formatar_status_caixa())
        sys.stderr.flush()
        
        self.monitor_thread = threading.Thread(
            target=self._monitorar_status,
            daemon=True,
            name="StatusDisplay"
        )
        self.monitor_thread.start()
        logger.info("Painel de Status Dinâmico iniciado.")
    
    def parar(self):
        """Para o display de status."""
        self.running = False
        if self.monitor_thread:
            self.monitor_thread.join(timeout=2)
        sys.stderr.write("\r" + " " * 120 + "\r")
        sys.stderr.flush()
        logger.info("Painel de Status parado.")
    
    def get_resumo_json(self) -> Dict[str, Any]:
        """Retorna resumo em formato JSON (útil para APIs de saúde)."""
        with self.lock:
            return {
                "threads": self.threads_status.copy(),
                "jobs_pendentes": self.jobs_pending.copy(),
                "total_threads": sum(self.threads_status.values()),
                "total_jobs": sum(self.jobs_pending.values())
            }