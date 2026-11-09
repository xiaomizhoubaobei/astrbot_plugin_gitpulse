"""Star / watch event handler."""

from astrbot.api import logger

from ..formatters.star_formatter import format_star_message


async def handle_star_event(data: dict, context):
    """Handle star and legacy watch events from GitHub webhook."""
    try:
        action = data.get("action", "unknown")
        repository = data.get("repository") or {}
        sender = data.get("sender") or {}

        return format_star_message(
            action=action,
            author_name=sender.get("login", "Unknown"),
            repo_name=repository.get("full_name", "Unknown"),
            stars_count=repository.get("stargazers_count"),
            repo_url=repository.get("html_url", ""),
        )

    except Exception as e:
        logger.error(f"GitHub Webhook: Error handling star/watch event: {e}")
        return None
