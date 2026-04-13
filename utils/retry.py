"""
Retry utilities unificado para OPS Engine.
Usa a biblioteca tenacity para retries robustos.
"""
import time
import functools
from typing import Callable, Type, Tuple, Optional

import redis.exceptions
import requests.exceptions
from loguru import logger


# =========================================================================
# REPOLY FOR REDIS
# =========================================================================
def retry_on_redis(tries: int = 5, delay: float = 1.0, backoff: float = 2.0, max_delay: float = 30.0):
    """
    Decorador para retries em operações Redis.
    
    Args:
        tries: Número máximo de tentativas
        delay: Delay inicial em segundos
        backoff: Multiplicador de backoff exponencial
        max_delay: Delay máximo permitido
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = tries, delay
            last_exc = None
            
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
                    last_exc = e
                    logger.warning(f"Redis error: {e}. Retrying in {mdelay}s (tries: {mtries-1})")
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay = min(mdelay * backoff, max_delay)
            
            logger.error(f"All retries failed for Redis operation: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


# =========================================================================
# RETRY FOR REQUESTS (API)
# =========================================================================
def retry_on_api(tries: int = 3, delay: float = 2.0, backoff: float = 2.0, max_delay: float = 60.0):
    """
    Decorador para retries em chamadas de API.
    
    Args:
        tries: Número máximo de tentativas
        delay: Delay inicial em segundos
        backoff: Multiplicador de backoff exponencial
        max_delay: Delay máximo permitido
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = tries, delay
            last_exc = None
            
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except (
                    requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout,
                    requests.exceptions.HTTPError
                ) as e:
                    last_exc = e
                    status = getattr(e, 'response', None)
                    # Não retenta em erros 4xx (cliente)
                    if status and 400 <= status.status_code < 500:
                        raise
                    
                    logger.warning(f"API error: {e}. Retrying in {mdelay}s (tries: {mtries-1})")
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay = min(mdelay * backoff, max_delay)
            
            logger.error(f"All retries failed for API operation: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


# =========================================================================
# RETRY FOR GSPREAD
# =========================================================================
def retry_on_gspread(tries: int = 4, delay: float = 2.0, backoff: float = 2.0, max_delay: float = 30.0):
    """
    Decorador para retries em operações do Google Sheets (gspread).
    
    Args:
        tries: Número máximo de tentativas
        delay: Delay inicial em segundos
        backoff: Multiplicador de backoff exponencial
        max_delay: Delay máximo permitido
    """
    try:
        import gspread.exceptions
        gspread_exceptions = (gspread.exceptions.APIError,)
    except ImportError:
        gspread_exceptions = (Exception,)
    
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = tries, delay
            last_exc = None
            
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except gspread_exceptions as e:
                    last_exc = e
                    logger.warning(f"GSpread error: {e}. Retrying in {mdelay}s (tries: {mtries-1})")
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay = min(mdelay * backoff, max_delay)
            
            logger.error(f"All retries failed for GSpread operation: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


# =========================================================================
# GENERIC RETRY (mantém compatibilidade com código anterior)
# =========================================================================
def retry(
    on_exception: Tuple[Type[Exception], ...] = (Exception,),
    tries: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    logger=None
):
    """Retry genérico com Tuple de exceções."""
    """
    Decorador genérico de retry (mantido para compatibilidade).
    
    Example:
        @retry((gspread.exceptions.APIError,), tries=5, delay=2)
        def call_api(...):
            ...
    """
    def decorator(func: Callable):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            mtries, mdelay = tries, delay
            last_exc = None
            
            while mtries > 0:
                try:
                    return func(*args, **kwargs)
                except on_exception as e:
                    last_exc = e
                    if logger:
                        logger.warning(f"Retryable: {e}. Retrying in {mdelay}s (tries: {mtries-1})")
                    time.sleep(mdelay)
                    mtries -= 1
                    mdelay *= backoff
            
            if logger:
                logger.error(f"All retries failed: {last_exc}")
            raise last_exc
        return wrapper
    return decorator


# =========================================================================
# BACKOFF ESTRATEGIAS PRÉ-DEFINIDAS
# =========================================================================
class ExponentialBackoff:
    """Classe para gerenciar estratégias de backoff."""
    
    def __init__(self, initial: float = 1.0, max_delay: float = 60.0, multiplier: float = 2.0):
        self.initial = initial
        self.max_delay = max_delay
        self.multiplier = multiplier
        self.current = initial
    
    def reset(self):
        """Reseta para o delay inicial."""
        self.current = self.initial
    
    def next(self) -> float:
        """Retorna o próximo delay e incrementa."""
        delay = self.current
        self.current = min(self.current * self.multiplier, self.max_delay)
        return delay
    
    def __enter__(self):
        self.reset()
        return self
    
    def __exit__(self, *args):
        pass


class LinearBackoff:
    """Classe para backoff linear."""
    
    def __init__(self, step: float = 5.0, max_delay: float = 60.0):
        self.step = step
        self.max_delay = max_delay
        self.current = step
    
    def reset(self):
        self.current = self.step
    
    def next(self) -> float:
        delay = self.current
        self.current = min(self.current + self.step, self.max_delay)
        return delay
    
    def __enter__(self):
        self.reset()
        return self
    
    def __exit__(self, *args):
        pass