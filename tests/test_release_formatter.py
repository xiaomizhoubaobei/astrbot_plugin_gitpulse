"""Tests for the release event formatter."""

import pytest

from src.formatters.release_formatter import format_release_message


@pytest.mark.parametrize(
    ("action", "emoji"),
    [
        ("published", "🚀"),
        ("released", "🚀"),
        ("prereleased", "🧪"),
        ("unpublished", "🚫"),
        ("created", "📦"),
        ("edited", "📝"),
        ("deleted", "🗑️"),
    ],
)
def test_action_emoji_and_content(action, emoji):
    """各 release 动作应命中对应 emoji，并渲染标签、标题与链接."""
    message = format_release_message(
        action=action,
        author_name="alice",
        repo_name="owner/repo",
        tag_name="v1.2.3",
        release_name="Big Release",
        release_url="https://github.com/owner/repo/releases/tag/v1.2.3",
    )

    assert message.startswith(f"{emoji} GitHub Release Event")
    assert f"alice {action} release in owner/repo" in message
    assert "🏷️ v1.2.3: Big Release" in message
    assert "📎 https://github.com/owner/repo/releases/tag/v1.2.3" in message


def test_unknown_action_falls_back_to_default_emoji():
    """未知动作使用默认 emoji."""
    message = format_release_message(
        "unknown", "alice", "owner/repo", "v1.0.0", "", ""
    )

    assert message.startswith("📦 GitHub Release Event")
    assert "🏷️ v1.0.0" in message
    assert "📎" not in message


def test_release_name_equal_to_tag_is_not_duplicated():
    """名称与标签相同时只展示一次."""
    message = format_release_message(
        "published", "alice", "owner/repo", "v1.0.0", "v1.0.0", ""
    )

    assert "🏷️ v1.0.0" in message
    assert "v1.0.0: v1.0.0" not in message


def test_missing_tag_uses_untagged():
    """标签缺失时使用 untagged 占位."""
    message = format_release_message("published", "alice", "owner/repo", "", "", "")

    assert "🏷️ untagged" in message
