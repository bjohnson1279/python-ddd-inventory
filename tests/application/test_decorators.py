import pytest
import asyncio
from unittest.mock import patch, MagicMock

from src.application.decorators import retry_on_concurrency, ConcurrencyException

pytestmark = pytest.mark.asyncio

async def test_retry_on_concurrency_success_first_try():
    mock_func = MagicMock(return_value="success")

    @retry_on_concurrency(max_retries=3, base_delay=0.1)
    async def decorated_func(*args, **kwargs):
        return mock_func(*args, **kwargs)

    result = await decorated_func("test_arg")

    assert result == "success"
    mock_func.assert_called_once_with("test_arg")

async def test_retry_on_concurrency_success_after_retries():
    # It will raise ConcurrencyException twice, then succeed
    mock_func = MagicMock(side_effect=[ConcurrencyException(), ConcurrencyException(), "success"])

    @retry_on_concurrency(max_retries=3, base_delay=0.1)
    async def decorated_func(*args, **kwargs):
        return mock_func(*args, **kwargs)

    with patch("asyncio.sleep") as mock_sleep:
        result = await decorated_func("test_arg")

    assert result == "success"
    assert mock_func.call_count == 3
    assert mock_sleep.call_count == 2
    # Check backoff delays
    mock_sleep.assert_any_call(0.1) # First retry delay (0.1 * 2^0)
    mock_sleep.assert_any_call(0.2) # Second retry delay (0.1 * 2^1)

async def test_retry_on_concurrency_max_retries_exceeded():
    # Will always raise ConcurrencyException
    mock_func = MagicMock(side_effect=ConcurrencyException())

    @retry_on_concurrency(max_retries=2, base_delay=0.1)
    async def decorated_func(*args, **kwargs):
        return mock_func(*args, **kwargs)

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(ConcurrencyException):
            await decorated_func("test_arg")

    # Original call + 2 retries = 3 calls
    assert mock_func.call_count == 3
    assert mock_sleep.call_count == 2

async def test_retry_on_concurrency_propagates_non_concurrency_exception():
    class OtherException(Exception):
        pass

    # Will raise OtherException on first call
    mock_func = MagicMock(side_effect=OtherException("Some other error"))

    @retry_on_concurrency(max_retries=3, base_delay=0.1)
    async def decorated_func(*args, **kwargs):
        return mock_func(*args, **kwargs)

    with patch("asyncio.sleep") as mock_sleep:
        with pytest.raises(OtherException, match="Some other error"):
            await decorated_func("test_arg")

    # Should fail immediately without retrying
    assert mock_func.call_count == 1
    assert mock_sleep.call_count == 0

async def test_retry_on_concurrency_exponential_backoff_logic():
    # Will fail 3 times, succeed on 4th
    mock_func = MagicMock(side_effect=[
        ConcurrencyException(),
        ConcurrencyException(),
        ConcurrencyException(),
        "success"
    ])

    @retry_on_concurrency(max_retries=3, base_delay=0.5)
    async def decorated_func(*args, **kwargs):
        return mock_func(*args, **kwargs)

    with patch("asyncio.sleep") as mock_sleep:
        result = await decorated_func("test_arg")

    assert result == "success"
    assert mock_func.call_count == 4
    assert mock_sleep.call_count == 3

    # Check backoff delays: base_delay * (2 ** (retries - 1))
    # attempt 1: retries=1 -> delay = 0.5 * 2^0 = 0.5
    # attempt 2: retries=2 -> delay = 0.5 * 2^1 = 1.0
    # attempt 3: retries=3 -> delay = 0.5 * 2^2 = 2.0
    mock_sleep.assert_any_call(0.5)
    mock_sleep.assert_any_call(1.0)
    mock_sleep.assert_any_call(2.0)
