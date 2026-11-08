"""Tests for the push event handler."""

import pytest

from src.handlers.push_handler import handle_push_event


class _ExplodingDict(dict):
    """构造一个一读就抛异常的 payload，用于覆盖异常兜底分支。"""

    def get(self, *args, **kwargs):
        raise RuntimeError("payload boom")


def _make_payload(**overrides):
    """构造 GitHub push 事件的典型 payload。"""
    payload = {
        "pusher": {"name": "alice"},
        "repository": {"full_name": "owner/repo"},
        "ref": "refs/heads/main",
        "commits": [
            {
                "id": "abcdef1234567890",
                "message": "feat: 新增功能",
                "url": "https://github.com/owner/repo/commit/abcdef1",
            }
        ],
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_push_event_formats_message():
    """正常 push 事件应把作者、仓库、分支与提交信息渲染成消息。"""
    payload = _make_payload()

    message = await handle_push_event(payload, context=None)

    assert message is not None
    assert "alice pushed to owner/repo" in message
    assert "🌿 Branch: main" in message
    assert "feat: 新增功能" in message
    assert "abcdef1" in message
    assert "https://github.com/owner/repo/commit/abcdef1" in message


@pytest.mark.asyncio
async def test_push_event_uses_last_commit_url_as_head():
    """多提交时汇总链接应指向本次推送的 head（commits 末尾）。"""
    commits = [
        {
            "id": "1111111aaaa",
            "message": "first",
            "url": "https://example.com/first",
        },
        {
            "id": "2222222bbbb",
            "message": "second",
            "url": "https://example.com/head",
        },
    ]

    message = await handle_push_event(_make_payload(commits=commits), context=None)

    assert message is not None
    assert "🔢 2 commits" in message
    assert "https://example.com/head" in message
    assert "https://example.com/first" not in message


@pytest.mark.asyncio
async def test_push_event_strips_refs_heads_prefix():
    """refs/heads/ 前缀应被剥离，标签等其它 ref 原样保留。"""
    message = await handle_push_event(
        _make_payload(ref="refs/heads/feature/demo"), context=None
    )
    assert message is not None
    assert "Branch: feature/demo" in message

    tag_message = await handle_push_event(
        _make_payload(ref="refs/tags/v1.0.0"), context=None
    )
    assert tag_message is not None
    assert "Branch: refs/tags/v1.0.0" in tag_message


@pytest.mark.asyncio
async def test_push_event_without_commits_returns_none():
    """没有提交（如删除分支）时不发送消息。"""
    assert await handle_push_event(_make_payload(commits=[]), context=None) is None
    assert await handle_push_event({"ref": "refs/heads/main"}, context=None) is None


@pytest.mark.asyncio
async def test_push_event_uses_defaults_for_missing_fields():
    """字段缺失时应使用兜底值，而不是抛异常。"""
    payload = {"commits": [{"id": "abcdef1234567890", "message": None}]}

    message = await handle_push_event(payload, context=None)

    assert message is not None
    assert "Unknown pushed to Unknown" in message
    assert "Branch: Unknown" in message
    assert "No message" in message


@pytest.mark.asyncio
async def test_push_event_exception_returns_none():
    """payload 结构异常时应记录错误并返回 None，不能让请求失败。"""
    assert await handle_push_event(_ExplodingDict(), context=None) is None
