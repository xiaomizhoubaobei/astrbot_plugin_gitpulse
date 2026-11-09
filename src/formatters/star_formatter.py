"""Star / watch event formatter."""

from ..core.constants import ACTION_STAR_DELETED

STAR_EMOJI = "⭐"
UNSTAR_EMOJI = "💔"


def format_star_message(
    action: str,
    author_name: str,
    repo_name: str,
    stars_count: int | None,
    repo_url: str,
) -> str:
    """Format star/watch event message."""
    if action == ACTION_STAR_DELETED:
        emoji = UNSTAR_EMOJI
        verb = "unstarred"
    else:
        emoji = STAR_EMOJI
        verb = "starred"

    lines = [
        f"{emoji} GitHub Star Event",
        f"👤 {author_name} {verb} {repo_name}",
    ]
    if stars_count is not None:
        lines.append(f"⭐ Stars: {stars_count}")
    if repo_url:
        lines.append(f"📎 {repo_url}")
    return "\n".join(lines)
