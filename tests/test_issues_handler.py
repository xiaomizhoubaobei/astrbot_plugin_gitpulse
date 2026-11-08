"""Tests for the issues event handler."""

import pytest

from src.handlers.issues_handler import handle_issues_event


class _ExplodingDict(dict):
    """构造一个一读就抛异常的 payload，用于覆盖异常兜底分支。"""

    def get(self, *args, **kwargs):
        raise RuntimeError("payload boom")


def _make_payload(**overrides):
    """构造 GitHub issues 事件的典型 payload。"""
    payload = {
        "action": "opened",
        "issue": {
            "number": 42,
            "title": "发现一个缺陷",
            "html_url": "https://github.com/owner/repo/issues/42",
        },
        "sender": {"login": "alice"},
        "repository": {"full_name": "owner/repo"},
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_issues_event_formats_message():
    """正常 issues 事件应把动作、作者、仓库与编号渲染成消息。"""
    message = await handle_issues_event(_make_payload(), context=None)

    assert message is not None
    assert "🆕 GitHub Issue Event" in message
    assert "alice opened issue in owner/repo" in message
    assert "Issue #42: 发现一个缺陷" in message
    assert "https://github.com/owner/repo/issues/42" in message


@pytest.mark.asyncio
async def test_issues_event_unknown_action_falls_back_to_default_emoji():
    """未映射的动作应回落默认 emoji，同时保留原始动作文本。"""
    message = await handle_issues_event(_make_payload(action="labeled"), context=None)

    assert message is not None
    assert "📝 GitHub Issue Event" in message
    assert "alice labeled issue in owner/repo" in message


@pytest.mark.asyncio
async def test_issues_event_uses_defaults_for_missing_fields():
    """字段缺失时应使用兜底值，而不是抛异常。"""
    message = await handle_issues_event({}, context=None)

    assert message is not None
    assert "📝 GitHub Issue Event" in message
    assert "Unknown unknown issue in Unknown" in message
    assert "Issue #0: No title" in message


@pytest.mark.asyncio
async def test_issues_event_exception_returns_none():
    """payload 结构异常时应记录错误并返回 None。"""
    assert await handle_issues_event(_ExplodingDict(), context=None) is None
