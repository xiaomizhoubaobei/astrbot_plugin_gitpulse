"""群内 GitHub 快捷指令（merge / close / reopen / comment）的结果格式化.

与其它 formatter 一致：纯函数、无 I/O，便于单测。
"""

ACTION_LABELS = {
    "merge": "合并 PR",
    "close": "关闭",
    "reopen": "重新打开",
    "comment": "发表评论",
}

ACTION_EMOJI = {
    "merge": "🔀",
    "close": "✅",
    "reopen": "🔄",
    "comment": "💬",
}

DEFAULT_ACTION_EMOJI = "✅"

FAILURE_EMOJI = "⚠️"


def format_command_success(
    action: str,
    repo: str,
    number: int,
    detail: str = "",
    url: str = "",
) -> str:
    """格式化指令执行成功的结果.

    Args:
        action: 指令动作（``merge`` / ``close`` / ``reopen`` / ``comment``）。
        repo: 仓库全名（``owner/repo``）。
        number: Issue 或 PR 编号。
        detail: 附加说明（合并方式、目标类型等），可为空。
        url: 目标页面链接，为空时不输出该行。

    Returns:
        可直接回显到群聊的多行文本。
    """
    label = ACTION_LABELS.get(action, action)
    emoji = ACTION_EMOJI.get(action, DEFAULT_ACTION_EMOJI)

    lines = [f"{emoji} 已完成{label}：{repo}#{number}"]
    if detail:
        lines.append(detail)
    if url:
        lines.append(f"🔗 {url}")
    return "\n".join(lines)


def format_command_failure(message: str) -> str:
    """格式化指令执行失败的结果：只回显可直接看懂的中文原因."""
    return f"{FAILURE_EMOJI} {message}"
