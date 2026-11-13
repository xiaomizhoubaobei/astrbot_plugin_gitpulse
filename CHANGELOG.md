# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

## [0.0.3] - 2026-11-14

### 新增
- 新增 Push / PR 的 AI 代码变更摘要：拉取 GitHub diff 后由 LLM 生成中文摘要，
  作为独立消息发送；摘要失败、超时或输出为空时静默跳过，不影响模板消息
- 新增 `scripts/vector_ingest.py` 向量知识库导入脚本：纯标准库实现百炼
  embedding 调用与 OSS V4 签名，用于沉淀 CNB 项目知识与踩坑记录

### 修复
- 无 commit 的 push（分支 / Tag 创建、删除、force-push）不再被静默丢弃
- 端口被占用时按指数退避重试，并给出可操作的启动错误提示
- 修复向量导入脚本的 Bandit B310、DevSkim 告警与批次参数边界校验
- 修正 Release Notes 的 CHANGELOG 提取版本匹配与越界问题

### 更改
- 事件处理改为先返回 HTTP 200，再在后台完成格式化、LLM 改写与发送，
  避免 GitHub 10 秒超时重试导致重复推送
- 移除 PR 事件中的 PR Lens 流水线，PR 门禁仅保留单元测试
- 同步更新 pre-commit、pytest-asyncio 与 action-gh-release 等工具版本

## [0.0.2] - 2026-11-09

### 修复
- 修复 Release Notes「安装方法」代码块未闭合，导致「证据包说明」被整体渲染进代码块的问题

### 更改
- 按当前 black 版本格式化 `tests/test_event_filter.py` 与 `tests/test_release_formatter.py`（仅格式，无逻辑变化）

## [0.0.1] - 2026-10-12

### 更改
- 插件更名为 **GitPulse**：插件 ID 由 `astrbot_plugin_github_webhook` 调整为 `astrbot_plugin_gitpulse`，展示名调整为「GitPulse 插件」
- 版本号重置为 `0.0.1`，仓库地址更新为 `https://github.com/xiaomizhoubaobei/astrbot_plugin_gitpulse`
- 同步更新 README、docs、templates 与脚本中的插件名、配置文件名与示例输出
