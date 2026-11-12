"""GitPulse plugin core implementation."""

import asyncio
import errno
import json

from aiohttp import web
from astrbot.api import all as api
from astrbot.api import logger
from astrbot.api.message_components import Plain
from astrbot.api.star import Context, Star

from ..handlers.issue_comment_handler import handle_issue_comment_event
from ..handlers.issues_handler import handle_issues_event
from ..handlers.pull_request_handler import handle_pull_request_event
from ..handlers.push_handler import handle_push_event
from ..handlers.release_handler import handle_release_event
from ..handlers.star_handler import handle_star_event
from ..handlers.workflow_run_handler import handle_workflow_run_event
from ..services.repo_router import get_repo_full_name, resolve_target_umos
from ..utils.rate_limiter import RateLimiter
from ..utils.verify_signature import verify_signature
from .config import PluginConfig
from .constants import (
    EVENT_TYPE_ISSUE_COMMENT,
    EVENT_TYPE_ISSUES,
    EVENT_TYPE_PING,
    EVENT_TYPE_PULL_REQUEST,
    EVENT_TYPE_PUSH,
    EVENT_TYPE_RELEASE,
    EVENT_TYPE_STAR,
    EVENT_TYPE_WATCH,
    EVENT_TYPE_WORKFLOW_RUN,
    SERVER_START_MAX_ATTEMPTS,
    SERVER_START_RETRY_BASE_DELAY,
    SUPPORTED_EVENT_TYPES,
)


def _is_address_in_use(error: OSError) -> bool:
    """判断 OSError 是否由端口被占用（EADDRINUSE）引起."""
    if error.errno == errno.EADDRINUSE:
        return True
    # Windows 的 asyncio 会抛 WSAEADDRINUSE（10048）而非 errno.EADDRINUSE。
    # POSIX 没有该常量，用 -1 作哨兵：若用 None 作默认值，errno 为 None 的
    # OSError 会被误判为端口占用并做无意义重试。
    if error.errno == getattr(errno, "WSAEADDRINUSE", -1):
        return True
    return getattr(error, "winerror", None) == 10048


class GitHubWebhookPlugin(Star):
    """GitHub Webhook receiver plugin."""

    def __init__(self, context: Context, config):
        """初始化插件：装配 aiohttp 应用与限流器."""
        super().__init__(context)
        self.cfg = PluginConfig(config)
        self.app = web.Application()
        self.app.router.add_post(self.cfg.webhook_path, self.handle_webhook)
        self.runner: web.AppRunner | None = None
        self.site: web.TCPSite | None = None
        # 后台事件处理任务：先回 200 再格式化/调 LLM/发送，避免 GitHub 10 秒超时重试。
        self._background_tasks: set[asyncio.Task] = set()

        # rate_limit <= 0 表示不启用限流，此时显式置空以便下游判空
        self.rate_limiter: RateLimiter | None = None
        if self.cfg.rate_limit > 0:
            self.rate_limiter = RateLimiter(max_requests=self.cfg.rate_limit)

    async def start_server(self):
        """启动（或重启）webhook HTTP 服务."""
        # Clean up any existing server instance
        if self.site:
            await self.site.stop()
            logger.info("GitHub Webhook: Cleaned up existing server instance")
        if self.runner:
            await self.runner.cleanup()
            logger.info("GitHub Webhook: Cleaned up existing runner")

        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        # 监听地址来自配置（默认 0.0.0.0，可用 bind_host 收敛到 127.0.0.1
        # 配合反向代理），不再在代码里硬编码通配地址。
        try:
            self.site = await self._start_site_with_retry()
        except OSError:
            # 启动失败时释放 runner，避免残留已 setup 的应用资源
            await self.runner.cleanup()
            self.runner = None
            self.site = None
            raise
        logger.info(
            f"GitHub Webhook: Server started on "
            f"{self.cfg.bind_host}:{self.cfg.port}{self.cfg.webhook_path}"
        )

    async def _start_site_with_retry(self) -> web.TCPSite:
        """绑定配置地址；端口被占用时按指数退避重试，仍失败则抛出 OSError."""
        if self.runner is None:  # pragma: no cover - 仅防御性检查
            raise RuntimeError("runner is not initialized")

        host, port = self.cfg.bind_host, self.cfg.port
        for attempt in range(1, SERVER_START_MAX_ATTEMPTS + 1):
            site = web.TCPSite(self.runner, host, port)
            try:
                await site.start()
                return site
            except OSError as error:
                if not _is_address_in_use(error):
                    logger.error(
                        f"GitHub Webhook: Failed to bind {host}:{port}: {error}. "
                        f"Check that bind_host is a local address and port is valid"
                    )
                    raise
                if attempt >= SERVER_START_MAX_ATTEMPTS:
                    logger.error(
                        f"GitHub Webhook: Port {port} is still in use after "
                        f"{SERVER_START_MAX_ATTEMPTS} attempts. Change the 'port' "
                        f"config or free the port (e.g. lsof -i :{port}) and restart"
                    )
                    raise
                delay = SERVER_START_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.warning(
                    f"GitHub Webhook: Port {port} is in use ({error}), retrying "
                    f"in {delay:.1f}s "
                    f"({attempt}/{SERVER_START_MAX_ATTEMPTS} attempts used)"
                )
                await asyncio.sleep(delay)

        # 循环只会通过 return 或 raise 退出，此处仅为类型完备
        raise RuntimeError("unreachable")  # pragma: no cover

    async def handle_webhook(self, request: web.Request):
        """处理 GitHub webhook 请求：限流、验签、分发并回包."""
        event_type = request.headers.get("X-GitHub-Event", "unknown")
        signature = request.headers.get("X-Hub-Signature-256", "")

        # Rate limiting check
        if self.rate_limiter:
            is_allowed, retry_after = await self.rate_limiter.is_allowed()
            if not is_allowed:
                current, max_req = self.rate_limiter.get_usage()
                logger.warning(
                    f"GitHub Webhook: Rate limit exceeded "
                    f"({current}/{max_req} requests/minute)"
                )
                return web.Response(
                    status=429,
                    text=f"Rate limit exceeded. Retry after {retry_after} seconds.",
                    headers={
                        "Retry-After": str(retry_after),
                        "X-RateLimit-Limit": str(max_req),
                        "X-RateLimit-Remaining": str(max_req - current),
                        "X-RateLimit-Reset": str(retry_after),
                    },
                )

        # Read payload
        try:
            payload_bytes = await request.read()
            data = json.loads(payload_bytes.decode("utf-8"))
        except json.JSONDecodeError as e:
            logger.error(f"GitHub Webhook: Failed to parse JSON: {e}")
            return web.Response(status=400, text="Invalid JSON")
        except Exception as e:
            logger.error(f"GitHub Webhook: Failed to read request body: {e}")
            return web.Response(status=400, text="Failed to read request")

        # Signature verification
        # 只要配置了 secret 就必须验签：缺少签名头与签名不匹配同样拒绝，
        # 避免「无签名头即静默放行」的绕过路径。
        if self.cfg.webhook_secret:
            if not signature:
                logger.warning(
                    "GitHub Webhook: Missing signature header - request rejected"
                )
                return web.Response(status=401, text="Missing signature")
            if not verify_signature(payload_bytes, signature, self.cfg.webhook_secret):
                logger.warning("GitHub Webhook: Invalid signature - request rejected")
                return web.Response(status=401, text="Invalid signature")

        logger.info(f"GitHub Webhook: Received event type: {event_type}")

        if event_type == EVENT_TYPE_PING:
            return web.Response(text="Pong")

        if event_type not in SUPPORTED_EVENT_TYPES:
            logger.info(f"GitHub Webhook: Event type '{event_type}' not handled")
            return web.Response(status=200, text="OK")

        # 订阅白名单：未配置时启用全部支持的事件；ping 不受白名单影响。
        if not self.cfg.is_event_enabled(event_type):
            logger.info(
                f"GitHub Webhook: Event type '{event_type}' disabled by "
                f"enabled_events, ignoring"
            )
            return web.Response(status=200, text="OK")

        # 验签与白名单校验通过后立即回包；格式化、LLM 改写与发送转入后台，
        # 避免 GitHub 因处理耗时超过 10 秒而判定失败并重复推送。
        self._schedule_event_processing(event_type, data)
        return web.Response(status=200, text="OK")

    def _schedule_event_processing(self, event_type: str, data: dict):
        """把事件处理投递到后台任务，不阻塞 webhook 响应."""
        task = asyncio.create_task(self._process_event(event_type, data))
        self._background_tasks.add(task)
        task.add_done_callback(self._on_background_task_done)

    def _on_background_task_done(self, task: asyncio.Task):
        """回收已结束的后台任务，并记录逃逸的异常."""
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.error(
                f"GitHub Webhook: Background event processing failed: {exc}",
                exc_info=exc,
            )

    async def _process_event(self, event_type: str, data: dict):
        """在后台完成事件格式化、LLM 改写与消息发送；异常只记日志."""
        try:
            message = await self._format_event(event_type, data)
            if not message:
                return

            target_umos = resolve_target_umos(
                get_repo_full_name(data), self.cfg.repo_routes, self.cfg.target_umo
            )
            if self.cfg.enable_agent:
                from ..services.llm_service import send_with_agent

                await send_with_agent(self, message, data, event_type, target_umos)
            else:
                await self.send_message(message, target_umos)
        except Exception as e:
            logger.error(f"GitHub Webhook: Error processing event: {e}", exc_info=True)

    async def _format_event(self, event_type: str, data: dict) -> str | None:
        """按事件类型调用对应 handler 生成模板消息."""
        if event_type == EVENT_TYPE_PUSH:
            return await handle_push_event(data, self.context)
        if event_type == EVENT_TYPE_ISSUES:
            return await handle_issues_event(data, self.context)
        if event_type == EVENT_TYPE_PULL_REQUEST:
            return await handle_pull_request_event(data, self.context)
        if event_type == EVENT_TYPE_RELEASE:
            return await handle_release_event(data, self.context)
        if event_type == EVENT_TYPE_WORKFLOW_RUN:
            return await handle_workflow_run_event(data, self.context)
        if event_type in (EVENT_TYPE_STAR, EVENT_TYPE_WATCH):
            return await handle_star_event(data, self.context)
        if event_type == EVENT_TYPE_ISSUE_COMMENT:
            return await handle_issue_comment_event(data, self.context)
        return None

    async def wait_for_background_tasks(self):
        """等待当前所有后台事件处理任务结束（测试与优雅退出使用）."""
        tasks = list(self._background_tasks)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancel_background_tasks(self):
        """取消尚未完成的后台任务，避免插件卸载后仍有协程在跑."""
        tasks = list(self._background_tasks)
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info(f"GitHub Webhook: Cancelled {len(tasks)} background task(s)")

    async def send_message(self, message: str, target_umos: list[str] | None = None):
        """按仓库路由得到的 UMO 列表分发消息；未指定时回落到 target_umo."""
        if target_umos is None:
            target_umos = [self.cfg.target_umo]

        targets = [umo for umo in target_umos if umo]
        if not targets:
            if target_umos:
                logger.error(
                    "GitHub Webhook: Cannot send message - target_umo not configured"
                )
            else:
                logger.info(
                    "GitHub Webhook: No target UMO matched this repository, "
                    "message dropped"
                )
            return

        for target_umo in targets:
            await self._send_to_target(target_umo, message)

    async def _send_to_target(self, target_umo: str, message: str):
        """向单个 UMO 发送纯文本消息；单目标失败不影响其它目标."""
        try:
            message_chain = api.MessageChain([Plain(message)])
            result = await self.context.send_message(target_umo, message_chain)
            logger.info(
                f"GitHub Webhook: Message sent to {target_umo}, result: {result}"
            )
            if not result:
                logger.warning(f"GitHub Webhook: Platform not found for {target_umo}")
        except Exception as e:
            # 记录完整错误信息但不传播异常
            logger.error(f"GitHub Webhook: Failed to send message to {target_umo}: {e}")
            logger.error(f"GitHub Webhook: Error type: {type(e).__name__}")
            logger.error(f"GitHub Webhook: Error details: {str(e)}")

    async def terminate(self):
        """插件卸载时释放 HTTP 服务、后台任务与运行器资源."""
        logger.info("GitHub Webhook: Shutting down server...")
        await self._cancel_background_tasks()
        if self.site:
            await self.site.stop()
            logger.info("GitHub Webhook: Server stopped")
        if self.runner:
            await self.runner.cleanup()
            logger.info("GitHub Webhook: Runner cleaned up")
