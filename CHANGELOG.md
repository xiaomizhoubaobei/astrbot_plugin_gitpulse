# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [未发布]

### 新增
- CI 同步到 GitHub 镜像仓库时开启强制推送（`.cnb.yml` 的 `git-sync` 步骤新增 `force: true`）
  - 即使 CNB 与镜像仓库历史分叉，也能以 CNB 侧为准强制覆盖，避免同步失败或半同步状态

### 修复
- AI 审计示例（`.github/workflows/ai-audit-scheduled.yml`）：公告接口失败不再拖垮整批告警处理
  - `GH_PAT` 缺 `Repository security advisories` 权限时，列公告/建公告的失败不再被
    `set -euo pipefail` 放大成 job 级退出，改为**跳过该条、继续处理后续告警**，并在结尾汇总计入「跳过 N 条」
  - 列公告失败改「先赋值、由退出码分流」，避免 `if` 条件上下文吞掉失败后把诊断文本当作公告 ID 写进公开 Issue
  - 建公告失败改判退出码（403/404 时 `curl -f` 非 0 但响应体仍是 JSON，只判空串会被 `pipefail` 终止）
  - 两个去重辅助函数新增翻页硬上限 `MAX_DEDUPE_PAGES`，防上游异常时无限翻页拖到 Actions 超时

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
