"""Tests for the sliding-window rate limiter."""

import asyncio
import types

import pytest

import src.utils.rate_limiter as rate_limiter_module
from src.utils.rate_limiter import RateLimiter


class _FakeClock:
    """可手动推进的时钟，避免测试依赖真实时间导致不稳定."""

    def __init__(self, now: float = 1000.0):
        self.now = now

    def time(self) -> float:
        """返回当前假时间."""
        return self.now

    def advance(self, seconds: float) -> None:
        """把假时间向前推进指定秒数."""
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    """替换模块内 time 为可控假时钟，返回该时钟供用例推进时间."""
    fake = _FakeClock()
    monkeypatch.setattr(
        rate_limiter_module, "time", types.SimpleNamespace(time=fake.time)
    )
    return fake


@pytest.mark.asyncio
async def test_first_request_is_allowed(clock):
    """窗口内首次请求应放行，且无需等待."""
    limiter = RateLimiter(max_requests=2)

    allowed, retry_after = await limiter.is_allowed()

    assert allowed is True
    assert retry_after == 0


@pytest.mark.asyncio
async def test_request_over_limit_is_rejected(clock):
    """达到阈值后的请求应被拦截，并给出到窗口释放的等待秒数."""
    limiter = RateLimiter(max_requests=2, window_seconds=60)

    assert (await limiter.is_allowed())[0] is True
    assert (await limiter.is_allowed())[0] is True

    allowed, retry_after = await limiter.is_allowed()

    assert allowed is False
    assert retry_after == 60


@pytest.mark.asyncio
async def test_window_expiry_restores_quota(clock):
    """窗口过期后，旧时间戳应被清理并恢复放行."""
    limiter = RateLimiter(max_requests=1, window_seconds=60)
    assert (await limiter.is_allowed())[0] is True
    assert (await limiter.is_allowed())[0] is False

    clock.advance(61)

    allowed, retry_after = await limiter.is_allowed()
    assert allowed is True
    assert retry_after == 0


@pytest.mark.asyncio
async def test_get_usage_prunes_expired_entries(clock):
    """get_usage 应只统计窗口内的请求."""
    limiter = RateLimiter(max_requests=3, window_seconds=60)
    await limiter.is_allowed()
    await limiter.is_allowed()

    assert limiter.get_usage() == (2, 3)

    clock.advance(61)

    assert limiter.get_usage() == (0, 3)


@pytest.mark.asyncio
async def test_non_positive_limit_always_allows(clock):
    """max_requests <= 0 表示不限流，连续请求都应放行且不计数."""
    limiter = RateLimiter(max_requests=0)

    for _ in range(5):
        assert await limiter.is_allowed() == (True, 0)

    assert limiter.get_usage() == (0, 0)


@pytest.mark.asyncio
async def test_concurrent_requests_do_not_exceed_limit(clock):
    """并发请求下计数不能超过阈值（锁保护）."""
    limiter = RateLimiter(max_requests=5, window_seconds=60)

    results = await asyncio.gather(*(limiter.is_allowed() for _ in range(20)))

    assert sum(1 for allowed, _ in results if allowed) == 5
    assert limiter.get_usage() == (5, 5)
