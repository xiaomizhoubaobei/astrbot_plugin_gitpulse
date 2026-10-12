# 提交签名与防伪验证指南 (Commit Signature Verification)

本项目所有官方代码提交、标签发布及自动化构建产物均由维护者使用专属 GPG 密钥签署，以确保代码完整性，防止供应链投毒、代码篡改与身份仿冒。

无论你是开发者、安全审计人员还是刚接触 Git 的新手，都可以通过本文档全面了解与实操提交验证。

---

## 🎯 目录
- [什么是 GPG 提交签名？](#-什么是-gpg-提交签名)
- [方式一：Web 界面直观核验（零门槛）](#-方式一web-界面直观核验零门槛)
- [方式二：本地 Git 命令行全量核验（推荐）](#-方式二本地-git-命令行全量核验推荐)
- [维护者官方公钥清单与指纹表](#-维护者官方公钥清单与指纹表)
- [贡献者指南：如何开启你自己的 GPG 签名](#-贡献者指南如何开启你自己的-gpg-签名)
- [常见验证提示与排错 FAQ](#-常见验证提示与排错-faq)

---

## 📌 什么是 GPG 提交签名？

在 Git 体系中，提交者名字与邮箱（Author / Committer）属于纯文本元数据，任何人都可以通过 `git config` 伪装成项目维护者。

**GPG 签名**（GNU Privacy Guard）基于非对称加密算法，相当于维护者为每次代码变更盖上的**密码学防伪印章**：

1. **不可伪造**：只有持有受保护私钥的维护者本人才能完成签署。
2. **防篡改与完整性**：一旦代码内容、提交时间、父提交等任何元数据被改动一个字节，签名验证即刻失败。
3. **责任可追溯**：每个提交均可明确回溯至具体的密钥指纹与责任人。

---

## 🖥️ 方式一：Web 界面直观核验（零门槛）

最方便、无需任何本地环境的核验方式：

1. 打开本仓库页面（GitHub 或 CNB）。
2. 进入 **Commits（提交历史）** 列表。
3. 查看每条提交记录状态：
   - 官方提交右侧会显示绿色 **`Verified`**（已验证）徽标。
   - 点击徽标可展开弹窗，核验签名公钥的 **Key ID** 与维护者信息。
   - 若出现黄色或未验证提示，说明未签署或密钥未在平台登记。

---

## 💻 方式二：本地 Git 命令行全量核验（推荐）

克隆代码到本地后，可通过本地密码学工具链进行端到端独立核验。

### 1. 准备环境与导入公钥

首先确保系统安装了 `gpg` 与 `curl`：
- **Ubuntu / Debian**: `sudo apt-get install -y gnupg curl`
- **macOS**: `brew install gnupg curl`
- **Windows**: 安装 Git for Windows 时默认已包含 `gpg`。

维护者公钥已托管至 GitHub 官方通道。执行以下命令拉取并导入本地钥匙环：

```bash
curl -s https://github.com/xiaomizhoubaobei.gpg | gpg --import
```

> 仓库内亦提供了自动化脚本 `bash install_gpg_keys.sh`，可一键完成密钥拉取与环境预检。

---

### 2. 执行签名验证

进入仓库根目录，按需选择以下命令：

- **验证当前最新提交 (HEAD)：**
  ```bash
  git verify-commit HEAD
  ```

- **验证指定历史提交：**
  ```bash
  git verify-commit <commit-hash>
  ```

- **查看最近 5 次提交的签名详情及证书信息：**
  ```bash
  git log --show-signature -n 5
  ```

- **验证 Git 标签（Tag）：**
  ```bash
  git verify-tag <tag-name>
  ```

---

### 3. 结果解读

#### ✅ 验证成功（正品提交）
输出中包含明确的 `Good signature` 字样：
```text
gpg: Signature made Mon Oct 12 23:25:00 2026 CST
gpg:                using RSA key 55735DDE36C519B3
gpg: Good signature from "祁筱欣 (手机推送专用GPG密钥) <qixiaoxin@stu.sqxy.edu.cn>"
```
说明该提交确实来自维护者本人且数据完整无篡改。

#### ❌ 验证失败（存在篡改或伪造风险）
若输出显示 `BAD signature`：
```text
gpg: BAD signature from "..." [GNUPG:] BADSIG ...
```
说明提交已被篡改，请勿直接采信或合并。

---

## 🔑 维护者官方公钥清单与指纹表

为防止中间人攻击（MITM），可核对本地导入密钥的完整指纹（Fingerprint）：

```bash
gpg --list-keys --keyid-format LONG
```

维护者公钥指纹速查表：

| 平台 / 用途 | 算法 | Key ID | 完整指纹 (Fingerprint) | 关联邮箱 |
| :--- | :--- | :--- | :--- | :--- |
| **手机推送专用** | RSA 4096 | `A342B6848AAE8FAD` | `30F9 1E90 F391 9CD6 A2F0 7EF3 A342 B684 8AAE 8FAD` | `qixiaoxin@stu.sqxy.edu.cn` |
| **CNB 平台专用** | RSA 4096 | `4D0B2BEFBDFA8AB4` | `01D0 F939 D269 4CCF 5C18 6355 4D0B 2BEF BDFA 8AB4` | `qixiaoxin@stu.sqxy.edu.cn` |
| **CNB CI/CD 环境** | RSA 4096 | `1505904637A96352` | `6144 ACE6 3496 866B 3DC2 29D9 1505 9046 37A9 6352` | `qixiaoxin@stu.sqxy.edu.cn` |
| **GitHub 签名密钥** | Ed25519 | `97CD918AF3513964` | `443A D74C 0152 1B4A 9811 6CE8 97CD 918A F351 3964` | `qixiaoxin@stu.sqxy.edu.cn` |
| **GitLab 专用** | RSA 4096 | `EC4A77EB11773C0F` | `926F E431 C6CC 78DC 4AE0 F467 EC4A 77EB 1177 3C0F` | `qixiaoxin@stu.sqxy.edu.cn` |
| **Gitee 专用** | RSA 4096 | `39D86F84805EE993` | `3467 3B0F 2159 FB52 268C 285B 39D8 6F84 805E E993` | `qixiaoxin@stu.sqxy.edu.cn` |
| **Codeup 专用** | RSA 4096 | `F763BAD00A549021` | `AA10 CADB 81A5 BAFF 1061 FB43 F763 BAD0 0A54 9021` | `qixiaoxin@stu.sqxy.edu.cn` |
| **CODING 专用** | RSA 4096 | `AEF8CB637B7F82A3` | `2CB6 0034 B33D E3BF FC88 9E4F AEF8 CB63 7B7F 82A3` | `qixiaoxin@stu.sqxy.edu.cn` |

---

## 🛠️ 贡献者指南：如何开启你自己的 GPG 签名

本项目欢迎社区贡献。若你也希望对提交进行签名保护：

1. **生成个人 GPG 密钥**：
   ```bash
   gpg --full-generate-key
   ```
   （建议选择 `RSA (sign only)` 或 `Ed25519`，密钥长度推荐 4096 位）。

2. **获取你的 Key ID**：
   ```bash
   gpg --list-secret-keys --keyid-format LONG
   ```

3. **配置本地 Git 默认自动签名**：
   ```bash
   git config --global user.signingkey <你的KEY_ID>
   git config --global commit.gpgsign true
   git config --global tag.gpgsign true
   ```

4. **导出公钥并上传到平台**：
   ```bash
   gpg --armor --export <你的KEY_ID>
   ```
   复制输出的文本，粘贴到 GitHub/CNB 的 `Settings -> SSH and GPG keys` 中即可。

---

## ❓ 常见验证提示与排错 FAQ

### Q1: 提示 `WARNING: This key is not certified with a trusted signature!` 是什么意思？
**A:** 这不是报错，密码学签名已验证成功。
该提示仅代表该密钥尚未被你本地信任数据库显式标为绝对信任。若需消除该警告：
```bash
gpg --edit-key 30F91E90F3919CD6A2F07EF3A342B6848AAE8FAD
# 交互中依次输入：trust -> 5 -> y -> quit
```

### Q2: 如何批量查看历史提交的签名状态？
**A:** 运行以下命令可以直观检查提交的签名状态标识：
```bash
git log --pretty="format:%h %G? %an: %s" -n 20
```
- `G` (Good)：有效签名
- `B` (Bad)：无效或损坏签名
- `U` (Untrusted)：签名有效但公钥本地未标信任
- `N` (None)：未签名普通提交

### Q3: 报错 `gpg: Can't check signature: No public key`？
**A:** 说明本地尚未导入签名者对应的公钥，请先参考[方式二](#方式二本地-git-命令行全量核验推荐)导入公钥后再试。
