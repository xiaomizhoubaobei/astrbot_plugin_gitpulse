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

# Default rate limit (requests per minute)
DEFAULT_RATE_LIMIT = 10

# Default LLM timeout (seconds)
DEFAULT_LLM_TIMEOUT = 60

# GitHub event types
EVENT_TYPE_PUSH = "push"
EVENT_TYPE_ISSUES = "issues"
EVENT_TYPE_PULL_REQUEST = "pull_request"
EVENT_TYPE_PING = "ping"

# Issue actions
ACTION_OPENED = "opened"
ACTION_CLOSED = "closed"
ACTION_REOPENED = "reopened"

# PR actions
ACTION_PR_OPENED = "opened"
ACTION_PR_CLOSED = "closed"
ACTION_PR_REOPENED = "reopened"
ACTION_PR_SYNCHRONIZED = "synchronized"
