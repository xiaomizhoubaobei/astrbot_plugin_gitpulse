"""Tests for the issues event formatter."""

import pytest

from src.formatters.issues_formatter import format_issue_message


def _format(action: str) -> str:
    """按典型 Issue 参数渲染消息."""
    return format_issue_message(
        action=action,
        author_name="alice",
        repo_name="owner/repo",
        issue_number=42,
        title="发现一个缺陷",
        issue_url="https://github.com/owner/repo/issues/42",
    )


@pytest.mark.parametrize(
    ("action", "emoji"),
    [
        ("opened", "🆕"),
        ("closed", "✅"),
        ("reopened", "🔄"),
    ],
)
def test_known_actions_render_expected_emoji(action: str, emoji: str):
    """已知 Issue 动作应命中各自 emoji，并保留动作、编号与链接."""
    message = _format(action)

    assert message.startswith(emoji)
    assert f"alice {action} issue in owner/repo" in message
    assert "Issue #42: 发现一个缺陷" in message
    assert "https://github.com/owner/repo/issues/42" in message


def test_edited_action_falls_back_to_default_emoji():
    """GitHub 真实动作 edited 未映射 emoji 时回落 📝，同时保留动作文本."""
    message = _format("edited")

    assert message.startswith("📝")
    assert "alice edited issue in owner/repo" in message
