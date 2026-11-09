"""Tests for the star / watch event formatter."""

import pytest

from src.formatters.star_formatter import format_star_message


@pytest.mark.parametrize("action", ["started", "created"])
def test_star_actions(action):
    """watch started 与 star created 都渲染为新增 Star."""
    message = format_star_message(
        action=action,
        author_name="alice",
        repo_name="owner/repo",
        stars_count=128,
        repo_url="https://github.com/owner/repo",
    )

    assert message.startswith("⭐ GitHub Star Event")
    assert "alice starred owner/repo" in message
    assert "⭐ Stars: 128" in message
    assert "📎 https://github.com/owner/repo" in message


def test_unstar_action():
    """star deleted 渲染为取消 Star，并使用不同 emoji."""
    message = format_star_message(
        "deleted", "alice", "owner/repo", 127, "https://github.com/owner/repo"
    )

    assert message.startswith("💔 GitHub Star Event")
    assert "alice unstarred owner/repo" in message


def test_missing_count_and_url_are_optional():
    """Star 数与仓库链接缺失时不渲染对应行."""
    message = format_star_message("started", "alice", "owner/repo", None, "")

    assert "⭐ Stars:" not in message
    assert "📎" not in message
