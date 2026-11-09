"""Tests for the workflow_run event formatter."""

import pytest

from src.formatters.workflow_run_formatter import format_workflow_run_message


@pytest.mark.parametrize(
    ("conclusion", "emoji"),
    [
        ("success", "✅"),
        ("failure", "❌"),
        ("cancelled", "⚪"),
        ("skipped", "⏭️"),
        ("timed_out", "⏰"),
        ("action_required", "⚠️"),
        ("neutral", "➖"),
        ("stale", "🧹"),
        ("startup_failure", "💥"),
    ],
)
def test_conclusion_emoji(conclusion, emoji):
    """各结论应命中对应 emoji，并渲染工作流信息."""
    message = format_workflow_run_message(
        action="completed",
        author_name="alice",
        repo_name="owner/repo",
        workflow_name="CI",
        conclusion=conclusion,
        branch="main",
        trigger_event="push",
        run_url="https://github.com/owner/repo/actions/runs/1",
    )

    assert message.startswith(f"{emoji} GitHub Workflow Run Event")
    assert "alice completed workflow in owner/repo" in message
    assert "⚙️ CI · push · main" in message
    assert f"📊 Result: {conclusion}" in message
    assert "📎 https://github.com/owner/repo/actions/runs/1" in message


def test_unknown_conclusion_uses_default_emoji():
    """未知或缺失结论使用默认 emoji."""
    message = format_workflow_run_message(
        "completed", "alice", "owner/repo", "", "", "", "", ""
    )

    assert message.startswith("🔄 GitHub Workflow Run Event")
    assert "⚙️ Workflow · unknown · unknown" in message
    assert "📊 Result: unknown" in message
    assert "📎" not in message
