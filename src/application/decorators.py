import asyncio
import logging
from functools import wraps
from typing import Callable, Any

logger = logging.getLogger(__name__)

class ConcurrencyException(Exception):
    """Raised when an optimistic concurrency check fails."""
    pass

def retry_on_concurrency(max_retries: int = 3, base_delay: float = 0.1):
    """
    Auto-Retry Use Case Decorator to intercept ConcurrencyException failures
    and automatically retry command execution with exponential backoff.
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            retries = 0
            while True:
                try:
                    return await func(*args, **kwargs)
                except ConcurrencyException as e:
                    retries += 1
                    if retries > max_retries:
                        logger.error(f"Concurrency retry limit exceeded for {func.__name__}")
                        raise e
                    
                    delay = base_delay * (2 ** (retries - 1))
                    logger.warning(f"Concurrency conflict in {func.__name__}. Retrying in {delay}s (Attempt {retries}/{max_retries})...")
                    await asyncio.sleep(delay)
        return wrapper
    return decorator
