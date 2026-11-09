# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [0.0.2] - 2026-11-09

### 新增
- 新增 `webhook_path` 配置，支持自定义 Webhook 接收路径，并可与 AstrBot 共用反向代理入口
- 新增 `repo_routes` 多仓库路由，支持按仓库、`owner/*` 组织通配与 `*` 全局通配把事件分发到多个会话
- 新增 `release`、`workflow_run`、`star` / `watch`、`issue_comment` 事件通知
- 新增 `enabled_events` 事件订阅白名单，可按需关闭事件类型避免消息轰炸

### 修复
- LLM 输出丢失模板核心链接时自动回退到模板消息
- 配置 `webhook_secret` 后，缺少 `X-Hub-Signature-256` 签名头的请求返回 401
- 修正 PR 同步动作常量，恢复 `synchronize` 消息的 🔀 图标

### 更改
- `aiohttp` 最低版本提升至 `>=3.14.4`
- 补齐 formatter、handler、plugin 与 LLM 服务单元测试，覆盖率口径与 CI 对齐

## [0.0.1] - 2026-10-12

### 更改
- 插件更名为 **GitPulse**：插件 ID 由 `astrbot_plugin_github_webhook` 调整为 `astrbot_plugin_gitpulse`，展示名调整为「GitPulse 插件」
- 版本号重置为 `0.0.1`，仓库地址更新为 `https://github.com/xiaomizhoubaobei/astrbot_plugin_gitpulse`
- 同步更新 README、docs、templates 与脚本中的插件名、配置文件名与示例输出
