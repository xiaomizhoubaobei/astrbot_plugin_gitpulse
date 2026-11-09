"""GitPulse plugin configuration management."""

from __future__ import annotations

import re
from collections.abc import MutableMapping
from functools import cached_property
from typing import Any, get_type_hints

from astrbot.api import AstrBotConfig, logger

from ..services.repo_router import parse_repo_routes
from .constants import DEFAULT_BIND_HOST, DEFAULT_WEBHOOK_PATH

# 仅允许普通的 URL 路径字符：排除空白、查询串、片段以及 aiohttp 的
# 动态路由标记（{}），避免用户配置意外生成通配路由。
_WEBHOOK_PATH_PATTERN = re.compile(r"^/[A-Za-z0-9._~!$&'()*+,;=:@%/-]*$")


def normalize_webhook_path(raw: Any) -> str:
    """归一化 webhook 路由路径：补前导斜杠、去尾斜杠，非法值回落默认值."""
    if not isinstance(raw, str):
        return DEFAULT_WEBHOOK_PATH

    path = raw.strip()
    if not path:
        return DEFAULT_WEBHOOK_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    path = path.rstrip("/") or "/"

    if not _WEBHOOK_PATH_PATTERN.fullmatch(path):
        logger.warning(
            f"GitHub Webhook: Invalid webhook_path {raw!r}, "
            f"falling back to {DEFAULT_WEBHOOK_PATH}"
        )
        return DEFAULT_WEBHOOK_PATH
    return path


class ConfigNode:
    """配置节点：dict → 强类型属性访问（极简版）."""

    _SCHEMA_CACHE: dict[type, dict[str, type]] = {}

    # 各类型的兜底默认值：宿主 AstrBotConfig 对缺失键返回 None，
    # 直接用 None 参与比较/拼接会抛 TypeError，故按声明类型给出零值兜底。
    _TYPE_DEFAULTS: dict[type, Any] = {
        int: 0,
        float: 0.0,
        bool: False,
        str: "",
        list: None,
        dict: None,
    }

    @classmethod
    def _schema(cls) -> dict[str, type]:
        """获取（并缓存）类上声明的字段类型表."""
        return cls._SCHEMA_CACHE.setdefault(cls, get_type_hints(cls))

    @classmethod
    def _default_for(cls, key: str) -> Any:
        """按类型注解给出缺失字段的兜底默认值（无法判定时返回 None）."""
        declared = cls._schema().get(key)
        if declared in (list, dict):
            # 可变类型单独创建，避免多个实例共享同一对象
            return declared()
        return cls._TYPE_DEFAULTS.get(declared)  # type: ignore[arg-type]

    def __init__(self, data: MutableMapping[str, Any]):
        """以底层映射初始化节点，并对缺失字段给出告警."""
        object.__setattr__(self, "_data", data)
        for key in self._schema():
            if key in data:
                continue
            if hasattr(self.__class__, key):
                continue
            logger.warning(f"[config:{self.__class__.__name__}] 缺少字段: {key}")

    def __getattr__(self, key: str) -> Any:
        """按字段名读取配置值，缺失时按声明类型兜底."""
        if key in self._schema():
            value = self._data.get(key)
            # 宿主对缺失键返回 None：此处按声明类型兜底，避免下游 TypeError
            return self._default_for(key) if value is None else value
        raise AttributeError(key)

    def __setattr__(self, key: str, value: Any) -> None:
        """写入配置值：已知字段落到映射，其余落到实例属性."""
        if key in self._schema():
            self._data[key] = value
            return
        object.__setattr__(self, key, value)


class PluginConfig(ConfigNode):
    """插件自定义配置."""

    port: int
    target_umo: str
    webhook_secret: str
    rate_limit: int
    enable_agent: bool
    llm_provider_id: str
    agent_timeout: int
    agent_system_prompt: str

    @property
    def bind_host(self) -> str:
        """Webhook 服务监听地址：未配置时回落到默认监听地址.

        不对该字段做类型注解，避免与 property 同名冲突；宿主缺键时
        ``_data`` 取不到值，此处兜底到 ``DEFAULT_BIND_HOST``，
        避免把空串交给 ``web.TCPSite`` 导致启动即失败。
        """
        raw = self._data.get("bind_host")
        return str(raw) if raw else DEFAULT_BIND_HOST

    @property
    def webhook_path(self) -> str:
        """Webhook 路由路径：未配置或非法时回落到 ``/webhook``.

        与 ``bind_host`` 相同，不做类型注解，避免与 property 同名冲突。
        配置成 ``/github/webhook`` 这类前缀后，可与 AstrBot 共用同一个
        域名和反向代理入口。
        """
        return normalize_webhook_path(self._data.get("webhook_path"))

    @cached_property
    def repo_routes(self) -> dict[str, list[str]]:
        """仓库到目标 UMO 列表的路由映射（``repo_routes`` 配置解析结果）.

        配置为 JSON 文本，WebUI 无法渲染任意映射类型，故以 text 存储。
        解析失败或条目非法时跳过该条目并告警，不影响 ``target_umo`` 兜底。
        """
        return parse_repo_routes(self._data.get("repo_routes"))

    def __init__(self, cfg: AstrBotConfig):
        """基于 AstrBotConfig 装载配置并输出启动日志."""
        super().__init__(cfg)
        # 配置验证和完整日志
        logger.info("=" * 60)
        logger.info("GitHub Webhook: Configuration loaded")
        logger.info(f"  bind_host: {self.bind_host}:{self.port}")
        logger.info(f"  webhook_path: {self.webhook_path}")
        logger.info(f"  target_umo: {self.target_umo}")
        logger.info(f"  repo_routes: {len(self.repo_routes)} route(s)")
        logger.info(f"  enable_agent: {self.enable_agent}")
        logger.info(f"  llm_provider_id: {self.llm_provider_id or '(default)'}")
        logger.info(f"  agent_timeout: {self.agent_timeout}s")
        prompt_length = len(self.agent_system_prompt) if self.agent_system_prompt else 0
        logger.info(f"  agent_system_prompt: {prompt_length} chars")
        logger.info(f"  rate_limit: {self.rate_limit} req/min")
        logger.info("=" * 60)

        if not self.target_umo:
            logger.warning(
                "GitHub Webhook: target_umo not configured, plugin may not work!"
            )

        if self.webhook_secret:
            logger.info("GitHub Webhook: Signature verification enabled")
        else:
            logger.warning(
                "GitHub Webhook: No webhook_secret configured, "
                "signature verification disabled (not recommended for production)"
            )

        if self.rate_limit > 0:
            logger.info(
                f"GitHub Webhook: Rate limiting enabled "
                f"({self.rate_limit} requests/minute)"
            )

        # LLM 配置日志
        if self.enable_agent:
            logger.info("GitHub Webhook: LLM mode enabled")
            if self.llm_provider_id:
                logger.info(
                    f"GitHub Webhook: Using LLM provider ID: {self.llm_provider_id}"
                )
            else:
                logger.info("GitHub Webhook: Using default LLM provider")
            if self.agent_system_prompt:
                logger.info("GitHub Webhook: Custom system prompt configured")
        else:
            logger.info("GitHub Webhook: LLM mode disabled, using default templates")
