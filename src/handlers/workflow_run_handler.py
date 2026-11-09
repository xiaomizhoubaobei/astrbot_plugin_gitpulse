"""Workflow run event handler."""

from astrbot.api import logger

from ..core.constants import ACTION_WORKFLOW_COMPLETED
from ..formatters.workflow_run_formatter import format_workflow_run_message


async def handle_workflow_run_event(data: dict, context):
    """Handle workflow_run event from GitHub webhook.

    只转发 completed 状态，requested/in_progress 会产生大量噪声。
    """
    try:
        action = data.get("action", "unknown")
        if action != ACTION_WORKFLOW_COMPLETED:
            logger.info(
                f"GitHub Webhook: Skipping workflow_run action '{action}', "
                f"only completed runs are forwarded"
            )
            return None

        workflow_run = data.get("workflow_run") or {}
        repository = data.get("repository") or {}
        sender = data.get("sender") or {}

        return format_workflow_run_message(
            action=action,
            author_name=sender.get("login", "Unknown"),
            repo_name=repository.get("full_name", "Unknown"),
            workflow_name=workflow_run.get("name", ""),
            conclusion=workflow_run.get("conclusion", ""),
            branch=workflow_run.get("head_branch", ""),
            trigger_event=workflow_run.get("event", ""),
            run_url=workflow_run.get("html_url", ""),
        )

    except Exception as e:
        logger.error(f"GitHub Webhook: Error handling workflow_run event: {e}")
        return None
