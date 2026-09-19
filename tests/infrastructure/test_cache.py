import os
import pytest
from unittest.mock import patch, AsyncMock
import json
from src.infrastructure.cache import DistributedCache

@pytest.mark.asyncio
async def test_get_existing_key():
    with patch('src.infrastructure.cache.redis_client.get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = '{"data": "value"}'
        result = await DistributedCache.get('test_key')
        mock_get.assert_called_once_with('test_key')
        assert result == {"data": "value"}

@pytest.mark.asyncio
async def test_get_missing_key():
    with patch('src.infrastructure.cache.redis_client.get', new_callable=AsyncMock) as mock_get:
        mock_get.return_value = None
        result = await DistributedCache.get('test_key_missing')
        mock_get.assert_called_once_with('test_key_missing')
        assert result is None

@pytest.mark.asyncio
async def test_set():
    with patch('src.infrastructure.cache.redis_client.setex', new_callable=AsyncMock) as mock_setex:
        await DistributedCache.set('test_set_key', {'foo': 'bar'}, 3600)
        mock_setex.assert_called_once_with('test_set_key', 3600, '{"foo": "bar"}')

@pytest.mark.asyncio
async def test_invalidate():
    with patch('src.infrastructure.cache.redis_client.delete', new_callable=AsyncMock) as mock_delete:
        await DistributedCache.invalidate('test_inv_key')
        mock_delete.assert_called_once_with('test_inv_key')

@pytest.mark.asyncio
async def test_invalidate_pattern_with_keys():
    with patch('src.infrastructure.cache.redis_client.keys', new_callable=AsyncMock) as mock_keys, \
         patch('src.infrastructure.cache.redis_client.delete', new_callable=AsyncMock) as mock_delete:

        mock_keys.return_value = ['key1', 'key2']
        await DistributedCache.invalidate_pattern('pattern*')
        mock_keys.assert_called_once_with('pattern*')
        mock_delete.assert_called_once_with('key1', 'key2')

@pytest.mark.asyncio
async def test_invalidate_pattern_no_keys():
    with patch('src.infrastructure.cache.redis_client.keys', new_callable=AsyncMock) as mock_keys, \
         patch('src.infrastructure.cache.redis_client.delete', new_callable=AsyncMock) as mock_delete:

        mock_keys.return_value = []
        await DistributedCache.invalidate_pattern('pattern*')
        mock_keys.assert_called_once_with('pattern*')
        mock_delete.assert_not_called()

def test_cache_missing_redis_url():
    import subprocess
    import sys

    # Run the cache file in a subprocess with empty environment (except essential Windows system vars) to simulate missing REDIS_URL
    test_env = {k: v for k, v in os.environ.items() if k.upper() in ('SYSTEMROOT', 'WINDIR', 'SYSTEMDRIVE', 'PATH')}
    result = subprocess.run(
        [sys.executable, "-c", "import src.infrastructure.cache"],
        capture_output=True,
        text=True,
        env=test_env
    )

    assert result.returncode != 0
    assert "CRITICAL: REDIS_URL environment variable is missing!" in result.stderr
