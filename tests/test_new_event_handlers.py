"""Tests for release / workflow_run / star / issue_comment handlers."""

import pytest

from src.handlers.issue_comment_handler import handle_issue_comment_event
from src.handlers.release_handler import handle_release_event
from src.handlers.star_handler import handle_star_event
from src.handlers.workflow_run_handler import handle_workflow_run_event


class _ExplodingDict(dict):
    """构造一个一读就抛异常的 payload，用于覆盖异常兜底分支。"""

    def get(self, *args, **kwargs):
        raise RuntimeError("payload boom")


@pytest.mark.asyncio
async def test_release_handler_formats_message():
    """release payload 应提取标签、标题与链接."""
    payload = {
        "action": "published",
        "release": {
            "tag_name": "v1.2.3",
            "name": "Big Release",
            "html_url": "https://github.com/owner/repo/releases/tag/v1.2.3",
        },
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "alice"},
    }

    message = await handle_release_event(payload, context=None)

    assert message is not None
    assert "🚀 GitHub Release Event" in message
    assert "🏷️ v1.2.3: Big Release" in message


@pytest.mark.asyncio
async def test_release_handler_uses_defaults_for_missing_fields():
    """字段缺失时使用兜底值，而不是抛异常."""
    message = await handle_release_event({"action": "published"}, context=None)

    assert message is not None
    assert "Unknown published release in Unknown" in message
    assert "🏷️ untagged" in message


@pytest.mark.asyncio
async def test_workflow_run_handler_formats_completed_run():
    """completed 的 workflow_run 应渲染结论、分支与链接."""
    payload = {
        "action": "completed",
        "workflow_run": {
            "name": "CI",
            "conclusion": "failure",
            "head_branch": "main",
            "event": "push",
            "html_url": "https://github.com/owner/repo/actions/runs/1",
        },
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "alice"},
    }

    message = await handle_workflow_run_event(payload, context=None)

    assert message is not None
    assert message.startswith("❌ GitHub Workflow Run Event")
    assert "⚙️ CI · push · main" in message
    assert "📊 Result: failure" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["requested", "in_progress"])
async def test_workflow_run_handler_skips_non_completed(action):
    """requested/in_progress 不发送消息，避免刷屏."""
    payload = {"action": action, "workflow_run": {"name": "CI"}}

    assert await handle_workflow_run_event(payload, context=None) is None


@pytest.mark.asyncio
async def test_star_handler_formats_watch_started():
    """watch started（旧版 star 事件）应渲染新增 Star."""
    payload = {
        "action": "started",
        "repository": {
            "full_name": "owner/repo",
            "stargazers_count": 128,
            "html_url": "https://github.com/owner/repo",
        },
        "sender": {"login": "alice"},
    }

    message = await handle_star_event(payload, context=None)

    assert message is not None
    assert "⭐ GitHub Star Event" in message
    assert "alice starred owner/repo" in message
    assert "⭐ Stars: 128" in message


@pytest.mark.asyncio
async def test_star_handler_formats_unstar():
    """star deleted 应渲染取消 Star."""
    payload = {
        "action": "deleted",
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "alice"},
    }

    message = await handle_star_event(payload, context=None)

    assert message is not None
    assert message.startswith("💔 GitHub Star Event")
    assert "alice unstarred owner/repo" in message


@pytest.mark.asyncio
async def test_issue_comment_handler_formats_issue_comment():
    """issue_comment payload 应区分 Issue/PR 并渲染评论摘要."""
    payload = {
        "action": "created",
        "issue": {
            "number": 42,
            "title": "Bug report",
            "pull_request": None,
        },
        "comment": {
            "body": "Looks good to me",
            "html_url": "https://github.com/owner/repo/issues/42#issuecomment-1",
        },
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "alice"},
    }

    message = await handle_issue_comment_event(payload, context=None)

    assert message is not None
    assert "📋 Issue #42: Bug report" in message
    assert "💭 Looks good to me" in message


@pytest.mark.asyncio
async def test_issue_comment_handler_marks_pull_request_comments():
    """issue.pull_request 非空时按 PR 渲染."""
    payload = {
        "action": "created",
        "issue": {"number": 10, "title": "Feature", "pull_request": {"url": "x"}},
        "comment": {"body": "LGTM", "html_url": ""},
        "repository": {"full_name": "owner/repo"},
        "sender": {"login": "alice"},
    }

    message = await handle_issue_comment_event(payload, context=None)

    assert message is not None
    assert "📋 PR #10: Feature" in message


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "handler",
    [
        handle_release_event,
        handle_workflow_run_event,
        handle_star_event,
        handle_issue_comment_event,
    ],
)
async def test_handler_exception_returns_none(handler):
    """payload 结构异常时应记录错误并返回 None，不能让请求失败."""
    assert await handler(_ExplodingDict(), context=None) is None
