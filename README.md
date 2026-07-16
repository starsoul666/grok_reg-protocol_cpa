# grok_reg-protocol_cpa

基于 **Chromium + DrissionPage + turnstilePatch** 的免费 Grok 账号注册机。

## 核心特性

1. **多邮箱支持**  
   支持 Hotmail/Outlook（四段凭证+XOAUTH2 IMAP）、CloudMail、Cloudflare、DuckMail、YYDS 等主流临时邮箱服务。

2. **协议优先的 CPA 导出**  
   注册拿到 SSO 后，优先用 **纯 HTTP PKCE authorization-code flow**（`curl_cffi` + `sso` cookie）铸造 CPA 用的 `xai-*.json`；协议失败默认不再回退旧 Device Flow，避免产出 `/models` 可用但 chat 403 的坏 token。

## 产物说明

一条成功链路会产出两类凭证：

| 产物 | 用途 | 路径 |
|------|------|------|
| **SSO** | grok.com / grok2api Web 池 | 账本第三段 + 可选推远端池 |
| **OIDC（CPA xAI）** | 免费 **Grok 4.5**（Grok Build / cli-chat-proxy） | `cpa_auths/xai-<email>.json` |

> **硬约束：SSO ≠ OIDC。**  
> 免费 Grok 4.5 **不能**用账本里的 sso JWT 直接打 API；必须再走  
> `accounts.x.ai` OAuth 铸 OIDC，写成 CPA 的 `type=xai` 认证文件。
> 本仓库的协议路径正是用 **SSO cookie 自动完成** 这一步（无需再弹浏览器时优先走协议）。

## 核心模块

本仓库**自包含** OIDC/CPA 铸造代码（`cpa_xai/`）：

| 路径 | 说明 |
|------|------|
| 文件 | 说明 |
|------|------|
| `cpa_xai/pkce_mint.py` | SSO → 纯 HTTP PKCE authorization-code（**推荐 CPA 路径**，数秒完成） |
| `cpa_xai/protocol_mint.py` | 旧 SSO → OAuth Device Flow（兼容路径，默认不回退） |
| `cpa_xai/mint.py` | PKCE 优先的导出编排（协议 → 浏览器回退） |
| `cpa_xai/browser_confirm.py` | 原逻辑：有头 Chromium 完成 consent |
| `cpa_export.py` | 注册成功 hook（调用 mint 编排） |
| `cpa_xai/probe.py` | CPA 写入后探测 `/v1/models` 与 `/v1/responses` 可用性 |
| `scripts/backfill_cpa_xai_from_accounts.py` | 存量账号批量补 CPA |
| `scripts/export_cpa_xai_from_grok_auth.py` | 从 `~/.grok/auth.json` 导出 CPA 文件 |

---

## 本版主要改动

### 支持的邮箱服务商

本项目支持多种邮箱服务商，可在 `config.json` 中通过 `email_provider` 配置：

| 邮箱类型 | 配置值 | 凭证格式 | 说明 |
|----------|--------|----------|------|
| Hotmail/Outlook | `hotmail` / `outlookmail` | `邮箱----密码----ClientID----Token` | 四段凭证格式 |
| Cloudflare | `cloudflare` | API 配置 | Cloudflare Email Routing API |
| CloudMail | `cloudmail` | URL + 账号密码 | 自托管 CloudMail |
| DuckMail | `duckmail` | API Key | 临时邮箱服务 |
| YYDS | `yyds` | API Key | 临时邮箱服务 |
| MailNest | `mailnest` | API Key + 项目代码 | Outlook 临时邮箱 |

**Cloudflare 配置示例：**

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://your-api-domain.com",
  "cloudflare_admin_password": "your-admin-password",
  "cloudflare_auth_mode": "none",
  "cloudflare_path_domains": "/api/domains",
  "cloudflare_path_accounts": "/admin/new_address",
  "cloudflare_path_token": "/api/token",
  "cloudflare_path_messages": "/api/parsed_mails",
  "defaultDomains": "your-domain.com"
}
```

| 配置项 | 说明 |
|--------|------|
| `cloudflare_api_base` | API 基础地址 |
| `cloudflare_api_key` | 通用 API Key；配合 `cloudflare_auth_mode` 发送 Bearer、`X-API-Key` 或 query 参数 |
| `cloudflare_auth_mode` | 通用 API Key 认证模式：`none` / `bearer` / `x-api-key` / `query-key`；不控制 `x-admin-auth` |
| `cloudflare_admin_password` | 管理员密码；配置后创建地址和拉域名会发送 `x-admin-auth`，CLI / GUI 都生效 |
| `cloudflare_path_domains` | 拉取域名的端点 |
| `cloudflare_path_accounts` | 创建邮箱的端点 |
| `cloudflare_path_token` | 账号密码模式获取 token 的端点 |
| `cloudflare_path_messages` | 拉取邮件列表的端点；推荐 `/api/parsed_mails`，旧部署会回退 `/api/mails` |
| `defaultDomains` | 邮箱域名；多个域名可用逗号、中文逗号或空格分隔 |

Cloudflare 临时邮箱流程中，创建地址走 `/admin/new_address` 并携带 `x-admin-auth`；读取邮件只使用创建地址返回的 JWT 作为 `Authorization: Bearer <jwt>`。

**MailNest-迈巢 配置示例：**

```json
{
  "email_provider": "mailnest",
  "mailnest_api_key": "",
  "mailnest_project_code": "x-ai001"
}
```

- API Key 获取页：https://mailnest.top/account
- 项目代码获取页：https://mailnest.top/buy-email（默认 `x-ai001`）

### 1. Hotmail / Outlook：`邮箱----密码----ClientID----Token`

设置：

```json
{
  "email_provider": "hotmail",
  "hotmail_accounts_file": "mail_credentials.txt",
  "hotmail_max_aliases_per_account": 5
}
```

凭证文件（可从模板复制）：

```bash
cp mail_credentials.example.txt mail_credentials.txt
```

**每行格式（四段，`----` 分隔）：**

```text
邮箱----密码----ClientID----Token
```

| 段 | 含义 |
|----|------|
| 邮箱 | Hotmail / Outlook 主邮箱 |
| 密码 | 邮箱登录密码（注册机侧保留；IMAP 走 OAuth） |
| ClientID | 微软应用（Azure AD 应用）Client ID |
| Token | Microsoft OAuth2 **refresh_token**（XOAUTH2 IMAP 用） |

示例：

```text
your@hotmail.com----mailPassword----xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx----0.AXcA...refresh_token...
```

运行时行为摘要：

- 默认先用原邮箱，后续用随机 plus alias（如 `name+k8s2p9qa@domain`）
- 经 `outlook.office365.com`（可回退 `imap-mail.outlook.com`）XOAUTH2 IMAP 拉验证码
- refresh_token 若轮换会**自动回写** `mail_credentials.txt`
- 成功 / 失败 / 占用中的 alias 会参与去重与 `hotmail_max_aliases_per_account` 计数

相关配置见 `config.example.json` 中 `hotmail_*` 注释键。

### 2. 协议 OIDC → CPA（PKCE 优先）

```
注册成功拿到 sso cookie
        ↓
【优先】pkce_mint：curl_cffi + sso
   authorize → cookie-setter → consent → authorization_code → token
        ↓ 成功
  cpa_auths/xai-<email>.json   mint_method=pkce
        ↓ 失败
  默认失败并记录；如显式开启 cpa_allow_device_flow_fallback，才回退旧 Device Flow / 浏览器路径
```

实测协议路径约数秒级即可完成（含 probe）；浏览器路径约 40–60s/号。

关键配置：

| 字段 | 默认 | 含义 |
|------|------|------|
| `cpa_prefer_protocol` | `true` | 有 SSO 时先走纯 HTTP 协议 mint |
| `cpa_protocol_flow` | `pkce` | 协议 mint 流程：`pkce`=推荐默认；`device`=旧 Device Flow |
| `cpa_protocol_only` | `false` | `true`=协议失败也不回退浏览器（调试用） |
| `cpa_allow_device_flow_fallback` | `false` | PKCE 失败后是否允许旧 Device Flow fallback；可能产生 chat 403 token |
| `cpa_protocol_poll_timeout_sec` | `90` | 协议路径 token 轮询超时 |
| `cpa_export_enabled` | `true` | 注册成功后是否 mint OIDC |
| `cpa_auth_dir` | `./cpa_auths` | 主导出目录 |
| `cpa_base_url` | `https://cli-chat-proxy.grok.com/v1` | 免费 Build **必须**此上游 |
| `cpa_headless` | `false` | 回退浏览器时建议有头 |
| `cpa_force_standalone` | `true` | 回退时独立 Chromium，不复用注册 tab |
| `cpa_mint_cookie_inject` | `true` | 回退时注入注册 cookie，尽量跳过二次登录 |

日志里可看到：

```text
[cpa] mint try protocol (SSO HTTP PKCE authorization-code flow)
[cpa] pkce authorization code ok ...
[cpa] mint protocol PKCE SUCCESS
[cpa] mint_method=pkce
```

协议失败时类似：

```text
[cpa] mint protocol failed: ...
[cpa] mint fallback → browser
[cpa] mint_method=browser
```

---

## 架构流程

```
[邮箱 Hotmail/Outlook 或 CloudMail 等]
       ↓  注册 accounts.x.ai
 accounts_*.txt / accounts_cli.txt    email----password----sso
       ↓
 grok2api 池 (可选)                   SSO → Web 非 4.5 模型
       ↓
 OIDC mint（协议优先 → 浏览器回退）
       ↓
 cpa_auths/xai-email.json             【注册机主导出】
       ↓ (cpa_copy_to_hotload=true 时移动，原文件移走)
 CPA auth-dir 热加载                  【可选】
       ↓
 CLIProxyAPI :8317                    model=grok-4.5
```

### CPA Mint 流程详解

```
注册成功拿到 sso cookie
        ↓
【优先】pkce_mint：curl_cffi + sso cookie
   authorize → cookie-setter → consent → authorization_code → token
        ↓ 成功 (~数秒)
  cpa_auths/xai-<email>.json   mint_method=pkce
        ↓ 失败
  默认失败并记录；如显式开启 cpa_allow_device_flow_fallback，才回退旧 Device Flow / 浏览器路径
```

**性能对比**：
- 协议路径（PKCE）：约 **数秒** 完成（含 probe）
- 浏览器路径：约 **40–60s/号**

---

## 环境准备

| 依赖 | 说明 |
|------|------|
| macOS / Linux + 桌面 | 协议 mint **不需要**浏览器；回退浏览器时需要 `DISPLAY` / 本机 GUI |
| `uv` + Python 3.13 | 本目录 `pyproject.toml` / `uv.lock`；可选 `mise` |
| `chromium` | 仅注册 + 协议失败回退时需要 |
| 代理 | xAI / accounts.x.ai 通常需要，如 `http://127.0.0.1:7890` |
| 可选 | 本机 grok2api `:8000`、CLIProxyAPI(CPA) `:8317` |

### 依赖安装

```bash
cd /path/to/grok_reg-protocol_cpa
uv sync
# 或使用 mise
mise install
mise run deps
```

### 验证环境

```bash
uv run python -c "from DrissionPage import Chromium; from curl_cffi import requests; print('OK')"
```

---

## 配置

1. 复制模板并编辑（模板内 `"//…"` 键是注释，加载时忽略）：

```bash
cp config.example.json config.json
# 编辑：email_provider、proxy、hotmail_*、cpa_*
```

2. **字段详解见 `config.example.json` 内注释键**。

### 代理优先级

| 字段 | 作用 |
|------|------|
| `proxy` | **注册** Chromium + 邮箱等 HTTP |
| `register_headless` | 注册 Chromium 是否无头；默认 `false`，Turnstile 不稳定时保持有头 |
| `cpa_proxy` | **OIDC mint**（协议 HTTP + 回退浏览器 + probe） |

```
cpa_proxy  >  proxy  >  环境变量 https_proxy/http_proxy
```

配置优先于 shell 里的 `https_proxy`，避免「config 写了 7890 却被环境变量盖掉」。

### 与 CPA 相关的关键项（摘要）

| 字段 | 含义 | 建议 |
|------|------|------|
| `cpa_export_enabled` | 注册成功后是否 mint OIDC | `true` |
| `cpa_prefer_protocol` | SSO 协议优先 | `true` |
| `cpa_protocol_only` | 仅协议、不回退浏览器 | 调试时 `true`，日常 `false` |
| `cpa_auth_dir` | **主导出目录** | `./cpa_auths` |
| `cpa_copy_to_hotload` | 是否移动到 CPA 热加载目录（移动后原 `cpa_auths` 副本不保留） | 可选，默认 `false` |
| `cpa_hotload_dir` | CPA `auth-dir` | 仅 move 时需要 |
| `cpa_base_url` | 上游 API 根 | **必须** `https://cli-chat-proxy.grok.com/v1` |
| `cpa_headless` | 回退浏览器是否无头 | **`false`** |
| `cpa_force_standalone` | 回退时独立浏览器 | **`true`** |
| `cpa_proxy` | mint 专用代理 | 如 `http://127.0.0.1:7890` |
| `cpa_mint_required` | mint 失败是否整号失败 | 通常 `false` |

CLI 与 GUI 都会在注册成功后读这些配置。GUI 下 CPA 导出会串行，避免多窗口抢焦点。

### 落盘约定

| 路径 | 是否必须 | 说明 |
|------|----------|------|
| `mail_credentials.txt` | hotmail 模式必须 | `邮箱----密码----ClientID----Token` |
| `accounts_cli.txt` / `accounts_*.txt` | 是 | 主账本 `email----password----sso` |
| `cpa_auths/xai-*.json` | 是（开 export 时） | CPA 格式 OIDC 归档 |
| CPA `…/auths/xai-*.json` | 可选 | 热加载；由 `cpa_copy_to_hotload` 控制 |

---

## 命令：批量注册 + 认证

前置：

```bash
cd /path/to/grok_reg-protocol_cpa
# 代理建议写在 config.json 的 proxy / cpa_proxy
# 回退浏览器时需要桌面会话
export DISPLAY=${DISPLAY:-:0}
```

### A. 新注册 N 个号（含 SSO + OIDC 导出）

```bash
# 再注册 1 个（推荐）
uv run python -u register_cli.py --extra 1 --threads 1

# 再注册 5 个
uv run python -u register_cli.py --extra 5 --threads 2

# 无头注册 1 个账号（默认仍是有头；Turnstile 失败时改回有头）
uv run python -u register_cli.py --extra 1 --threads 1 --headless-register

# 强制有头覆盖 config.register_headless
uv run python -u register_cli.py --extra 1 --threads 1 --headed-register

# GUI
uv run python grok_register_ttk.py
# 或 mise run gui / mise run register
```

成功时：

1. 追加账本 `email----password----sso`
2. 可选：推 grok2api
3. 若 `cpa_export_enabled`：协议 mint（失败则浏览器）→ `cpa_auths/xai-<email>.json`
4. 若 `cpa_copy_to_hotload`：移动到 `cpa_hotload_dir`（不再保留 `cpa_auths` 中的副本）

### B. 存量号补 CPA（只 mint，不重新注册）

账本需含 SSO（第三段）。协议优先，有 SSO 时通常**无需**弹浏览器：

```bash
uv run python -u scripts/backfill_cpa_xai_from_accounts.py \
  --accounts accounts_cli.txt \
  --limit 1 --probe --timeout 300

# PKCE set-cookie 被 403 时，直接走有头浏览器 consent
uv run python -u scripts/backfill_cpa_xai_from_accounts.py \
  --accounts accounts_cli.txt \
  --email "user@example.com" \
  --limit 1 --no-skip-existing --browser-only --probe --probe-chat --timeout 300

# 全量缺失号
uv run python -u scripts/backfill_cpa_xai_from_accounts.py \
  --limit 0 --probe --timeout 300 --sleep 3
```

| 参数 | 含义 |
|------|------|
| `--limit N` | 本次最多 N 个缺失号；`0`=全部 |
| `--email x@y` | 只处理指定邮箱 |
| `--out-dir` | 主导出目录 |
| `--cpa-dir` | 成功后复制到 CPA 热加载目录 |
| `--probe` | 检查是否列出 `grok-4.5` |
| `--probe-chat` | 再打一发最小 chat 探测，避免 `/models` 误判 |
| `--browser-only` | 跳过 PKCE，直接走有头浏览器 consent；适合 `set-cookie HTTP 403` |
| `--headless` | 回退浏览器时无头（不推荐） |

### C. 从 `~/.grok/auth.json` 导出 CPA 文件

```bash
uv run python scripts/export_cpa_xai_from_grok_auth.py --out-dir ./cpa_auths
```

### D. 手动导入 CPA 热加载

```bash
cp -a ./cpa_auths/xai-USER@domain.json "$CPA_AUTH_DIR"/
chmod 600 "$CPA_AUTH_DIR"/xai-USER@domain.json
```

### E. 调用验证（免费 Grok 4.5）

```bash
KEY="<你的 CPA API KEY>"

curl -sS http://127.0.0.1:8317/v1/models -H "Authorization: Bearer $KEY" | head

curl -sS http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "grok-4.5",
    "messages": [{"role":"user","content":"Reply with exactly OK"}],
    "stream": false
  }'
```

---

## CLI 参数速查（`register_cli.py`）

### 注册参数

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--extra N` | **再新注册 N 个**（推荐） | 0 |
| `--count N` | 账号**总数目标**（含已有）；已达标则退出 | 1 |
| `--threads N` | 注册并发线程数（1-10） | 1 |
| `--accounts-file` | 账本路径 | `./accounts_cli.txt` |
| `--headless-register` | 注册浏览器使用无头模式 | 有头 |
| `--headed-register` | 注册浏览器强制有头模式 | - |

### CPA Mint 参数

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--mint-workers N` | CPA mint 并发数：-1=用 config/auto；0=内联；1-10=固定 | -1（auto） |
| `--mint-queue-max N` | mint 队列背压上限：-1=用 config/auto(2×workers)；0=不限制 | -1 |
| `--inline-mint` | 强制注册线程内联 mint（调试用） | 异步队列 |

### 性能参数

| 参数 | 含义 | 默认值 |
|------|------|--------|
| `--fast` | 快速模式：压缩 sleep、关截图 | 开 |
| `--no-fast` | 关闭快速模式 | - |
| `--no-browser-reuse` | 每号强制 quit 浏览器 | 复用 |
| `--browser-recycle-every N` | 复用 N 次后完整回收 | 25 |
| `--cookie-snapshot` | 注册成功写 cookie 快照 | 关（fast 时） |

### 使用示例

```bash
# 注册 1 个账号（推荐）
uv run python -u register_cli.py --extra 1 --threads 1

# 注册 5 个账号，并发 2
uv run python -u register_cli.py --extra 5 --threads 2

# 无头注册（默认仍建议有头，Turnstile 失败时去掉该参数）
uv run python -u register_cli.py --extra 1 --threads 1 --headless-register

# GUI 模式
uv run python grok_register_ttk.py
# 或 mise run gui / mise run register
```

---

## 故障排查

| 现象 | 原因 / 处理 |
|------|-------------|
| 协议 `sso invalid` | SSO 过期或无效；检查账本第三段 |
| PKCE cookie-setter / consent 失败 | 会话态变化 / 风控；默认不写 CPA，检查日志后重试 |
| Device Flow token chat 403 | 旧 Device Flow 常见现象：`/models` 可列出但 chat endpoint permission-denied；保持 `cpa_allow_device_flow_fallback=false` |
| Cloudflare / Turnstile | 回退浏览器时关 headless、开 turnstilePatch、检查代理 |
| Hotmail 收不到码 | 检查四段凭证、ClientID/Token、IMAP 主机与 alias 计数 |
| 有 token 但 chat 403 | 不要只看 `/v1/models`；保持 `cpa_probe_chat=true` 并使用 PKCE mint |
| 注册成功但无 `cpa_auths` | `cpa_export_enabled`？看 `cpa_auth_failed.txt` |

调试原则：以 **最小 `/v1/responses` chat 探测成功** 为准；`/v1/models` 只能证明模型列表可见，不能证明 chat 权限可用。

---

## 目录结构

```
grok_reg-protocol_cpa/
  register_cli.py              # CLI 批量注册
  grok_register_ttk.py         # 浏览器注册核心 + Hotmail 等
  cpa_export.py                # 成功 hook
  cpa_xai/
    pkce_mint.py               # SSO 纯 HTTP PKCE authorization-code（协议优先）
    protocol_mint.py           # 旧 Device Flow 兼容路径（默认不回退）
    mint.py                    # 协议 → 浏览器回退编排
    browser_confirm.py         # 原浏览器 consent
    oauth_device.py / schema.py / writer.py / probe.py ...
  scripts/
    backfill_cpa_xai_from_accounts.py
    export_cpa_xai_from_grok_auth.py
  config.example.json
  config.json                  # 本地实配（勿外泄）
  mail_credentials.example.txt # 邮箱----密码----ClientID----Token 模板
  mail_credentials.txt         # 本地邮箱池（勿提交）
  accounts_cli.txt             # 主账本
  cpa_auths/                   # xai-<email>.json（分享包勿带）
  turnstilePatch/
  pyproject.toml / uv.lock / mise.toml
```

---


```bash
cd grok_reg-protocol_cpa
uv sync
cp config.example.json config.json
cp mail_credentials.example.txt mail_credentials.txt
# 填 hotmail 四段凭证 + proxy，再运行
uv run python -u register_cli.py --extra 1
```
---

## 安全

- `config.json`、`mail_credentials.txt`、账本、`cpa_auths/*.json` 含密码与 refresh_token，**权限 600 / 勿提交 git / 勿塞进分享包**
- 免费 Build 有额度与风控；批量 mint 请控速（`--sleep`）

---

## 相关

- CLIProxyAPI / CPA：自备；将 `cpa_auths/xai-*.json` 拷到 CPA auth-dir 即可
- 免费 Grok 4.5 只走 Build OIDC + `cli-chat-proxy`，不是网页 SSO
