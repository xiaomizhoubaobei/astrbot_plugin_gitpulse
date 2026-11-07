"""Pull request event formatter."""

from ..core.constants import (
    ACTION_PR_CLOSED,
    ACTION_PR_OPENED,
    ACTION_PR_REOPENED,
    ACTION_PR_SYNCHRONIZE,
)

# 动作 → emoji 映射。键一律引用 constants 中的常量，避免硬编码字符串
# 与常量表拼写漂移（issue #4：曾因 "synchronize" / "synchronized" 不一致，
# 导致 🔀 分支永不命中，所有同步事件恒定回落 📝）。
ACTION_EMOJI = {
    ACTION_PR_OPENED: "🆕",
    ACTION_PR_CLOSED: "✅",
    ACTION_PR_REOPENED: "🔄",
    ACTION_PR_SYNCHRONIZE: "🔀",
}

DEFAULT_ACTION_EMOJI = "📝"


def format_pull_request_message(
    action: str,
    author_name: str,
    repo_name: str,
    pr_number: int,
    title: str,
    base_branch: str,
    head_branch: str,
    pr_url: str,
) -> str:
    """Format pull request event message."""
    action_emoji = ACTION_EMOJI.get(action, DEFAULT_ACTION_EMOJI)

    return (
        f"{action_emoji} GitHub Pull Request Event\n"
        f"👤 {author_name} {action} PR in {repo_name}\n"
        f"📋 PR #{pr_number}: {title}\n"
        f"🌿 {head_branch} → {base_branch}\n"
        f"📎 {pr_url}"
    )
