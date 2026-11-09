# 更新日志

本项目的所有重要变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 规范。

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
