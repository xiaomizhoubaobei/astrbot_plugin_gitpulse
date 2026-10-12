"""Push event formatter."""

from __future__ import annotations

from typing import Any, Iterable, Mapping

# 单个事件最多逐条展示的 commit 数量，超出部分折叠为摘要，避免消息过长
MAX_DISPLAYED_COMMITS = 5

# 单条 commit 摘要的最大字符数，超出截断，避免长提交信息撑爆消息
COMMIT_MESSAGE_LIMIT = 72


def _short_message(message: str) -> str:
    """把 commit 信息压成单行：去掉换行、压缩空白、超长截断."""
    # commit message 常带多行 body，群消息里只保留首行摘要
    text = (message or "").strip()
    first_line = text.splitlines()[0] if text else "No message"
    collapsed = " ".join(first_line.split())
    if len(collapsed) > COMMIT_MESSAGE_LIMIT:
        collapsed = collapsed[: COMMIT_MESSAGE_LIMIT - 1] + "…"
    return collapsed


def _normalize_commit(commit: Mapping[str, Any]) -> dict[str, str]:
    """Normalize a single GitHub commit object into display fields."""
    return {
        "message": _short_message(commit.get("message") or "No message"),
        "id": (commit.get("id") or "")[:7],
    }


def format_push_message(
    author_name: str,
    repo_name: str,
    branch: str,
    commits: Iterable[Mapping[str, Any]],
    commit_url: str = "",
) -> str:
    """Format push event message with all commits folded into a summary.

    commits 为本次推送的提交列表（每个含 id/message）。
    单条提交时保持紧凑格式；多条时每条压缩为一行（最多 MAX_DISPLAYED_COMMITS 条），
    超出部分折叠为 "…等 N 条"，避免消息过长。
    """
    normalized = [_normalize_commit(c) for c in commits]
    total = len(normalized)

    header = (
        f"📦 GitHub Push Event\n"
        f"👤 {author_name} pushed to {repo_name}\n"
        f"🌿 Branch: {branch}"
    )

    # 单条提交：沿用原有的紧凑单行摘要格式
    if total <= 1:
        lines = [header]
        if normalized:
            lines.append(f"💬 {normalized[0]['message']}")
            lines.append(f"🔗 Commit: {normalized[0]['id']}")
        if commit_url:
            lines.append(f"📎 {commit_url}")
        return "\n".join(lines)

    # 多条提交：每条压成一行，超出上限折叠统计
    lines = [header, f"🔢 {total} commits"]
    for commit in normalized[:MAX_DISPLAYED_COMMITS]:
        lines.append(f"  • {commit['id']} {commit['message']}")
    hidden = total - MAX_DISPLAYED_COMMITS
    if hidden > 0:
        lines.append(f"  …等 {hidden} 条")
    if commit_url:
        lines.append(f"📎 {commit_url}")
    return "\n".join(lines)
