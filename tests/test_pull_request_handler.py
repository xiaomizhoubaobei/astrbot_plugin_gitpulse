"""Tests for the pull request event handler."""

import pytest

from src.handlers.pull_request_handler import handle_pull_request_event


class _ExplodingDict(dict):
    """构造一个一读就抛异常的 payload，用于覆盖异常兜底分支。"""

    def get(self, *args, **kwargs):
        raise RuntimeError("payload boom")


def _make_payload(**overrides):
    """构造 GitHub pull_request 事件的典型 payload。"""
    payload = {
        "action": "opened",
        "pull_request": {
            "number": 7,
            "title": "feat: 新增功能",
            "html_url": "https://github.com/owner/repo/pull/7",
            "base": {"ref": "main"},
            "head": {"ref": "feature/demo"},
        },
        "sender": {"login": "alice"},
        "repository": {"full_name": "owner/repo"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_pull_request_event_formats_message():
    """正常 PR 事件应把动作、作者、仓库、分支与链接渲染成消息。"""
    message = await handle_pull_request_event(_make_payload(), context=None)

    assert message is not None
    assert "🆕 GitHub Pull Request Event" in message
    assert "alice opened PR in owner/repo" in message
    assert "PR #7: feat: 新增功能" in message
    assert "feature/demo → main" in message
    assert "https://github.com/owner/repo/pull/7" in message


@pytest.mark.asyncio
async def test_pull_request_event_preserves_action():
    """action 应原样透传给格式化层（含 synchronize 的 emoji 映射）。"""
    message = await handle_pull_request_event(
        _make_payload(action="synchronize"), context=None
    )

    assert message is not None
    assert "🔀 GitHub Pull Request Event" in message
    assert "alice synchronize PR in owner/repo" in message


@pytest.mark.asyncio
async def test_merged_pull_request_renders_as_closed():
    """GitHub 合并事件是 action=closed + merged=true，应渲染为 ✅ 已关闭."""
    payload = _make_payload(action="closed")
    payload["pull_request"]["merged"] = True

    message = await handle_pull_request_event(payload, context=None)

    assert message is not None
    assert "✅ GitHub Pull Request Event" in message
    assert "alice closed PR in owner/repo" in message


@pytest.mark.asyncio
async def test_pull_request_event_uses_defaults_for_missing_fields():
    """字段缺失时应使用兜底值，而不是抛异常。"""
    message = await handle_pull_request_event({}, context=None)

    assert message is not None
    assert "📝 GitHub Pull Request Event" in message
    assert "Unknown unknown PR in Unknown" in message
    assert "PR #0: No title" in message
    assert "unknown → unknown" in message


@pytest.mark.asyncio
async def test_pull_request_event_exception_returns_none():
    """payload 结构异常时应记录错误并返回 None。"""
    assert await handle_pull_request_event(_ExplodingDict(), context=None) is None
