"""Issue / PR comment event handler."""

from astrbot.api import logger

from ..formatters.issue_comment_formatter import format_issue_comment_message


async def handle_issue_comment_event(data: dict, context):
    """Handle issue_comment event from GitHub webhook."""
    try:
        action = data.get("action", "unknown")
        issue = data.get("issue") or {}
        comment = data.get("comment") or {}
        repository = data.get("repository") or {}
        sender = data.get("sender") or {}

        return format_issue_comment_message(
            action=action,
            author_name=sender.get("login", "Unknown"),
            repo_name=repository.get("full_name", "Unknown"),
            issue_number=issue.get("number", 0),
            title=issue.get("title", "No title"),
            is_pull_request=bool(issue.get("pull_request")),
            comment_body=comment.get("body", ""),
            comment_url=comment.get("html_url", ""),
        )

    except Exception as e:
        logger.error(f"GitHub Webhook: Error handling issue_comment event: {e}")
        return None
