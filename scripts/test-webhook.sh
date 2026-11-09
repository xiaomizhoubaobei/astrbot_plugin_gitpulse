#!/bin/bash

# GitPulse - 集成测试脚本
# 用法: ./test-webhook.sh [选项] <事件类型>

set -e

# 默认配置
DEFAULT_HOST="localhost"
DEFAULT_PORT="8080"
DEFAULT_PATH="/webhook"
DEFAULT_SECRET=""

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# 显示帮助信息
show_help() {
    cat << EOF
GitHub Webhook 测试脚本

用法:
    $0 [选项] <事件类型>

选项:
    -H, --host HOST       服务器地址 (默认: localhost)
    -p, --port PORT       服务器端口 (默认: 8080)
    -P, --path PATH       Webhook 路径 (默认: /webhook)
    -s, --secret SECRET   Webhook 密钥 (默认: 无)
    -v, --verbose         详细输出
    -h, --help            显示帮助信息

事件类型:
    push              Push 事件
    issues            Issue 事件
    pr                Pull Request 事件
    release           Release 事件
    workflow_run      Workflow Run 事件（别名 workflow）
    star              Star 事件（新增 Star）
    watch             Watch 事件（旧版 starred 事件）
    comment           Issue / PR 评论事件
    ping              Ping 事件 (健康检查)

示例:
    # 发送 push 事件到默认服务器
    $0 push

    # 发送 PR 事件到指定端口
    $0 -p 6100 pr

    # 发送到自定义 webhook 路径
    $0 -P /github/webhook ping

    # 带密钥的测试
    $0 -s "my-secret" issues

    # 详细模式
    $0 -v push

EOF
}

# 解析参数
HOST="$DEFAULT_HOST"
PORT="$DEFAULT_PORT"
WEBHOOK_PATH="$DEFAULT_PATH"
SECRET="$DEFAULT_SECRET"
VERBOSE=false
EVENT_TYPE=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -H|--host)
            HOST="$2"
            shift 2
            ;;
        -p|--port)
            PORT="$2"
            shift 2
            ;;
        -P|--path)
            WEBHOOK_PATH="$2"
            shift 2
            ;;
        -s|--secret)
            SECRET="$2"
            shift 2
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -h|--help)
            show_help
            exit 0
            ;;
        push|issues|pr|release|workflow_run|workflow|star|watch|comment|ping)
            EVENT_TYPE="$1"
            shift
            ;;
        *)
            echo -e "${RED}错误: 未知参数或事件类型 '$1'${NC}"
            show_help
            exit 1
            ;;
    esac
done

# 检查是否指定了事件类型
if [[ -z "$EVENT_TYPE" ]]; then
    echo -e "${RED}错误: 必须指定事件类型${NC}"
    show_help
    exit 1
fi

# 构建 Webhook URL（路径未以 / 开头时自动补上）
if [[ "$WEBHOOK_PATH" != /* ]]; then
    WEBHOOK_PATH="/${WEBHOOK_PATH}"
fi
WEBHOOK_URL="http://${HOST}:${PORT}${WEBHOOK_PATH}"

echo -e "${GREEN}=== GitHub Webhook 测试 ===${NC}"
echo "事件类型: $EVENT_TYPE"
echo "目标地址: $WEBHOOK_URL"
echo ""

# 发送请求的函数
send_request() {
    local event_type="$1"
    local payload="$2"

    # 用数组拼装 curl 参数，全程不经过 eval：
    # 原实现把整条命令拼成字符串再 eval，payload 里一旦出现引号 / 反引号
    # 就会被二次求值（命令注入），且变量未加引号会触发 shellcheck SC2086。
    local -a curl_args=(
        curl -s -X POST "$WEBHOOK_URL"
        -H "Content-Type: application/json"
        -H "X-GitHub-Event: $event_type"
    )

    # 如果有密钥，添加签名头（简化版，实际使用 HMAC）
    if [[ -n "$SECRET" ]]; then
        local signature
        signature="sha256=$(printf '%s' "$payload" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')"
        curl_args+=(-H "X-Hub-Signature-256: $signature")
    fi

    curl_args+=(-d "$payload")

    if [[ "$VERBOSE" == true ]]; then
        echo -e "${YELLOW}发送请求...${NC}"
        echo "事件类型: $event_type"
        if [[ "$VERBOSE" == true ]]; then
            echo "Payload:"
            echo "$payload" | jq '.' 2>/dev/null || echo "$payload"
        fi
        echo ""
    fi

    # 执行请求；用 `|| EXIT_CODE=$?` 兜住失败，避免 set -e 直接中断脚本
    EXIT_CODE=0
    RESPONSE="$("${curl_args[@]}")" || EXIT_CODE=$?

    if [[ $EXIT_CODE -eq 0 ]]; then
        echo -e "${GREEN}✓ 请求发送成功${NC}"
        if [[ "$VERBOSE" == true ]]; then
            echo "响应: $RESPONSE"
        fi
    else
        echo -e "${RED}✗ 请求失败 (退出码: $EXIT_CODE)${NC}"
        if [[ "$VERBOSE" == true ]]; then
            echo "响应: $RESPONSE"
        fi
    fi

    return $EXIT_CODE
}

# 生成测试 payload
case "$EVENT_TYPE" in
    push)
        PAYLOAD='{
  "ref": "refs/heads/main",
  "before": "a1b2c3d4e5f6g7h8i9j0",
  "after": "0j9i8h7g6f5e4d3c2b1a",
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false,
    "owner": {
      "login": "testuser",
      "type": "User"
    }
  },
  "pusher": {
    "name": "Test User",
    "email": "test@example.com"
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  },
  "commits": [
    {
      "id": "1234567890abcdef",
      "message": "Test commit message",
      "timestamp": "2024-01-27T10:00:00Z",
      "author": {
        "name": "Test User",
        "email": "test@example.com"
      }
    }
  ]
}'
        send_request "push" "$PAYLOAD"
        ;;

    issues)
        PAYLOAD='{
  "action": "opened",
  "issue": {
    "id": 12345,
    "number": 1,
    "title": "Test Issue",
    "body": "This is a test issue",
    "state": "open",
    "user": {
      "login": "testuser",
      "type": "User"
    },
    "html_url": "https://github.com/testuser/test-repo/issues/1",
    "labels": []
  },
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        send_request "issues" "$PAYLOAD"
        ;;

    pr)
        PAYLOAD='{
  "action": "opened",
  "number": 10,
  "pull_request": {
    "id": 98765,
    "title": "Test Pull Request",
    "body": "This is a test PR",
    "state": "open",
    "user": {
      "login": "testuser",
      "type": "User"
    },
    "html_url": "https://github.com/testuser/test-repo/pull/10",
    "merged": false,
    "draft": false,
    "head": {
      "ref": "feature-branch",
      "sha": "a1b2c3d4"
    },
    "base": {
      "ref": "main",
      "sha": "e5f6g7h8"
    }
  },
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        send_request "pull_request" "$PAYLOAD"
        ;;

    release)
        PAYLOAD='{
  "action": "published",
  "release": {
    "tag_name": "v1.0.0",
    "name": "First Release",
    "html_url": "https://github.com/testuser/test-repo/releases/tag/v1.0.0",
    "prerelease": false,
    "draft": false
  },
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        send_request "release" "$PAYLOAD"
        ;;

    workflow_run|workflow)
        PAYLOAD='{
  "action": "completed",
  "workflow_run": {
    "id": 123456,
    "name": "CI",
    "event": "push",
    "status": "completed",
    "conclusion": "success",
    "head_branch": "main",
    "html_url": "https://github.com/testuser/test-repo/actions/runs/123456"
  },
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        send_request "workflow_run" "$PAYLOAD"
        ;;

    star|watch)
        PAYLOAD='{
  "action": "started",
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false,
    "stargazers_count": 128,
    "html_url": "https://github.com/testuser/test-repo"
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        # `star` 是新版事件，`watch` 是旧版事件；两者 payload 结构一致
        send_request "$EVENT_TYPE" "$PAYLOAD"
        ;;

    comment)
        PAYLOAD='{
  "action": "created",
  "issue": {
    "number": 42,
    "title": "Test Issue",
    "html_url": "https://github.com/testuser/test-repo/issues/42",
    "pull_request": null
  },
  "comment": {
    "id": 123456,
    "body": "Looks good to me",
    "html_url": "https://github.com/testuser/test-repo/issues/42#issuecomment-123456",
    "user": {
      "login": "testuser",
      "type": "User"
    }
  },
  "repository": {
    "id": 123456789,
    "name": "test-repo",
    "full_name": "testuser/test-repo",
    "private": false
  },
  "sender": {
    "login": "testuser",
    "type": "User"
  }
}'
        send_request "issue_comment" "$PAYLOAD"
        ;;

    ping)
        PAYLOAD='{
  "zen": "Keep it simple.",
  "hook_id": 12345
}'
        send_request "ping" "$PAYLOAD"
        ;;
esac

echo ""
echo -e "${GREEN}=== 测试完成 ===${NC}"
