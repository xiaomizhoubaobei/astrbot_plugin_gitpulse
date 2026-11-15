"""Tests for the CI failure analysis card formatter."""

from src.formatters.ci_failure_formatter import format_ci_failure_card


def test_format_ci_failure_card_contains_context_and_analysis():
    """卡片应包含动作标识、仓库、工作流、结论、分析与链接."""
    text = format_ci_failure_card(
        repo_name="owner/repo",
        workflow_name="CI",
        conclusion="failure",
        analysis="根因：缺少依赖\n- 安装 requirements.txt",
        run_url="https://github.com/owner/repo/actions/runs/123",
    )

    assert text.startswith("🤖 CI 失败归因")
    assert "owner/repo" in text
    assert "CI" in text
    assert "failure" in text
    assert "缺少依赖" in text
    assert "https://github.com/owner/repo/actions/runs/123" in text


def test_format_ci_failure_card_omits_empty_url_and_workflow():
    """无链接、无工作流名时使用占位且不输出空链接行."""
    text = format_ci_failure_card(
        repo_name="owner/repo",
        workflow_name="",
        conclusion="",
        analysis="根因：测试失败",
    )

    assert "Workflow" in text
    assert "unknown" in text
    assert "📎" not in text
    assert len(text.splitlines()) == 3
