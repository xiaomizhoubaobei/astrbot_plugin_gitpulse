"""群内 GitHub 快捷指令：直接调用 GitHub REST API 执行 merge / close / reopen / comment.

设计要点：
- **只认自家 API 地址**：请求路径全部由「校验过的仓库名 + 编号」拼出，
  不接受任何外部传入的 URL，避免被构造成 SSRF 请求。
- **仓库白名单前置**：调用方必须先过仓库白名单校验（可写指令必须显式授权仓库）。
- **错误一律转成人话**：401/403/404/405/409/422 等状态码都映射为中文原因，
  原始堆栈与响应体只进日志，不回显到群聊。
- 纯函数式的参数解析（``parse_target`` / ``parse_repo_whitelist``）便于单测。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping

import aiohttp
from astrbot.api import logger

from ..core.constants import (
    DEFAULT_MERGE_METHOD,
    GITHUB_API_BASE,
    GITHUB_COMMAND_TIMEOUT,
    GITHUB_WEB_BASE,
    ISSUE_STATE_CLOSED,
    ISSUE_STATE_OPEN,
    MAX_COMMAND_COMMENT_CHARS,
    SUPPORTED_MERGE_METHODS,
)

# GitHub 仓库全名：只允许 owner/repo 形式的常规字符，杜绝路径穿越。
_REPO_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# 指令目标：``12`` / ``#12`` / ``owner/repo#12`` / ``owner/repo 12``。
_NUMBER_PATTERN = re.compile(r"^#?(\d+)$")

# 白名单里的非 JSON 写法按逗号/空白切分，兼容 "owner/a, owner/b"。
_SPLIT_PATTERN = re.compile(r"[,\s]+")

# 状态码 → 中文原因。仅覆盖群成员看得懂、可自行处置的情形。
_STATUS_HINTS = {
    401: "GitHub Token 无效或已过期，请维护者检查插件配置",
    403: "GitHub 拒绝了本次操作：Token 权限不足，或已触发 API 限流",
    404: "找不到该仓库或编号；私有仓库需要 Token 具备访问权限",
    429: "触发 GitHub API 限流，请稍后重试",
}


class CommandError(ValueError):
    """指令参数或配置非法：``str(error)`` 可直接回显给群成员."""


@dataclass(frozen=True)
class CommandOutcome:
    """一次 GitHub API 调用的人话结果（成功带详情，失败带原因）."""

    ok: bool
    detail: str = ""
    url: str = ""
    error: str = ""


def parse_repo_whitelist(raw: Any) -> frozenset[str] | None:
    """解析 ``github_command_repos`` 白名单.

    Returns:
        未配置（None / 空白）时返回 ``None``；已配置时返回小写化的仓库全名
        集合。非法条目跳过并告警，全部条目都非法时返回空集合。

    注意：``None`` 与空集合在 :func:`ensure_repo_allowed` 中都表示**拒绝**
    ——写操作必须由维护者显式授权仓库，不能因为漏配就放开全部仓库。
    """
    if raw is None:
        return None

    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.startswith("["):
            try:
                data: Any = json.loads(text)
            except json.JSONDecodeError as exc:
                logger.warning(
                    f"GitHub Webhook: Invalid github_command_repos JSON: {exc}"
                )
                return frozenset()
        else:
            data = _SPLIT_PATTERN.split(text)
    elif isinstance(raw, (list, tuple)):
        data = list(raw)
    else:
        logger.warning("GitHub Webhook: github_command_repos must be a string or list")
        return frozenset()

    repos: set[str] = set()
    for item in data:
        if not isinstance(item, str):
            logger.warning(
                f"GitHub Webhook: Invalid github_command_repos entry {item!r}, "
                f"expecting owner/repo"
            )
            continue
        repo = item.strip().lower()
        if not _is_valid_repo(repo):
            logger.warning(
                f"GitHub Webhook: Invalid github_command_repos entry {item!r}, "
                f"expecting owner/repo"
            )
            continue
        repos.add(repo)
    return frozenset(repos)


def _is_valid_repo(repo: str) -> bool:
    """校验仓库全名：格式合法，且不含 ``.`` / ``..`` 这类穿越路径段."""
    if not _REPO_PATTERN.fullmatch(repo):
        return False
    return all(segment not in {".", ".."} for segment in repo.split("/"))


def ensure_repo_allowed(repo: str, whitelist: frozenset[str] | None) -> None:
    """校验仓库是否在授权列表内；不在则抛 ``CommandError``.

    ``whitelist`` 为 ``None``（未配置）或空集合（配置全非法）时一律拒绝：
    群内指令是**写操作**，Token 往往具备整账号范围的权限，必须由维护者显式
    列出允许操作的仓库，不能靠「漏配即放开」兜底。
    """
    if not _is_valid_repo(repo):
        raise CommandError(f"仓库名不合法：{repo}，应为 owner/repo 形式")
    if not whitelist:
        raise CommandError(
            "群内 GitHub 写操作尚未启用：请维护者先在插件配置 "
            "github_command_repos 中填写授权仓库（如 owner/repo）"
        )
    if repo.lower() not in whitelist:
        raise CommandError(
            f"仓库 {repo} 不在授权列表内，请维护者在插件配置 github_command_repos "
            f"中显式加入后再试"
        )


def parse_target(raw: str, default_repo: str = "") -> tuple[str, int]:
    """解析指令目标为 ``(repo, number)``.

    支持 ``12`` / ``#12`` / ``owner/repo#12``；未写仓库时使用 ``default_repo``。
    """
    text = (raw or "").strip()
    if not text:
        raise CommandError("缺少目标编号，例如：/github close 12 或 /github close #12")

    repo = default_repo.strip()
    if "#" in text:
        repo_part, _, number_part = text.rpartition("#")
        if repo_part.strip():
            repo = repo_part.strip()
    else:
        number_part = text

    match = _NUMBER_PATTERN.fullmatch(number_part.strip())
    if not match:
        raise CommandError(f"编号不合法：{raw}，应为 12、#12 或 owner/repo#12 形式")
    number = int(match.group(1))
    if number <= 0:
        raise CommandError("编号必须为正整数")

    if not repo:
        raise CommandError(
            "未指定仓库。请在指令里写明 owner/repo#编号，"
            "或由维护者配置 github_command_default_repo"
        )
    return repo, number


def normalize_merge_method(raw: str) -> str:
    """校验并归一化合并方式（merge / squash / rebase），默认 squash."""
    method = (raw or "").strip().lower() or DEFAULT_MERGE_METHOD
    if method not in SUPPORTED_MERGE_METHODS:
        raise CommandError(
            f"合并方式 {raw} 不受支持，可选："
            f"{' / '.join(sorted(SUPPORTED_MERGE_METHODS))}"
        )
    return method


def normalize_comment_body(raw: str) -> str:
    """校验评论正文：非空且不超过上限."""
    body = (raw or "").strip()
    if not body:
        raise CommandError("评论正文不能为空")
    if len(body) > MAX_COMMAND_COMMENT_CHARS:
        raise CommandError(
            f"评论正文过长（{len(body)} > {MAX_COMMAND_COMMENT_CHARS} 字符），请精简后再发"
        )
    return body


def _repo_path(repo: str, suffix: str) -> str:
    """拼出 ``/repos/<owner>/<repo>/<suffix>``，并前置校验仓库名.

    纵深防御：即使调用方漏了白名单校验，非法仓库名（含 ``.`` / ``..`` 路径段、
    斜杠越界）也不会被拼进请求路径。
    """
    if not _is_valid_repo(repo):
        raise CommandError(f"仓库名不合法：{repo}，应为 owner/repo 形式")
    return f"/repos/{repo}/{suffix}"


def _api_error(status: int, action: str) -> str:
    """把 HTTP 状态码翻译成可直接回显的中文原因."""
    hint = _STATUS_HINTS.get(status)
    if hint:
        return hint
    if status == 405 and action == "merge":
        return "该 PR 当前无法合并：可能已合并/已关闭，或存在冲突、缺少必需检查"
    if status == 409 and action == "merge":
        return "PR 的 head 分支刚刚被修改，请稍后重试合并"
    if status == 422:
        # 422 的含义随动作不同，避免用一句笼统文案误导群成员。
        if action == "merge":
            return "GitHub 拒绝了本次合并：PR 状态或合并方式不满足条件"
        if action == "comment":
            return "GitHub 拒绝了本次评论：正文可能不符合要求，或该目标不支持评论"
        return "GitHub 拒绝了本次请求：编号可能不是 Issue / PR，或该仓库未开启 Issues"
    if status >= 500:
        return "GitHub 服务异常（5xx），请稍后重试"
    return f"GitHub API 返回 HTTP {status}，操作未生效"


async def _request(
    method: str,
    path: str,
    token: str,
    *,
    payload: Mapping[str, Any] | None = None,
    timeout: float = GITHUB_COMMAND_TIMEOUT,
) -> tuple[int, Any]:
    """调用 GitHub REST API，返回 ``(status, 解析后的 JSON)``；网络异常返回 ``(0, None)``."""
    url = f"{GITHUB_API_BASE}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": "GitPulse-AstrBot-Plugin",
        "X-GitHub-Api-Version": "2022-11-28",
        "Authorization": f"Bearer {token}",
    }

    try:
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.request(
                method, url, headers=headers, json=payload
            ) as response:
                text = await response.text()
                status = response.status
    except Exception as exc:  # 网络/超时：只告警，由调用方回显人话
        logger.warning(
            f"GitHub Webhook: GitHub API request failed ({method} {url}): {exc}"
        )
        return 0, None

    if not text:
        return status, None
    try:
        return status, json.loads(text)
    except json.JSONDecodeError:
        logger.warning(
            f"GitHub Webhook: GitHub API returned non-JSON response "
            f"({method} {url}, HTTP {status})"
        )
        return status, None


async def merge_pull_request(
    repo: str,
    number: int,
    token: str,
    method: str = DEFAULT_MERGE_METHOD,
) -> CommandOutcome:
    """合并指定 PR，返回可直接回显的结果."""
    status, data = await _request(
        "PUT",
        _repo_path(repo, f"pulls/{number}/merge"),
        token,
        payload={"merge_method": method},
    )

    if status == 0:
        return CommandOutcome(
            ok=False, error="请求 GitHub 失败（网络异常或超时），请稍后重试"
        )
    if status != 200:
        logger.warning(
            f"GitHub Webhook: Merge {repo}#{number} failed with HTTP {status}"
        )
        return CommandOutcome(ok=False, error=_api_error(status, "merge"))

    payload = data if isinstance(data, Mapping) else {}
    sha = str(payload.get("sha") or "")
    detail = f"合并方式：{method}"
    if sha:
        detail += f" · 合并提交 {sha[:7]}"
    # merge 接口的响应不含 html_url，按仓库名与编号拼出 PR 链接。
    url = f"{GITHUB_WEB_BASE}/{repo}/pull/{number}"
    return CommandOutcome(ok=True, detail=detail, url=url)


async def set_issue_state(
    repo: str,
    number: int,
    token: str,
    *,
    close: bool,
) -> CommandOutcome:
    """关闭 / 重新打开 Issue 或 PR（PR 同样走 issues 接口改 state）."""
    state = ISSUE_STATE_CLOSED if close else ISSUE_STATE_OPEN
    action = "close" if close else "reopen"

    status, data = await _request(
        "PATCH",
        _repo_path(repo, f"issues/{number}"),
        token,
        payload={"state": state},
    )

    if status == 0:
        return CommandOutcome(
            ok=False, error="请求 GitHub 失败（网络异常或超时），请稍后重试"
        )
    if status != 200:
        logger.warning(
            f"GitHub Webhook: {action} {repo}#{number} failed with HTTP {status}"
        )
        return CommandOutcome(ok=False, error=_api_error(status, action))

    payload = data if isinstance(data, Mapping) else {}
    is_pull_request = isinstance(payload.get("pull_request"), Mapping)
    kind = "PR" if is_pull_request else "Issue"
    url = str(payload.get("html_url") or "")
    return CommandOutcome(ok=True, detail=f"目标类型：{kind}", url=url)


async def create_issue_comment(
    repo: str,
    number: int,
    token: str,
    body: str,
) -> CommandOutcome:
    """在 Issue / PR 下发表评论."""
    status, data = await _request(
        "POST",
        _repo_path(repo, f"issues/{number}/comments"),
        token,
        payload={"body": body},
    )

    if status == 0:
        return CommandOutcome(
            ok=False, error="请求 GitHub 失败（网络异常或超时），请稍后重试"
        )
    if status not in (200, 201):
        logger.warning(
            f"GitHub Webhook: Comment {repo}#{number} failed with HTTP {status}"
        )
        return CommandOutcome(ok=False, error=_api_error(status, "comment"))

    payload = data if isinstance(data, Mapping) else {}
    url = str(payload.get("html_url") or "")
    return CommandOutcome(ok=True, detail=f"评论已发布（{len(body)} 字符）", url=url)
