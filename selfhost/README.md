# PR Lens 自托管服务

把 PR Lens 的图片托管从「仓库孤儿分支」换成「阿里云函数计算 + 腾讯云 COS +
EdgeOne 自定义域名」，在不向 PR 流水线注入长期密钥的前提下，实现类似 GitHub App
的「PR 评论里直接看图」体验。

## 架构

```
CNB pull_request job
  └─ pr-lens CLI（分析 diff、渲染 SVG）
        │  只带流水线内置 CNB_TOKEN
        ▼
阿里云 FC 函数 pr-lens（selfhost/fc）
        │  校验仓库/文件名/限流，签发 15 分钟有效的 COS PUT URL
        ▼
腾讯云 COS（ap-shanghai，公开读）
        ▲
        │ EdgeOne 加速域名 pr-lens.mizhoubaobei.top
CNB PR 评论引用 <img src="https://pr-lens.mizhoubaobei.top/...">
```

- 流水线不接触腾讯云 SecretKey，也不需要仓库 `repo-code:rw`；
- 图片由 COS 直接对外提供，评论只引用公开 URL；
- mint 服务只签「本次运行专属前缀」的短期 URL，过期即失效。

## 已部署资源

| 资源 | 值 |
| --- | --- |
| FC 函数 | `pr-lens`（cn-shanghai，nodejs20，HTTP 触发器） |
| FC 入口 | `https://pr-lens-eqkeyznvxz.cn-shanghai.fcapp.run` |
| COS 桶 | `pr-lens-1329111128`（ap-shanghai，public-read，前缀 `pr-lens/`） |
| EdgeOne 站点 | `zone-3dgurhfnt55e`（mizhoubaobei.top） |
| EdgeOne 域名 | `pr-lens.mizhoubaobei.top`，CNAME `pr-lens.mizhoubaobei.top.eo.dnse2.com` |
| 证书 | 已有通配证书 `*.mizhoubaobei.top`（SSL CertId `VmRCSiyU`） |
| CNB job 变量 | `.cnb.yml` 中 `PR_LENS_HOST` 指向 FC 入口 |

## 部署 FC 服务

依赖：已配置 `ALIBABA_CLOUD_ACCESS_KEY_ID` / `ALIBABA_CLOUD_ACCESS_KEY_SECRET` 的
`aliyun` CLI，以及 `python3`、`jq`、`node`。

```bash
COS_BUCKET=pr-lens-1329111128 \
COS_REGION=ap-shanghai \
COS_PUBLIC_BASE=https://pr-lens.mizhoubaobei.top \
CNB_API_ENDPOINT=https://api.cnb.cool \
ALLOWED_REPOS=XMZZUZHI/Astrbot/astrbot_plugin_github_webhook \
MINT_PER_HOUR=30 \
TENCENT_SECRET_ID=... TENCENT_SECRET_KEY=... \
bash selfhost/deploy/deploy-fc.sh
```

脚本会打包 `selfhost/fc` 并创建/更新函数与 HTTP 触发器，最后输出 `PR_LENS_HOST`。
把该值填进 `.cnb.yml` 的 `pr-lens` job。

### 环境变量

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY` | 必填 | COS 写入凭据；只存在于 FC 环境变量，不写入仓库 |
| `COS_BUCKET` | `pr-lens-1329111128` | COS 桶名 |
| `COS_REGION` | `ap-shanghai` | COS 地域 |
| `COS_PUBLIC_BASE` | COS 默认域名 | 评论引用的公开地址前缀（这里填 EdgeOne 域名） |
| `CNB_API_ENDPOINT` | `https://api.cnb.cool` | 可选 PR 校验用的 CNB API |
| `ALLOWED_REPOS` | 本仓库 | 允许 mint 的仓库，逗号分隔 |
| `MINT_PER_HOUR` | `30` | 每来源 IP 每小时最多 mint 次数（单实例内存计数） |
| `UPLOAD_EXPIRES_SECONDS` | `900` | 预签名 URL 有效期 |
| `MAX_FILES` | `12` | 单次会话最多图片数 |
| `VERIFY_CNB` | `0` | `1` 时调用 CNB API 校验 PR 头提交；CNB 对公网 API 有网络策略拦截，默认关闭 |

## COS 与 EdgeOne 配置（已执行，供复现）

```bash
# 1. 新建公开读桶
tccli cos create_bucket --bucket pr-lens-1329111128 --region ap-shanghai
tccli cos put_bucket_acl --bucket pr-lens-1329111128 --region ap-shanghai --acl public-read

# 2. EdgeOne 创建加速域名（COS 源站）
tccli teo CreateAccelerationDomain \
  --ZoneId zone-3dgurhfnt55e \
  --DomainName pr-lens.mizhoubaobei.top \
  --OriginInfo '{"OriginType":"COS","Origin":"pr-lens-1329111128.cos.ap-shanghai.myqcloud.com","PrivateAccess":"off"}'

# 3. 部署通配证书
tccli teo ModifyHostsCertificate \
  --ZoneId zone-3dgurhfnt55e \
  --Hosts '["pr-lens.mizhoubaobei.top"]' \
  --Mode sslcert \
  --ServerCertInfo '[{"CertId":"VmRCSiyU"}]'

# 4. DNSPod 添加 CNAME
tccli dnspod CreateRecord \
  --Domain mizhoubaobei.top \
  --SubDomain pr-lens \
  --RecordType CNAME \
  --RecordLine 默认 \
  --Value pr-lens.mizhoubaobei.top.eo.dnse2.com.
```

建议给 `pr-lens/` 前缀配置 COS 生命周期规则（例如 30 天过期），避免历史图无限增长。

## 安全边界

- CNB 流水线只使用内置临时 `CNB_TOKEN`，不引入任何长期密钥；
- mint 返回的 PUT URL 只覆盖本次随机会话前缀，15 分钟后失效；
- 文件名只允许单层 `.svg`，单会话最多 12 个；
- 按来源 IP 限流；
- 由于 CNB 对公网 `api.cnb.cool` / git 端点有网络策略拦截（实测 412/403），
  默认不做 token 校验；如需强校验，请把服务部署到 CNB 放行的网络并设置
  `VERIFY_CNB=1`；
- 桶是公开读的，只应存放可公开的图。

## 本地测试

```bash
cd selfhost/fc
node --test            # 单元测试
PORT=8080 node dev-server.js
# 另开终端：
curl -X POST http://127.0.0.1:8080/api/sessions \
  -H 'Authorization: Bearer test' \
  -H 'Content-Type: application/json' \
  -d '{"repo":"XMZZUZHI/Astrbot/astrbot_plugin_github_webhook","pr":1,"headSha":"'$(printf 'a%.0s' {1..40})'","files":["x.svg"]}'
```

## 运维提示

- 轮换 COS 密钥：更新 FC 环境变量并重新执行 `deploy-fc.sh`；
- 更换图片域名：在 EdgeOne 新建域名/证书，更新 FC 的 `COS_PUBLIC_BASE`；
- 清理历史图片：删除 COS 前缀 `pr-lens/` 下的旧对象，或依赖生命周期规则。
