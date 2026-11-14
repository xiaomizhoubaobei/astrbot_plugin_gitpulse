"""群内 GitHub 快捷指令的执行编排层.

AstrBot 的指令 handler 位于 ``main.py``（handler 需绑定插件实例），本模块只负责
「解析参数 → 调 GitHub API → 生成人话回显」，保持可单测、无框架依赖。

回显纪律：任何异常都在这里被翻译成群成员看得懂的中文，绝不把堆栈抛给用户。
"""

from __future__ import annotations

from typing import Any

from astrbot.api import logger

from ..formatters.github_command_formatter import (
    format_command_failure,
    format_command_success,
)
from ..services.github_command_service import (
    CommandError,
    CommandOutcome,
    create_issue_comment,
    ensure_repo_allowed,
    merge_pull_request,
    normalize_comment_body,
    normalize_merge_method,
    parse_target,
    set_issue_state,
)

COMMAND_HELP = (
    "🐙 GitPulse 群内 GitHub 快捷指令\n"
    "/github merge <编号> [squash|merge|rebase]  合并 PR\n"
    "/github close <编号>                          关闭 Issue / PR\n"
    "/github reopen <编号>                         重新打开 Issue / PR\n"
    "/github comment <编号> <正文>                发表评论\n"
    "编号可写作 12、#12 或 owner/repo#12；未写仓库时用配置里的默认仓库。"
)


def parse_command_line(text: str, command: str = "github") -> tuple[str, str, str]:
    """把群消息文本切成 ``(action, target, extra)``.

    入参是唤醒前缀已被 AstrBot 剥掉的消息文本（形如 ``github close 12``）。
    首个词与 ``command`` 一致时剥掉，其余按空白切分：

    - ``github merge 12 squash`` → ``("merge", "12", "squash")``
    - ``github close owner/repo#5`` → ``("close", "owner/repo#5", "")``
    - ``github comment 12 正文...`` → ``("comment", "12", "正文...")``

    只切前两个词，``extra`` 保留原始空白后的完整正文（评论正文可含空格）。
    """
    raw = (text or "").strip()
    if not raw:
        return "", "", ""

    head, _, rest = raw.partition(" ")
    if head.lower() != command.lower():
        # 兼容别名或带前缀的写法：只在首词不是本指令时保留整串
        rest = raw
    parts = rest.split(None, 2)
    action = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    extra = parts[2].strip() if len(parts) > 2 else ""
    return action.lower(), target, extra


def _require_token(token: str) -> str:
    """校验 GitHub Token 是否已配置，否则抛 ``CommandError``."""
    value = (token or "").strip()
    if not value:
        raise CommandError(
            "未配置 GitHub Token，无法执行写操作。请维护者在插件配置 github_token "
            "中填写具备对应仓库写权限的令牌"
        )
    return value


async def run_github_command(
    action: str,
    cfg: Any,
    target: str,
    extra: str = "",
) -> str:
    """执行一条群内 GitHub 指令，返回可直接回显的文本.

    Args:
        action: ``merge`` / ``close`` / ``reopen`` / ``comment``。
        cfg: ``PluginConfig`` 实例（提供 token、仓库授权列表与默认仓库）。
        target: 指令目标原文（``12`` / ``#12`` / ``owner/repo#12``）。
        extra: 附加参数：merge 的合并方式，或 comment 的评论正文。

    Returns:
        成功时带操作详情与链接，失败时带中文原因；本函数不抛异常。
    """
    try:
        repo, number = parse_target(target, cfg.github_command_default_repo)
        ensure_repo_allowed(repo, cfg.command_repos)
        token = _require_token(cfg.github_token)

        if action == "merge":
            method = normalize_merge_method(extra or cfg.github_command_merge_method)
            outcome = await merge_pull_request(repo, number, token, method)
        elif action in ("close", "reopen"):
            outcome = await set_issue_state(
                repo, number, token, close=action == "close"
            )
        elif action == "comment":
            body = normalize_comment_body(extra)
            outcome = await create_issue_comment(repo, number, token, body)
        else:
            raise CommandError(f"不支持的操作：{action}")
    except CommandError as exc:
        return format_command_failure(str(exc))
    except Exception as exc:  # 兜底：指令链路任何异常都只回显人话
        logger.error(f"GitHub Webhook: Command '{action}' failed unexpectedly: {exc}")
        return format_command_failure("执行失败，请稍后重试（详细原因见插件日志）")

    return _render(action, repo, number, outcome)


def _render(action: str, repo: str, number: int, outcome: CommandOutcome) -> str:
    """把服务层结果渲染成回显文本."""
    if not outcome.ok:
        return format_command_failure(outcome.error or "操作未生效，请稍后重试")
    logger.info(f"GitHub Webhook: Command '{action}' succeeded on {repo}#{number}")
    return format_command_success(
        action=action,
        repo=repo,
        number=number,
        detail=outcome.detail,
        url=outcome.url,
    )
