"""Release event formatter."""

from ..core.constants import (
    ACTION_RELEASE_CREATED,
    ACTION_RELEASE_DELETED,
    ACTION_RELEASE_EDITED,
    ACTION_RELEASE_PRERELEASED,
    ACTION_RELEASE_PUBLISHED,
    ACTION_RELEASE_RELEASED,
    ACTION_RELEASE_UNPUBLISHED,
)

# 动作 → emoji 映射；键引用 constants，避免与 GitHub 枚举拼写漂移。
ACTION_EMOJI = {
    ACTION_RELEASE_PUBLISHED: "🚀",
    ACTION_RELEASE_RELEASED: "🚀",
    ACTION_RELEASE_CREATED: "📦",
    ACTION_RELEASE_EDITED: "📝",
    ACTION_RELEASE_DELETED: "🗑️",
    ACTION_RELEASE_PRERELEASED: "🧪",
    ACTION_RELEASE_UNPUBLISHED: "🚫",
}

DEFAULT_ACTION_EMOJI = "📦"


def format_release_message(
    action: str,
    author_name: str,
    repo_name: str,
    tag_name: str,
    release_name: str,
    release_url: str,
) -> str:
    """Format release event message."""
    action_emoji = ACTION_EMOJI.get(action, DEFAULT_ACTION_EMOJI)

    tag = tag_name or "untagged"
    title = release_name or tag
    if tag_name and release_name and release_name != tag_name:
        title = f"{tag_name}: {release_name}"

    lines = [
        f"{action_emoji} GitHub Release Event",
        f"👤 {author_name} {action} release in {repo_name}",
        f"🏷️ {title}",
    ]
    if release_url:
        lines.append(f"📎 {release_url}")
    return "\n".join(lines)
