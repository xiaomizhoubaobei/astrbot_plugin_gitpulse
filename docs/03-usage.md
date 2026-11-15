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

### AI 代码变更摘要

配置 `enable_code_review = true` 后，插件会在 `push` 与新建 / 重新打开 / 新增提交的 PR 事件中拉取 GitHub diff，调用 LLM 生成一份人类可读的中文摘要，并作为独立消息紧随事件通知之后推送：

```
🤖 AI 变更摘要
- 新增登录接口的参数校验，避免空密码提交
- 修复退出登录时偶发的空指针崩溃
- 调整 CI 缓存策略，构建时间预计缩短一半
```

特点：

- 摘要与模板消息分开发送，模板消息永远不会因为摘要失败而丢失
- 单条摘要最多 1000 字符，diff 送入 LLM 前按 `code_review_max_diff_chars` 截断
- 仅对可评审的 PR 动作生效，关闭 / 打标签等动作不会触发额外 LLM 调用
- 私有仓库需要配置 `github_token`；公开仓库可留空

> 摘要链路只从 `api.github.com` 拉取 diff，仓库名与提交 SHA 会先做白名单校验，不接受 payload 中的任意 URL。

### CI 失败智能归因

开启 `enable_ci_failure_analysis` 并配置具备 `actions:read` 权限的 `github_token` 后，`workflow_run` 以 `failure` / `timed_out` / `startup_failure` 结束时，插件会抓取失败 job 的日志尾部，调用 LLM 分析根因，并作为独立消息紧随事件通知之后推送：

```
🤖 CI 失败归因
📦 owner/repo · CI · failure
最可能的原因是构建环境缺少新增依赖。
- 日志末尾出现 `ModuleNotFoundError: No module named 'xyz'`，建议把 xyz 加入依赖清单。
- 失败步骤为 Run tests，可先在本地复现该导入错误。
📎 https://github.com/owner/repo/actions/runs/123
```

特点：

- 只分析失败结论（`failure` / `timed_out` / `startup_failure`），`cancelled` / `skipped` 等不会触发
- 最多分析 3 个失败 job；日志只取尾部，送入 LLM 前按 `ci_failure_max_log_chars` 截断
- 进入 LLM 前会对常见 Token / 密码 / 私钥格式做尽力脱敏
- 归因失败（无 Token、无 LLM Provider、GitHub API 或 LLM 异常）只跳过归因，不影响原有模板消息
- `github_token` 未配置时直接跳过；私有仓库必须提供具备 `actions:read` 的 Token

> 链路只从 `api.github.com` 拉取 jobs / 日志，仓库名与 run、job 编号会先做校验；跟随日志签名地址时不携带 Token。

### 群内 GitHub 快捷指令

开启 `enable_github_commands` 后，可以直接在群里操作 GitHub，无需切到网页：

```
/github merge 12            合并 PR（默认 squash）
/github merge 12 rebase     指定合并方式：squash / merge / rebase
/github close 12            关闭 Issue 或 PR
/github reopen 12           重新打开 Issue 或 PR
/github comment 12 已修复    在 Issue / PR 下发表评论
```

编号支持三种写法：`12`、`#12`、`owner/repo#12`。未写仓库时使用
`github_command_default_repo`，两者都没有则提示补全。

成功回显示例：

```
🔀 已完成合并 PR：owner/repo#12
合并方式：squash · 合并提交 1a2b3c4
🔗 https://github.com/owner/repo/pull/12
```

失败时统一以 `⚠️` 开头并给出可读原因（如 Token 权限不足、PR 存在冲突、
仓库未在授权列表内），不会把 GitHub 的原始报错或堆栈甩到群里。

**安全边界（务必了解）**：

- 写操作默认**仅 AstrBot 管理员**可执行（`github_command_admin_only`，默认开启）
- 只有 `github_command_repos` 里显式列出的仓库才允许操作；**留空即全部拒绝**，
  不会因为漏配就放开整账号权限
- 需要配置具备对应仓库写权限的 `github_token`，未配置时直接拒绝并提示维护者
- 请求路径由「校验过的仓库名 + 编号」拼出，不接受任何外部传入的 URL

> 注：`/github` 同时注册了 `gh`、`merge`、`close`、`reopen`、`comment` 几个别名，
> 因此 `/merge #12` 也可用；这类短别名与其他插件撞名的可能性更高，可按需改用完整写法。

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
