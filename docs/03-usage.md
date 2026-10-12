# 使用示例

本文档展示各种 GitHub 事件的消息格式示例。

## Push 事件

### 消息格式（使用默认模板）

一次 push 携带多个 commit 时，会逐条列出全部提交（默认最多展示 5 条，
超出部分折叠为「…等 N 条」摘要），不再只转发第一条，同时控制消息长度。

**单条提交**（常见情况，保持紧凑格式）：

```
📦 GitHub Push Event
👤 username pushed to owner/repo
🌿 Branch: main
💬 Fix webhook message sending issue
🔗 Commit: abc1234
📎 https://github.com/owner/repo/commit/abc1234
```

**多条提交**（每条压成一行，超上限折叠）：

```
📦 GitHub Push Event
👤 username pushed to owner/repo
🌿 Branch: main
🔢 8 commits
  • abc1234 feat: 新增 A 功能
  • def5678 fix: 修复 B 问题
  • 9abcdef docs: 更新说明
  • 0123456 chore: 调整依赖
  • 789abcd refactor: 重构 C 模块
  …等 3 条
📎 https://github.com/owner/repo/commit/abcdef0
```

- 最多逐条展示 5 条提交（`MAX_DISPLAYED_COMMITS`，见 `src/formatters/push_formatter.py`）。
- 每条只保留短 ID 与提交信息首行，超长信息自动截断，避免撑爆消息。
- 汇总链接指向本次推送的最新提交。

### LLM 生成示例

```
📦 新代码推送
TatsukiMengChen 向 astrbot_plugin_gitpulse 推送了代码

💬 修复 webhook 消息发送问题
🌿 分支：main
🔗 https://github.com/owner/repo/commit/abc1234
```

## Issues 事件

### Issue 打开

**默认模板**：
```
🆕 GitHub Issue Event
👤 username opened issue in owner/repo
📋 Issue #42: Bug report
📎 https://github.com/owner/repo/issues/42
```

**LLM 生成**：
```
🆕 新问题报告
TatsukiMengChen 在 owner/repo 创建了新问题

📋 #42: Bug report
📎 https://github.com/owner/repo/issues/42
```

### Issue 关闭

**默认模板**：
```
✅ GitHub Issue Event
👤 username closed issue in owner/repo
📋 Issue #42: Bug report
📎 https://github.com/owner/repo/issues/42
```

### Issue 重新打开

```
🔄 GitHub Issue Event
👤 username reopened issue in owner/repo
📋 Issue #42: Bug report
📎 https://github.com/owner/repo/issues/42
```

## Pull Request 事件

### PR 打开

**默认模板**：
```
🆕 GitHub Pull Request Event
👤 username opened PR in owner/repo
📋 PR #10: Add new feature
🌿 feature → main
📎 https://github.com/owner/repo/pull/10
```

**LLM 生成**：
```
🔀 新拉取请求
TatsukiMengChen 在 owner/repo 提交了 PR

📋 #10: Add new feature
🌿 feature → main
📎 https://github.com/owner/repo/pull/10
```

### PR 合并

**默认模板**：
```
✅ GitHub Pull Request Event
👤 username closed PR in owner/repo
📋 PR #10: Add new feature
🌿 feature → main
📎 https://github.com/owner/repo/pull/10
```

### PR 重新打开

```
🔄 GitHub Pull Request Event
👤 username reopened PR in owner/repo
📋 PR #10: Add new feature
🌿 feature → main
📎 https://github.com/owner/repo/pull/10
```

## Ping 事件

GitHub 在配置 Webhook 时会自动发送 Ping 事件，插件会自动响应：

```
[INFO] GitHub Webhook: Received event type: ping
```

## 自定义消息

### 使用 LLM 生成

配置 `enable_agent = true` 后，插件会使用 LLM 生成个性化消息。

1. 在 AstrBot WebUI 中配置好 LLM Provider
2. 在插件配置中启用 `enable_agent`
3. （可选）配置 `agent_system_prompt` 自定义消息风格
4. （可选）配置 `llm_provider_id` 指定使用的模型

查看 [配置文档](02-configuration.md) 了解 LLM 相关配置详情。

### 使用 Prompt 示例

在 `prompts/` 目录中提供了预置的系统提示词：

- [默认 Prompt](../prompts/default.md) - 通用 GitHub 事件消息生成提示词

使用方法：
1. 打开 Prompt 文件
2. 复制提示词内容
3. 粘贴到插件配置的 `agent_system_prompt` 字段

## 相关文档

- [配置说明](02-configuration.md) - 了解如何配置消息生成方式
- [故障排查](05-troubleshooting.md) - LLM 相关问题排查
