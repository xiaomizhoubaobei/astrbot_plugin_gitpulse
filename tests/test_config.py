"""Tests for GitHub Webhook plugin."""

import json

from astrbot.api import AstrBotConfig

from src.core.config import PluginConfig
from src.core.constants import DEFAULT_PORT


def _make_config(tmp_path, config_data: dict) -> AstrBotConfig:
    """按宿主真实用法构造 AstrBotConfig.

    宿主签名为 ``AstrBotConfig(config_path, default_config, schema)``：
    首参是 **配置文件路径**，而非配置字典。此处把配置写入临时 JSON 文件，
    再交给 AstrBotConfig 加载，避免「把 dict 当路径传」的误用。
    """
    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps(config_data), encoding="utf-8")
    return AstrBotConfig(config_path=str(config_file))


def test_config_default_values(tmp_path):
    """测试配置类的默认值"""
    config_data = {
        "port": DEFAULT_PORT,
        "target_umo": "",
        "webhook_secret": "",
        "rate_limit": 10,
        "enable_agent": False,
        "llm_provider_id": "",
        "agent_timeout": 60,
        "agent_system_prompt": "",
    }
    config = _make_config(tmp_path, config_data)
    plugin_config = PluginConfig(config)

    assert plugin_config.port == DEFAULT_PORT
    assert plugin_config.rate_limit == 10
    assert plugin_config.enable_agent is False


def test_config_with_custom_values(tmp_path):
    """测试配置类的自定义值"""
    config_data = {
        "port": 9000,
        "target_umo": "test:GroupMessage:123456",
        "webhook_secret": "test_secret",
        "rate_limit": 20,
        "enable_agent": True,
        "llm_provider_id": "test_provider",
        "agent_timeout": 90,
        "agent_system_prompt": "Custom prompt",
    }
    config = _make_config(tmp_path, config_data)
    plugin_config = PluginConfig(config)

    assert plugin_config.port == 9000
    assert plugin_config.target_umo == "test:GroupMessage:123456"
    assert plugin_config.webhook_secret == "test_secret"
    assert plugin_config.enable_agent is True


def test_config_tolerates_missing_keys():
    """宿主缺失键返回 None 时，配置类应给出类型安全的兜底值而非 None.

    回归：真实 AstrBotConfig 对缺失键返回 None，旧实现直接参与
    ``rate_limit > 0`` 比较会抛 ``TypeError``，导致插件加载失败。
    """
    # 模拟宿主把插件自有键全部剔除（无 schema 校验时的真实行为）
    plugin_config = PluginConfig({"some_unrelated_key": 1})

    assert plugin_config.port == 0
    assert plugin_config.rate_limit == 0
    assert plugin_config.agent_timeout == 0
    assert plugin_config.enable_agent is False
    assert plugin_config.target_umo == ""
    assert plugin_config.webhook_secret == ""
    # 不再抛 TypeError，且可安全参与比较
    assert (plugin_config.rate_limit > 0) is False
