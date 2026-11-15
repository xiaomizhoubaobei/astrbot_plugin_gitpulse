"""CI 失败归因卡片格式化.

与其它 formatter 一致：纯函数、无 I/O，便于单测。
"""


def format_ci_failure_card(
    repo_name: str,
    workflow_name: str,
    conclusion: str,
    analysis: str,
    run_url: str = "",
) -> str:
    """把 LLM 归因结论渲染成可直接发送的卡片.

    Args:
        repo_name: 仓库全名（``owner/repo``）。
        workflow_name: 工作流名称，可为空。
        conclusion: 运行结论（failure / timed_out / startup_failure）。
        analysis: LLM 生成的中文归因结论。
        run_url: 运行页面链接，为空时不输出该行。

    Returns:
        可直接发送到聊天平台的多行文本。
    """
    lines = [
        "🤖 CI 失败归因",
        f"📦 {repo_name} · {workflow_name or 'Workflow'} · {conclusion or 'unknown'}",
        analysis,
    ]
    if run_url:
        lines.append(f"📎 {run_url}")
    return "\n".join(lines)
