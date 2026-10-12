# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [0.0.1] - 2026-10-12

### 更改
- 插件更名为 **GitPulse**：插件 ID 由 `astrbot_plugin_github_webhook` 调整为 `astrbot_plugin_gitpulse`，展示名调整为「GitPulse 插件」
- 版本号重置为 `0.0.1`，仓库地址更新为 `https://github.com/xiaomizhoubaobei/astrbot_plugin_gitpulse`
- 同步更新 README、docs、templates 与脚本中的插件名、配置文件名与示例输出

## [0.4.1] - 2026-02-06

### 修复
- **#2**: 修复 `metadata.yaml` (`astrbot_plugin_github_webhook`) 与 `main.py` 的 `@register` 装饰器 (`github_webhook`) 之间的插件注册 ID 不匹配问题
- 该不匹配导致 AstrBot 注册了两个独立的插件，创建了一个无法正常卸载的空白/损坏的重复插件条目

### 更改
- 统一所有配置文件中的插件 ID 为 `astrbot_plugin_github_webhook`

## [0.4.0] - 2026-01-26

### 新增
- LLM 智能消息生成支持，支持自定义提示词
- 请求速率限制配置，防止消息轰炸
- Webhook 签名验证，增强安全性
- 全面的错误处理和日志记录
- 支持 Push、Issues 和 Pull Request 事件

### 更改
- 改进消息格式化，提升可读性
- 增强配置验证

## [0.3.0] - 2026-01-21

### 新增
- GitHub Webhook 插件首次发布
- 基础 Webhook 服务器实现
- 支持 GitHub Push、Issues 和 Pull Request 事件
- Webhook 服务器配置（端口、target_umo）
- 基础消息转发到聊天平台
