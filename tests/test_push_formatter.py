"""Tests for push event formatter."""

from src.formatters.push_formatter import (
    MAX_DISPLAYED_COMMITS,
    format_push_message,
)


def _make_commit(message: str, sha: str, url: str = "") -> dict:
    """构造 GitHub 风格的 commit 结构。"""
    return {"message": message, "id": sha, "url": url or f"https://example.com/{sha}"}


def test_single_commit_keeps_compact_format():
    """单条 commit 保持紧凑格式，不出现多提交计数行。"""
    message = format_push_message(
        author_name="alice",
        repo_name="owner/repo",
        branch="main",
        commits=[_make_commit("fix: 修复问题", "abcdef1234567890")],
        commit_url="https://example.com/abcdef1",
    )

    assert "alice pushed to owner/repo" in message
    assert "🌿 Branch: main" in message
    assert "💬 fix: 修复问题" in message
    assert "🔗 Commit: abcdef1" in message
    # 单条不出现多提交计数行与折叠提示
    assert "commits" not in message
    assert "已折叠" not in message
    assert "…等" not in message


def test_multiple_commits_all_rendered():
    """多 commit 时逐条展示，不再只取第一条。"""
    commits = [
        _make_commit("feat: 第一个", "1111111aaaa"),
        _make_commit("fix: 第二个", "2222222bbbb"),
        _make_commit("docs: 第三个", "3333333cccc"),
    ]
    message = format_push_message("alice", "owner/repo", "main", commits)

    assert "🔢 3 commits" in message
    for text in ("feat: 第一个", "fix: 第二个", "docs: 第三个"):
        assert text in message
    # 每条压成一行：短 ID + 摘要
    assert "• 1111111 feat: 第一个" in message


def test_overflow_commits_folded():
    """超过展示上限的 commit 折叠为 '…等 N 条'，避免消息过长。"""
    total = MAX_DISPLAYED_COMMITS + 2
    commits = [_make_commit(f"commit-{i}", f"{i:040d}") for i in range(total)]
    message = format_push_message("alice", "owner/repo", "main", commits)

    assert f"🔢 {total} commits" in message
    assert "commit-0" in message
    assert f"commit-{MAX_DISPLAYED_COMMITS - 1}" in message
    # 超出的部分不逐条展示，但给出折叠数量
    assert f"…等 {total - MAX_DISPLAYED_COMMITS} 条" in message
    assert f"commit-{MAX_DISPLAYED_COMMITS}" not in message


def test_multiline_message_only_first_line():
    """多行提交信息只取首行，避免污染消息格式。"""
    commit = _make_commit("feat: 标题\n\n详细描述\n第二行描述", "abcdef1234567890")
    message = format_push_message("alice", "owner/repo", "main", [commit])

    assert "feat: 标题" in message
    assert "详细描述" not in message


def test_overlong_message_truncated():
    """超长单行提交信息会被截断，防止撑爆消息。"""
    commit = _make_commit("feat: " + "很长的描述" * 30, "abcdef1234567890")
    message = format_push_message("alice", "owner/repo", "main", [commit])

    assert "…" in message
    # 单行摘要不会超过上限太多
    longest = max(len(line) for line in message.splitlines())
    assert longest <= 100
