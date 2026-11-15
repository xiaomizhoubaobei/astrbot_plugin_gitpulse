"""群内 GitHub 快捷指令的执行编排层.

AstrBot 的指令 handler 位于 ``main.py``（handler 需绑定插件实例），本模块只负责
「解析参数 → 调 GitHub API → 生成人话回显」，保持可单测、无框架依赖。

回显纪律：任何异常都在这里被翻译成群成员看得懂的中文，绝不把堆栈抛给用户。
"""

from __future__ import annotations

import re
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

# 指令主词（含 filter.command 注册的短别名）。首个词命中时剥掉，
# 其余首词（merge / close / reopen / comment）按动作处理。
COMMAND_PREFIXES = frozenset({"github", "gh"})

# filter.command 的动作别名：首词本身就是动作（如 ``/merge 12``）。
ACTION_COMMAND_ALIASES = frozenset({"merge", "close", "reopen", "comment"})

# ``owner/repo 12`` 这种以空格分隔编号的写法，折叠成 ``owner/repo#12``，
# 剩余部分（合并方式 / 评论正文）原样保留。
_SPACE_NUMBER_PATTERN = re.compile(r"#?(\d+)(?:\s+|$)")


def parse_command_line(text: str) -> tuple[str, str, str]:
    """把群消息文本切成 ``(action, target, extra)``.

    入参是唤醒前缀已被 AstrBot 剥掉的消息文本（形如 ``github close 12``）。
    首个词是 ``github`` / ``gh`` 时剥掉，其余按空白切分：

    - ``github merge 12 squash`` → ``("merge", "12", "squash")``
    - ``gh close 12`` → ``("close", "12", "")``
    - ``github close owner/repo#5`` → ``("close", "owner/repo#5", "")``
    - ``github close owner/repo 5`` → ``("close", "owner/repo#5", "")``
    - ``github comment 12 正文...`` → ``("comment", "12", "正文...")``

    只切前两个词，``extra`` 保留原始空白后的完整正文（评论正文可含空格）。
    """
    raw = (text or "").strip()
    if not raw:
        return "", "", ""

    head, _, rest = raw.partition(" ")
    if head.lower() not in COMMAND_PREFIXES:
        # 兼容别名写法（/merge <编号>）：首词本身就是动作
        rest = raw
    parts = rest.split(None, 2)
    action = parts[0] if parts else ""
    target = parts[1] if len(parts) > 1 else ""
    extra = parts[2].strip() if len(parts) > 2 else ""
    target, extra = _fold_space_target(target, extra)
    return action.lower(), target, extra


def _fold_space_target(target: str, extra: str) -> tuple[str, str]:
    """把 ``owner/repo 12`` 形式的编号折进 target，返回 ``(target, extra)``.

    仅当 target 是带斜杠、尚未带 ``#`` 的仓库名，且 extra 以编号开头时折叠；
    ``owner/repo#12`` 已带编号的写法保持不变。
    """
    if "#" in target or "/" not in target or not extra:
        return target, extra
    match = _SPACE_NUMBER_PATTERN.match(extra)
    if not match:
        return target, extra
    rest = extra[match.end() :].strip()
    return f"{target}#{match.group(1)}", rest


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
