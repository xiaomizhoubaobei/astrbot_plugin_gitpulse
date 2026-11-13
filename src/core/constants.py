"""GitPulse plugin constants."""

# Default port for webhook server
DEFAULT_PORT = 8080

# Webhook 服务的默认监听地址。
# 默认监听全部网卡：GitHub 的 webhook 请求来自外部（公网或内网其它主机），
# 绑定回环地址会直接收不到请求。暴露面由配置项 bind_host 收敛（例如改用
# 127.0.0.1 + 反向代理），故此处保留通配监听为默认值。
# bandit 的 B104 对任何取值为 0.0.0.0 的字符串常量都会告警，这里是有意为之的
# 默认值而非疏漏，按 bandit 官方方式就地标注 nosec。
DEFAULT_BIND_HOST = "0.0.0.0"  # nosec B104

# Webhook 服务启动时端口被占用的重试策略。
# 热重载 / AstrBot 重启后旧端口可能仍被占用（其它进程持有，或少数平台
# 未及时释放 TIME_WAIT），直接启动会抛 OSError 让插件加载失败，因此按
# 指数退避重试有限次数；仍失败则给出可操作的错误提示。
SERVER_START_MAX_ATTEMPTS = 3
SERVER_START_RETRY_BASE_DELAY = 1.0  # seconds，第 n 次重试等待 base * 2^(n-1)

# Webhook 路由路径的默认值。
# 允许用户改成 /github/webhook 之类的前缀，便于与 AstrBot 共用同一个域名
# 和反向代理；默认值保持向后兼容。
DEFAULT_WEBHOOK_PATH = "/webhook"

# Default rate limit (requests per minute)
DEFAULT_RATE_LIMIT = 10

# Default LLM timeout (seconds)
DEFAULT_LLM_TIMEOUT = 60

# AI 代码变更摘要（可选增强）：把 GitHub diff 交给 LLM 生成人类可读摘要。
# 单次送入 LLM 的 diff 字符上限；超出部分截断并在提示词中标注，避免长 PR
# 撑爆上下文（字符数限制与下面按字节读取的上限是两道独立闸门）。
DEFAULT_CODE_REVIEW_MAX_DIFF_CHARS = 12000

# 拉取 diff 的 HTTP 超时（秒）。独立于 LLM 的 agent_timeout：拉取失败应快速
# 放弃，把时间留给模板消息，而不是让后台任务长期挂起。
DIFF_FETCH_TIMEOUT = 15.0

# diff 响应体读取上限（字节）：超大 diff 只读前 512 KiB，防止内存被撑爆。
MAX_DIFF_RESPONSE_BYTES = 512 * 1024

# 单条摘要的输出上限，避免 LLM 长篇大论刷屏。
MAX_SUMMARY_CHARS = 1000

# GitHub event types
EVENT_TYPE_PUSH = "push"
EVENT_TYPE_ISSUES = "issues"
EVENT_TYPE_PULL_REQUEST = "pull_request"
EVENT_TYPE_RELEASE = "release"
EVENT_TYPE_WORKFLOW_RUN = "workflow_run"
EVENT_TYPE_STAR = "star"
EVENT_TYPE_WATCH = "watch"
EVENT_TYPE_ISSUE_COMMENT = "issue_comment"
EVENT_TYPE_PING = "ping"

# 支持消息转发的事件类型（ping 仅做健康检查，不参与订阅白名单）。
SUPPORTED_EVENT_TYPES = frozenset(
    {
        EVENT_TYPE_PUSH,
        EVENT_TYPE_ISSUES,
        EVENT_TYPE_PULL_REQUEST,
        EVENT_TYPE_RELEASE,
        EVENT_TYPE_WORKFLOW_RUN,
        EVENT_TYPE_STAR,
        EVENT_TYPE_WATCH,
        EVENT_TYPE_ISSUE_COMMENT,
    }
)

# Issue actions
ACTION_OPENED = "opened"
ACTION_CLOSED = "closed"
ACTION_REOPENED = "reopened"

# PR actions
ACTION_PR_OPENED = "opened"
ACTION_PR_CLOSED = "closed"
ACTION_PR_REOPENED = "reopened"
# GitHub 的 PR 同步动作枚举值是美式拼写 synchronize（**无尾字母 d**），
# 见 GitHub 文档 pull_request 事件的 activity types 与 webhook payload schema。
# 历史问题（issue #4）：本常量曾误写为 synchronized，与 formatter 里的
# synchronize 键拼写漂移，导致 🔀 分支永不命中；切勿再改回 synchronized。
ACTION_PR_SYNCHRONIZE = "synchronize"

# 允许触发 AI 代码摘要的 PR 动作：新建、重新打开与新增提交。
# closed/labeled 等动作不产生新的可评审代码，调用 LLM 只会浪费额度。
CODE_REVIEW_PR_ACTIONS = frozenset(
    {ACTION_PR_OPENED, ACTION_PR_REOPENED, ACTION_PR_SYNCHRONIZE}
)

# Release actions（GitHub release 事件的 activity types）
ACTION_RELEASE_PUBLISHED = "published"
ACTION_RELEASE_UNPUBLISHED = "unpublished"
ACTION_RELEASE_CREATED = "created"
ACTION_RELEASE_EDITED = "edited"
ACTION_RELEASE_DELETED = "deleted"
ACTION_RELEASE_PRERELEASED = "prereleased"
ACTION_RELEASE_RELEASED = "released"

# Workflow run 事件仅转发完成状态，避免 in_progress/requested 造成刷屏
ACTION_WORKFLOW_COMPLETED = "completed"

# workflow_run.conclusion 枚举
WORKFLOW_CONCLUSION_SUCCESS = "success"
WORKFLOW_CONCLUSION_FAILURE = "failure"
WORKFLOW_CONCLUSION_CANCELLED = "cancelled"
WORKFLOW_CONCLUSION_SKIPPED = "skipped"
WORKFLOW_CONCLUSION_TIMED_OUT = "timed_out"
WORKFLOW_CONCLUSION_ACTION_REQUIRED = "action_required"
WORKFLOW_CONCLUSION_NEUTRAL = "neutral"
WORKFLOW_CONCLUSION_STALE = "stale"
WORKFLOW_CONCLUSION_STARTUP_FAILURE = "startup_failure"

# Star 事件：新版 star 事件用 created/deleted，旧版 watch 事件用 started
ACTION_STAR_CREATED = "created"
ACTION_STAR_DELETED = "deleted"
ACTION_WATCH_STARTED = "started"

# Issue comment 事件的 activity types
ACTION_COMMENT_CREATED = "created"
ACTION_COMMENT_EDITED = "edited"
ACTION_COMMENT_DELETED = "deleted"
