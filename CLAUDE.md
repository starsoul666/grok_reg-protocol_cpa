# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

这是一个 Grok 账号批量注册工具，支持：
1. **Hotmail/Outlook 邮箱验证码接收**（四段凭证格式：`邮箱----密码----ClientID----Token`）
2. **协议优先的 OIDC/CPA 导出**：注册成功后自动将 SSO 转换为 CPA 认证文件（用于免费 Grok 4.5）

## 常用命令

### 环境初始化

```bash
cd /path/to/grok_reg-protocol_cpa
uv sync
# 或使用 mise
mise install
mise run deps
```

### 注册新账号

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

### 存量号补 CPA（只 mint，不重新注册）

```bash
uv run python -u scripts/backfill_cpa_xai_from_accounts.py \
  --accounts accounts_cli.txt \
  --limit 1 --probe --timeout 300
```

### CPA 文件验证

```bash
# 测试免费 Grok 4.5 API
KEY="<你的 CPA API KEY>"
curl -sS http://127.0.0.1:8317/v1/models -H "Authorization: Bearer $KEY"
curl -sS http://127.0.0.1:8317/v1/chat/completions \
  -H "Authorization: Bearer $KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"grok-4.5","messages":[{"role":"user","content":"Reply with exactly OK"}],"stream":false}'
```

## 架构概览

```
grok_reg-protocol_cpa/
├── register_cli.py           # CLI 批量注册入口
├── grok_register_ttk.py      # 浏览器注册核心（含 Hotmail/Outlook 邮箱）
├── cpa_export.py             # 注册成功后的 CPA 导出 hook
├── cpa_xai/                  # OIDC/CPA 铸造模块
│   ├── pkce_mint.py          # 纯 HTTP PKCE authorization-code（协议优先）
│   ├── protocol_mint.py      # 旧 Device Flow 兼容路径（默认不回退）
│   ├── mint.py               # 协议 → 浏览器回退编排
│   ├── browser_confirm.py    # 浏览器 consent 回退
│   ├── oauth_device.py       # OAuth Device Flow 核心逻辑
│   └── probe.py              # API 探测验证
├── scripts/
│   ├── backfill_cpa_xai_from_accounts.py  # 存量号补 CPA
│   └── export_cpa_xai_from_grok_auth.py   # 从现有 auth.json 导出
└── turnstilePatch/           # Cloudflare Turnstile 绕过扩展
```

### 核心流程

```
注册成功拿到 sso cookie
        ↓
【优先】pkce_mint：curl_cffi + sso cookie
   authorize → cookie-setter → consent → authorization_code → token
        ↓ 成功
  cpa_auths/xai-<email>.json   (mint_method=pkce)
        ↓ 失败
默认失败并记录；如显式开启 cpa_allow_device_flow_fallback，才回退旧 Device Flow / browser_confirm
        ↓
  避免产出 /models 可见但 chat endpoint 403 的坏 token
```

## 关键配置

配置位于 `config.json`（从 `config.example.json` 复制）：

| 字段 | 说明 |
|------|------|
| `email_provider` | 邮箱类型：`hotmail`、`cloudmail`、`cloudflare` 等 |
| `hotmail_accounts_file` | Hotmail 凭证文件路径 |
| `cpa_export_enabled` | 是否启用 CPA 导出（默认 `true`） |
| `cpa_prefer_protocol` | 是否优先协议 mint（默认 `true`） |
| `cpa_protocol_flow` | 协议 mint 流程：`pkce`（默认推荐）或 `device`（旧 Device Flow） |
| `cpa_auth_dir` | CPA 认证文件输出目录（默认 `./cpa_auths`） |
| `cpa_copy_to_hotload` | mint 成功后是否移动到 CLIProxyAPI 目录（移动后 cpa_auths 不保留副本，默认 `true`） |
| `cpa_hotload_dir` | CLIProxyAPI 账号目录（默认 `~/.cli-proxy-api`） |
| `cpa_base_url` | 免费 Grok 4.5 上游 API（必须为 `https://cli-chat-proxy.grok.com/v1`） |
| `proxy` / `cpa_proxy` | 注册和 mint 的代理配置 |

## 数据文件

| 文件 | 格式 | 说明 |
|------|------|------|
| `mail_credentials.txt` | `邮箱----密码----ClientID----Token` | Hotmail 四段凭证 |
| `accounts_cli.txt` | `邮箱----密码----sso` | 主账本 |
| `cpa_auths/xai-*.json` | CPA OIDC 格式 | CPA 认证文件（勿提交 git） |
| `~/.cli-proxy-api/xai-*.json` | 同上 | mint 成功后自动推送的 CLIProxyAPI 热加载副本 |

## 技术栈

- **Python 3.13**
- **依赖管理**：`uv` 或 `mise`
- **核心库**：`DrissionPage`（Chrome 自动化）、`curl_cffi`（HTTP 客户端，带 TLS 指纹）
- **可选**：本地 `grok2api`（:8000）、CLIProxyAPI（:8317）
