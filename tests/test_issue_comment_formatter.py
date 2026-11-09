"""Tests for the issue_comment event formatter."""

import pytest

from src.formatters.issue_comment_formatter import (
    COMMENT_BODY_LIMIT,
    format_issue_comment_message,
)


@pytest.mark.parametrize(
    ("action", "emoji", "verb"),
    [
        ("created", "💬", "commented on"),
        ("edited", "📝", "edited a comment on"),
        ("deleted", "🗑️", "deleted a comment on"),
    ],
)
def test_action_emoji_and_verb(action, emoji, verb):
    """各评论动作应命中对应 emoji 与动词，并渲染 issue 信息."""
    message = format_issue_comment_message(
        action=action,
        author_name="alice",
        repo_name="owner/repo",
        issue_number=42,
        title="Bug report",
        is_pull_request=False,
        comment_body="Looks good to me",
        comment_url="https://github.com/owner/repo/issues/42#issuecomment-1",
    )

    assert message.startswith(f"{emoji} GitHub Issue Comment Event")
    assert f"alice {verb} issue in owner/repo" in message
    assert "📋 Issue #42: Bug report" in message
    assert "💭 Looks good to me" in message
    assert "📎 https://github.com/owner/repo/issues/42#issuecomment-1" in message


def test_pull_request_comment_uses_pr_label():
    """issue.pull_request 存在时按 PR 渲染."""
    message = format_issue_comment_message(
        "created", "alice", "owner/repo", 10, "New feature", True, "LGTM", ""
    )

    assert "alice commented on pr in owner/repo" in message
    assert "📋 PR #10: New feature" in message


def test_long_and_multiline_body_is_collapsed_and_truncated():
    """评论取首行、压缩空白并按上限截断."""
    body = "first line " + "x" * (COMMENT_BODY_LIMIT * 2) + "\nsecond line"

    message = format_issue_comment_message(
        "created", "alice", "owner/repo", 1, "Title", False, body, ""
    )

    comment_line = next(line for line in message.splitlines() if line.startswith("💭 "))
    assert "second line" not in comment_line
    assert comment_line.endswith("…")
    assert len(comment_line) == len("💭 ") + COMMENT_BODY_LIMIT


def test_empty_body_and_url_are_optional():
    """评论内容或链接为空时不渲染对应行."""
    message = format_issue_comment_message(
        "created", "alice", "owner/repo", 1, "Title", False, "", ""
    )

    assert "💭" not in message
    assert "📎" not in message
