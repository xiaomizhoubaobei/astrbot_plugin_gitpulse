"""Workflow run event formatter."""

from ..core.constants import (
    WORKFLOW_CONCLUSION_ACTION_REQUIRED,
    WORKFLOW_CONCLUSION_CANCELLED,
    WORKFLOW_CONCLUSION_FAILURE,
    WORKFLOW_CONCLUSION_NEUTRAL,
    WORKFLOW_CONCLUSION_SKIPPED,
    WORKFLOW_CONCLUSION_STALE,
    WORKFLOW_CONCLUSION_STARTUP_FAILURE,
    WORKFLOW_CONCLUSION_SUCCESS,
    WORKFLOW_CONCLUSION_TIMED_OUT,
)

# conclusion → emoji；未知或缺失时使用 DEFAULT_CONCLUSION_EMOJI。
CONCLUSION_EMOJI = {
    WORKFLOW_CONCLUSION_SUCCESS: "✅",
    WORKFLOW_CONCLUSION_FAILURE: "❌",
    WORKFLOW_CONCLUSION_CANCELLED: "⚪",
    WORKFLOW_CONCLUSION_SKIPPED: "⏭️",
    WORKFLOW_CONCLUSION_TIMED_OUT: "⏰",
    WORKFLOW_CONCLUSION_ACTION_REQUIRED: "⚠️",
    WORKFLOW_CONCLUSION_NEUTRAL: "➖",
    WORKFLOW_CONCLUSION_STALE: "🧹",
    WORKFLOW_CONCLUSION_STARTUP_FAILURE: "💥",
}

DEFAULT_CONCLUSION_EMOJI = "🔄"


def format_workflow_run_message(
    action: str,
    author_name: str,
    repo_name: str,
    workflow_name: str,
    conclusion: str,
    branch: str,
    trigger_event: str,
    run_url: str,
) -> str:
    """Format workflow_run event message."""
    conclusion_emoji = CONCLUSION_EMOJI.get(conclusion, DEFAULT_CONCLUSION_EMOJI)

    lines = [
        f"{conclusion_emoji} GitHub Workflow Run Event",
        f"👤 {author_name} {action} workflow in {repo_name}",
        f"⚙️ {workflow_name or 'Workflow'} · "
        f"{trigger_event or 'unknown'} · {branch or 'unknown'}",
        f"📊 Result: {conclusion or 'unknown'}",
    ]
    if run_url:
        lines.append(f"📎 {run_url}")
    return "\n".join(lines)
