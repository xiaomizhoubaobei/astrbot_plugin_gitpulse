"""GitPulse plugin - entry point."""

import sys
from pathlib import Path

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register

# 将 src/ 添加到 sys.path，使 Python 能找到模块。
# 必须在导入 src.* 之前执行，故此处保留模块级语句（noqa 抑制 E402）。
sys.path.insert(0, str(Path(__file__).parent))

from src.commands.github_command import (  # noqa: E402
    ACTION_COMMAND_ALIASES,
    COMMAND_HELP,
    COMMAND_PREFIXES,
    parse_command_line,
    run_github_command,
)
from src.core.plugin import GitHubWebhookPlugin  # noqa: E402

# 群内快捷指令的别名：/github 为主指令，gh 是它的短写（解析时同样剥掉），
# merge / close / reopen / comment 首词即动作。别名静态注册（AstrBot 的指令
# 注册不支持运行时增删），是否真正放行由 enable_github_commands 配置决定，
# 未启用时只回一句提示不执行任何写操作。
_COMMAND_ALIASES = {*ACTION_COMMAND_ALIASES, *(COMMAND_PREFIXES - {"github"})}


@register(
    "astrbot_plugin_gitpulse",
    "TatsukiMengChen",
    "GitPulse - 接收 GitHub 事件并转发到聊天平台",
    "0.0.1",
)
class PluginEntry(Star):
    """插件入口类，代理到实际的 GitHubWebhookPlugin 类."""

    def __init__(self, context: Context, config):
        """初始化插件，将调用传递给实际的插件类."""
        self._plugin = GitHubWebhookPlugin(context, config)

    async def initialize(self):
        """插件初始化."""
        await self._plugin.start_server()

    async def handle_webhook(self, request):
        """处理 webhook 请求."""
        return await self._plugin.handle_webhook(request)

    async def terminate(self):
        """插件销毁."""
        await self._plugin.terminate()

    @filter.command("github", alias=_COMMAND_ALIASES)
    async def github_command(self, event: AstrMessageEvent):
        """群内快捷操作 GitHub：merge / close / reopen / comment.

        参数自行解析（不用框架的自动拆参），以便评论正文能保留空格与换行。
        """
        cfg = self._plugin.cfg
        if not cfg.enable_github_commands:
            yield event.plain_result(
                "群内 GitHub 指令未启用，请维护者在插件配置中开启 enable_github_commands"
            )
            return

        action, target, extra = parse_command_line(event.get_message_str())
        if not action:
            yield event.plain_result(COMMAND_HELP)
            return
        if action in ("help", "?", "-h"):
            yield event.plain_result(COMMAND_HELP)
            return

        # 写操作会真实改动仓库，默认只允许 AstrBot 管理员触发。
        if cfg.github_command_admin_only and not event.is_admin():
            yield event.plain_result(
                "⚠️ 只有管理员可以执行群内 GitHub 写操作，请联系管理员处理"
            )
            return

        result = await run_github_command(action, cfg, target, extra)
        yield event.plain_result(result)
