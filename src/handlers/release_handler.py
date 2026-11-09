"""Release event handler."""

from astrbot.api import logger

from ..formatters.release_formatter import format_release_message


async def handle_release_event(data: dict, context):
    """Handle release event from GitHub webhook."""
    try:
        action = data.get("action", "unknown")
        release = data.get("release") or {}
        repository = data.get("repository") or {}
        sender = data.get("sender") or {}

        return format_release_message(
            action=action,
            author_name=sender.get("login", "Unknown"),
            repo_name=repository.get("full_name", "Unknown"),
            tag_name=release.get("tag_name", ""),
            release_name=release.get("name", ""),
            release_url=release.get("html_url", ""),
        )

    except Exception as e:
        logger.error(f"GitHub Webhook: Error handling release event: {e}")
        return None
