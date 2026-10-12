"""Pytest 公共配置：在缺少 AstrBot 运行时环境时注入最小桩.

本插件的源码依赖 ``astrbot.api``（由 AstrBot 宿主提供）。单元测试不应强依赖
完整宿主，故此处仅在 ``astrbot`` 不可导入时，注册一组轻量桩模块，
让被测模块（formatter / config）可以在纯净环境下被导入。
真实环境中 astrbot 已存在，本文件不做任何覆盖。
"""

import json
import os
import sys
from types import ModuleType


class _StubAstrBotConfig(dict):
    """最小可用的 AstrBotConfig 桩（对齐宿主真实签名）.

    宿主实现为 ``AstrBotConfig(config_path, default_config, schema)``：
    从 JSON 配置文件加载配置，并以映射形式暴露配置项。此处在桩中保留
    **同名同序** 的构造签名（首参为配置文件路径），但以内存 dict 承载默认值，
    使得「把 dict 当 config_path 传」这类误用能在纯净环境下同样暴露出来，
    而不是被宽松的 dict 桩悄悄吞掉。
    """

    def __init__(self, config_path="", default_config=None, schema=None) -> None:
        super().__init__()
        # 与宿主一致：首参必须是路径（str / os.PathLike），否则视为误用
        if (
            config_path
            and not isinstance(config_path, (str, bytes))
            and not hasattr(config_path, "__fspath__")
        ):
            raise TypeError(
                "AstrBotConfig 的第一个参数应为配置文件路径（str / PathLike），"
                f"实际收到 {type(config_path).__name__}"
            )
        self.config_path = config_path
        self.default_config = default_config or {}
        self.schema = schema

        # 与宿主一致：从配置文件加载，缺省项由 default_config 补齐
        loaded: dict = {}
        if config_path and os.path.exists(str(config_path)):
            with open(str(config_path), encoding="utf-8-sig") as f:
                loaded = json.loads(f.read())
        merged = dict(self.default_config)
        merged.update(loaded)
        self.update(merged)

    def __getattr__(self, item):
        """与宿主一致：缺失的配置项返回 None，而非抛 AttributeError."""
        try:
            return self[item]
        except KeyError:
            return None


class _StubLogger:
    """最小可用的日志桩."""

    def debug(self, *args, **kwargs) -> None:
        pass

    def info(self, *args, **kwargs) -> None:
        pass

    def warning(self, *args, **kwargs) -> None:
        pass

    def error(self, *args, **kwargs) -> None:
        pass


def _install_astrbot_stubs() -> None:
    """在 astrbot 缺失时安装最小桩，避免测试收集阶段导入失败."""
    if "astrbot" in sys.modules:
        return

    try:
        __import__("astrbot")

        return
    except ImportError:
        pass

    astrbot = ModuleType("astrbot")
    api = ModuleType("astrbot.api")
    # 动态装配桩属性：ModuleType 上直接用属性赋值，mypy 无法静态识别，
    # 故统一走 setattr（等价语义，类型检查可通过）。
    setattr(api, "logger", _StubLogger())
    setattr(api, "AstrBotConfig", _StubAstrBotConfig)
    setattr(api, "all", ModuleType("astrbot.api.all"))
    setattr(api, "message_components", ModuleType("astrbot.api.message_components"))

    star = ModuleType("astrbot.api.star")

    class _Star:
        def __init__(self, context=None):
            self.context = context

    class _Context:
        pass

    def _register(*args, **kwargs):
        def _decorator(cls):
            return cls

        return _decorator

    setattr(star, "Star", _Star)
    setattr(star, "Context", _Context)
    setattr(star, "register", _register)
    setattr(api, "star", star)

    class _Plain:
        def __init__(self, *args, **kwargs):
            pass

    message_components = getattr(api, "message_components")
    setattr(message_components, "Plain", _Plain)

    setattr(astrbot, "api", api)
    sys.modules["astrbot"] = astrbot
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.all"] = getattr(api, "all")
    sys.modules["astrbot.api.star"] = star
    sys.modules["astrbot.api.message_components"] = message_components


_install_astrbot_stubs()
