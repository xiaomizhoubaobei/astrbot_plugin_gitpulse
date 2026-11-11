# PR Lens 自托管发布（CNB 侧）

本目录只保留 CNB PR job 使用的发布脚本 `ci/pr-lens.sh`。自托管的 mint 服务
（TypeScript）已合并到 [XMZZUZHI/Github/pr-lens](https://cnb.cool/XMZZUZHI/Github/pr-lens)
的 `src/selfhost/`，部署说明也在那里。

## 架构

```
CNB pull_request job
  └─ pr-lens CLI（分析 diff、渲染 SVG）
        │ 只带流水线内置 CNB_TOKEN
        ▼
腾讯云 SCF 函数 pr-lens（广州）
        │ 签发 15 分钟有效的 COS PUT URL
        ▼
腾讯云 COS  pr-lens-1329111128（广州，公开读）
        ▲
        │ EdgeOne 加速域名 pr-lens.mizhoubaobei.top
CNB PR 评论引用 <img src="https://pr-lens.mizhoubaobei.top/...">
```

- 流水线不接触腾讯云 SecretKey，也不需要仓库 `repo-code:rw`；
- 图片由 COS 直接对外提供，评论只引用公开 URL；
- mint 服务只签「本次运行专属前缀」的短期 URL，过期即失效。

## 当前部署

| 资源 | 值 |
| --- | --- |
| SCF 函数 | `pr-lens`，ap-guangzhou，Nodejs18.15 |
| SCF 入口 | `https://1329111128-lix1804oa9.ap-guangzhou.tencentscf.com` |
| COS 桶 | `pr-lens-1329111128`（广州，public-read，`pr-lens/` 前缀 30 天生命周期） |
| EdgeOne 域名 | `pr-lens.mizhoubaobei.top`，源站为上述广州 COS 桶 |
| CNB job | `.cnb.yml` 的 `pr-lens`，`PR_LENS_HOST` 指向 SCF 入口 |

## 安全边界

- CNB 流水线只使用内置临时 `CNB_TOKEN`，不引入长期密钥；
- mint 返回的 PUT URL 只覆盖本次随机会话前缀，15 分钟后失效；
- 文件名只允许单层 `.svg`，单会话最多 12 个，并按来源 IP 限流；
- CNB 对公网 `api.cnb.cool` / git 端点有网络策略拦截；若 SCF 出口也被拦截，
  保持 `VERIFY_CNB=0`，否则在服务侧开启 PR 头提交校验；
- COS 桶是公开读的，只应存放可公开的图。
