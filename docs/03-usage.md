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

### 无 commit 的 ref 生命周期事件

GitHub 在创建/删除分支或 Tag、以及部分 force-push 的 push 事件中，`commits`
列表为空。插件会按 `created` / `deleted` / `forced` 标记转发这类事件，不再静默丢弃。

**删除分支**：

```
🗑️ GitHub Push Event
👤 username deleted branch feature/old in owner/repo
```

**创建分支 / Tag**（`after` 非全 0 时附带目标提交）：

```
🌱 GitHub Push Event
👤 username created branch feature/new in owner/repo
🔗 Commit: abc1234
```

```
🏷️ GitHub Push Event
👤 username created tag v1.2.3 in owner/repo
🔗 Commit: abc1234
```

**Force push（无 commit）**：

```
🔄 GitHub Push Event
👤 username force-pushed branch main in owner/repo
```

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

## Release 事件

**默认模板**：
```
🚀 GitHub Release Event
👤 username published release in owner/repo
🏷️ v1.2.3: Big Release
📎 https://github.com/owner/repo/releases/tag/v1.2.3
```

预发布使用 🧪，删除使用 🗑️，取消发布使用 🚫；未知动作回落到 📦。

## Workflow Run 事件

只转发 `completed` 状态，`requested` / `in_progress` 会被忽略，避免刷屏。

**成功**：
```
✅ GitHub Workflow Run Event
👤 username completed workflow in owner/repo
⚙️ CI · push · main
📊 Result: success
📎 https://github.com/owner/repo/actions/runs/123456
```

**失败**：
```
❌ GitHub Workflow Run Event
👤 username completed workflow in owner/repo
⚙️ CI · push · main
📊 Result: failure
📎 https://github.com/owner/repo/actions/runs/123456
```

其它结论：`cancelled` ⚪、`skipped` ⏭️、`timed_out` ⏰、`action_required` ⚠️、
`neutral` ➖、`stale` 🧹、`startup_failure` 💥。

## Star 事件

同时支持新版 `star` 事件与旧版 `watch` 事件。

**新增 Star**：
```
⭐ GitHub Star Event
👤 username starred owner/repo
⭐ Stars: 128
📎 https://github.com/owner/repo
```

**取消 Star**（`star` 事件的 `deleted` 动作）：
```
💔 GitHub Star Event
👤 username unstarred owner/repo
⭐ Stars: 127
📎 https://github.com/owner/repo
```

## Issue Comment 事件

Issue 与 PR 评论共用 `issue_comment` 事件；评论正文取首行并截断到 100 字符。

**Issue 评论**：
```
💬 GitHub Issue Comment Event
👤 username commented on issue in owner/repo
📋 Issue #42: Bug report
💭 Looks good to me
📎 https://github.com/owner/repo/issues/42#issuecomment-123456
```

**PR 评论**：
```
💬 GitHub Issue Comment Event
👤 username commented on pr in owner/repo
📋 PR #10: Add new feature
💭 LGTM
📎 https://github.com/owner/repo/pull/10#issuecomment-123456
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

> LLM 输出会校验模板中的核心链接（commit / issue / PR URL 等）：只要丢失任意一个，就自动回退到模板消息，避免群通知信息失真。

查看 [配置文档](02-configuration.md) 了解 LLM 相关配置详情。

### 使用 Prompt 示例

在 `templates/` 目录中提供了预置的系统提示词：

- [默认 Prompt](../templates/default.md) - 通用 GitHub 事件消息生成提示词

使用方法：
1. 打开 Prompt 文件
2. 复制提示词内容
3. 粘贴到插件配置的 `agent_system_prompt` 字段

## 相关文档

- [配置说明](02-configuration.md) - 了解如何配置消息生成方式
- [故障排查](05-troubleshooting.md) - LLM 相关问题排查
