import json
import logging
from typing import Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)

class JobHistory:
    def __init__(self, redis_client):
        """
        Inicializa o histórico de jobs usando Redis.
        
        Args:
            redis_client: Instância do cliente Redis (preferencialmente apontando para o DB de filas).
        """
        self.redis = redis_client
        self.history_prefix = "sm_history:"
        self.pending_hash = "sm_pending_jobs"
        # Tempo de vida do histórico no Redis (ex: 15 dias para não estourar a memória)
        self.history_ttl = 60 * 60 * 24 * 15 

    def add_job(self, id_3zx: str, job_id: str, row: int, job_type: str = "criar_pre_sm"):
        """Adiciona um novo job ao histórico e marca como pendente."""
        job_entry = {
            'job_id': job_id,
            'job_type': job_type,
            'status': 'PENDING',
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat(),
            'error': None,
            'row': row
        }
        
        # 1. Atualiza o histórico completo dessa viagem
        key = f"{self.history_prefix}{id_3zx}"
        history_data = self.redis.get(key)
        
        if history_data:
            history = json.loads(history_data)
        else:
            history = {'jobs': []}
            
        history['jobs'].append(job_entry)
        
        # Salva o histórico e renova a data de expiração
        self.redis.setex(key, self.history_ttl, json.dumps(history))
        
        # 2. Adiciona ao Hash de controle de pendentes (Para busca O(1) rápida)
        pending_data = {
            'job_id': job_id,
            'row': row,
            'job_type': job_type
        }
        self.redis.hset(self.pending_hash, id_3zx, json.dumps(pending_data))

    def update_job_status(self, id_3zx: str, job_id: str, row: int, status: str, error: str = None):
        """Atualiza o status de um job no histórico e limpa dos pendentes se concluído."""
        key = f"{self.history_prefix}{id_3zx}"
        history_data = self.redis.get(key)
        
        if not history_data:
            logger.warning(f"ID 3ZX não encontrado no histórico do Redis: {id_3zx}")
            return
            
        history = json.loads(history_data)
        updated = False
        
        for job in history['jobs']:
            if job['job_id'] == job_id:
                job['status'] = status
                job['row'] = row
                job['updated_at'] = datetime.now().isoformat()
                if error:
                    job['error'] = error
                updated = True
                break
                
        if updated:
            self.redis.setex(key, self.history_ttl, json.dumps(history))
        else:
            logger.warning(f"Job ID não encontrado para ID 3ZX {id_3zx}: {job_id}")
            
        # 3. Se o job não for mais PENDING, removemos ele do Hash de buscas rápidas
        if status != 'PENDING':
            pending_str = self.redis.hget(self.pending_hash, id_3zx)
            if pending_str:
                pending_data = json.loads(pending_str)
                # Garante que só deleta se for o mesmo job_id
                if pending_data.get('job_id') == job_id:
                    self.redis.hdel(self.pending_hash, id_3zx)

    def get_job_status(self, id_3zx: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Retorna o status atual de um job específico."""
        key = f"{self.history_prefix}{id_3zx}"
        history_data = self.redis.get(key)
        
        if history_data:
            history = json.loads(history_data)
            for job in history['jobs']:
                if job['job_id'] == job_id:
                    return job
        return None

    def get_pending_jobs(self) -> Dict[str, Dict[str, Any]]:
        """
        Retorna todos os jobs pendentes de forma muito rápida lendo o Hash.
        Returns: Dict mapping ID 3ZX -> dados do job (job_id, row, job_type)
        """
        # HGETALL é super rápido no Redis e não precisa varrer históricos antigos
        pending_raw = self.redis.hgetall(self.pending_hash)
        pending = {}
        
        for id_3zx, data_str in pending_raw.items():
            try:
                pending[id_3zx] = json.loads(data_str)
            except json.JSONDecodeError:
                continue
                
        return pending

    def get_latest_status(self, id_3zx: str) -> Optional[Dict[str, Any]]:
        """Retorna o status do job mais recente para um ID 3ZX."""
        key = f"{self.history_prefix}{id_3zx}"
        history_data = self.redis.get(key)
        
        if history_data:
            history = json.loads(history_data)
            if history['jobs']:
                return history['jobs'][-1]
        return None