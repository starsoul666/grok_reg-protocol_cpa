# Cloudflare 临时邮箱 API 适配指南（x-admin-auth）

本文用于指导其他项目中的 Agent 接入 `cloudflare_temp_email` 临时邮箱服务，完成“创建邮箱 -> 提交注册 -> 轮询邮件 -> 提取验证码”的邮箱适配。

## 1. 适配目标

在目标项目中新增一个邮箱 provider，例如 `cloudflare` / `cf_temp_mail`，通过 Cloudflare Worker API 获取临时邮箱地址，并用该地址的 JWT 拉取收件箱。

核心约定：

- 创建邮箱：走 Admin API，使用 `x-admin-auth` 认证。
- 读取邮件：走 Address API，使用创建邮箱时返回的地址 JWT。
- 不要把 `x-admin-auth` 用于 `/api/*` 收件接口，也不要把地址 JWT 用于 `/admin/*` 接口。

## 2. 必要配置项

建议在目标项目配置中新增：

```json
{
  "email_provider": "cloudflare",
  "cloudflare_api_base": "https://mail.example.com",
  "cloudflare_admin_password": "your-admin-password",
  "defaultDomains": "example.com,example.net",
  "mail_timeout": 180,
  "mail_poll_interval": 3
}
```

字段说明：

| 配置项 | 必填 | 说明 |
| --- | --- | --- |
| `email_provider` | 是 | 选择 Cloudflare 临时邮箱 provider，例如 `cloudflare` |
| `cloudflare_api_base` | 是 | Worker API 根地址，不要以 `/` 结尾 |
| `cloudflare_admin_password` | 是 | Admin 密码，请求 `/admin/*` 时放入 `x-admin-auth` |
| `defaultDomains` | 建议 | 可用邮箱域名，多个用英文逗号、中文逗号或空白分隔 |
| `mail_timeout` | 建议 | 总轮询超时，建议 `150~180` 秒 |
| `mail_poll_interval` | 建议 | 轮询间隔，建议 `3` 秒起步；高并发时更大 |

如果部署启用了站点访问密码，还需要额外配置 `site_password`，请求时加 `x-custom-auth: <site_password>`。

## 3. API 合约

### 3.1 创建邮箱地址

```http
POST /admin/new_address
Content-Type: application/json
x-admin-auth: <cloudflare_admin_password>
```

请求体：

```json
{
  "enablePrefix": true,
  "name": "abcde12x",
  "domain": "example.com"
}
```

字段说明：

- `enablePrefix`: 通常传 `true`。
- `name`: 邮箱用户名部分，由目标项目生成随机值。
- `domain`: 可选；传入后创建 `name@domain`。不传时由服务端默认域名策略决定。

成功返回：

```json
{
  "jwt": "<ADDRESS_JWT>",
  "address": "abcde12x@example.com",
  "address_id": 123
}
```

适配时必须校验返回中同时存在：

- `address`
- `jwt`

否则按创建邮箱失败处理。

### 3.2 查询当前地址信息（自检）

```http
GET /api/settings
Authorization: Bearer <ADDRESS_JWT>
```

成功返回通常包含：

```json
{
  "address": "abcde12x@example.com",
  "send_balance": 0
}
```

如果返回 `401`，说明 JWT 错误、过期，或 `cloudflare_api_base` 与 JWT 不匹配。

### 3.3 拉取邮件列表

优先使用解析后的接口：

```http
GET /api/parsed_mails?limit=20&offset=0
Authorization: Bearer <ADDRESS_JWT>
```

返回示例：

```json
{
  "results": [
    {
      "id": 42,
      "source": "noreply@example.com",
      "to": "abcde12x@example.com",
      "subject": "Your verification code is 123456",
      "text": "Your verification code is 123456",
      "html": "<p>Your verification code is <b>123456</b></p>",
      "created_at": "2026-04-21 10:00:00"
    }
  ],
  "count": 1
}
```

兼容旧部署时回退：

```http
GET /api/mails?limit=20&offset=0
Authorization: Bearer <ADDRESS_JWT>
```

`/api/mails` 可能返回原始 RFC822 内容，字段可能是 `raw` / `source` / `content` / `body` / `snippet` 等，Agent 需要做字段兼容或本地 MIME 解析。

### 3.4 获取单封邮件详情

优先：

```http
GET /api/parsed_mail/:id
Authorization: Bearer <ADDRESS_JWT>
```

回退：

```http
GET /api/mail/:id
Authorization: Bearer <ADDRESS_JWT>
```

部分旧实现可能使用：

```http
GET /api/mails/:id
Authorization: Bearer <ADDRESS_JWT>
```

建议按以上顺序依次尝试。

## 4. 目标项目实现步骤

### Step 1：增加 provider 配置读取

实现这些基础函数：

```python
def get_cf_api_base(config):
    return str(config.get("cloudflare_api_base", "")).rstrip("/")


def get_cf_admin_password(config):
    return str(config.get("cloudflare_admin_password", ""))


def get_cf_domains(config):
    raw = str(config.get("defaultDomains", "") or "")
    return [x.strip() for x in re.split(r"[,，\s]+", raw) if x.strip()]
```

### Step 2：创建临时邮箱

```python
import random
import re
import string
import requests


_domain_index = 0


def random_mail_name():
    return (
        "".join(random.choices(string.ascii_lowercase, k=5))
        + "".join(random.choices(string.digits, k=random.randint(1, 3)))
        + "".join(random.choices(string.ascii_lowercase, k=random.randint(1, 3)))
    )


def create_cf_temp_address(config):
    global _domain_index

    api_base = get_cf_api_base(config)
    admin_password = get_cf_admin_password(config)
    if not api_base:
        raise RuntimeError("cloudflare_api_base 未配置")
    if not admin_password:
        raise RuntimeError("cloudflare_admin_password 未配置")

    payload = {
        "enablePrefix": True,
        "name": random_mail_name(),
    }

    domains = get_cf_domains(config)
    if domains:
        payload["domain"] = domains[_domain_index % len(domains)]
        _domain_index += 1

    resp = requests.post(
        f"{api_base}/admin/new_address",
        json=payload,
        headers={
            "Content-Type": "application/json",
            "x-admin-auth": admin_password,
        },
        timeout=20,
    )
    resp.raise_for_status()

    data = resp.json()
    address = str(data.get("address") or "").strip()
    jwt = str(data.get("jwt") or "").strip()
    if not address or not jwt:
        raise RuntimeError(f"Cloudflare 创建邮箱缺少 address/jwt: {data}")

    return address, jwt
```

返回值含义：

- `address`: 提交给注册网站的邮箱。
- `jwt`: 后续轮询收件箱的地址凭证，建议变量名叫 `mail_token` / `address_jwt`，不要叫 `admin_token`。

### Step 3：提交邮箱后轮询验证码

```python
import re
import time


def pick_list_payload(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("results", "hydra:member", "data", "messages"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    nested = data.get("data")
    if isinstance(nested, dict) and isinstance(nested.get("messages"), list):
        return nested["messages"]
    return []


def extract_verification_code(text, subject=""):
    if subject:
        m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", subject, re.I)
        if m:
            return m.group(1)

    m = re.search(r"\b([A-Z0-9]{3}-[A-Z0-9]{3})\b", text, re.I)
    if m:
        return m.group(1)

    for pattern in (
        r"verification\s+code[:\s]+(\d{4,8})",
        r"your\s+code[:\s]+(\d{4,8})",
        r"confirm(?:ation)?\s+code[:\s]+(\d{4,8})",
        r"验证码[:：\s]*(\d{4,8})",
    ):
        m = re.search(pattern, text, re.I)
        if m:
            return m.group(1)
    return None


def get_json_or_none(resp):
    try:
        return resp.json()
    except Exception:
        return None


def cf_get_mail_list(api_base, address_jwt):
    headers = {"Authorization": f"Bearer {address_jwt}"}

    # 新部署优先使用服务端解析接口；旧部署回退 raw 列表。
    for path in ("/api/parsed_mails", "/api/mails"):
        resp = requests.get(
            f"{api_base}{path}",
            headers=headers,
            params={"limit": 20, "offset": 0},
            timeout=20,
        )
        if resp.status_code == 404:
            continue
        resp.raise_for_status()
        data = get_json_or_none(resp)
        items = pick_list_payload(data)
        if items:
            return items
    return []


def cf_get_mail_detail(api_base, address_jwt, mail_id):
    headers = {"Authorization": f"Bearer {address_jwt}"}
    for path in (f"/api/parsed_mail/{mail_id}", f"/api/mail/{mail_id}", f"/api/mails/{mail_id}"):
        resp = requests.get(f"{api_base}{path}", headers=headers, timeout=20)
        if resp.status_code in (404, 405):
            continue
        resp.raise_for_status()
        data = get_json_or_none(resp)
        if isinstance(data, dict):
            return data.get("data") if isinstance(data.get("data"), dict) else data
    return {}


def flatten_mail_text(item, detail):
    subject = str(item.get("subject") or detail.get("subject") or "")
    parts = []
    for src in (item, detail):
        for key in ("text", "raw", "source", "content", "intro", "body", "snippet"):
            value = src.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)

        html = src.get("html")
        if isinstance(html, str):
            html = [html]
        if isinstance(html, list):
            for value in html:
                if isinstance(value, str):
                    parts.append(re.sub(r"<[^>]+>", " ", value))

    return subject, "\n".join(parts)


def wait_cf_verification_code(config, address, address_jwt):
    api_base = get_cf_api_base(config)
    timeout = int(config.get("mail_timeout", 180))
    interval = max(1, int(config.get("mail_poll_interval", 3)))
    deadline = time.time() + timeout
    seen_attempts = {}

    while time.time() < deadline:
        messages = cf_get_mail_list(api_base, address_jwt)

        for item in messages:
            mail_id = item.get("id") or item.get("mail_id")
            if not mail_id:
                continue

            # 同一封邮件可能先入库后解析完成，允许重复尝试几次。
            attempt = seen_attempts.get(mail_id, 0)
            if attempt >= 5:
                continue
            seen_attempts[mail_id] = attempt + 1

            # 尽量匹配目标地址，但接口字段漂移时不要过早丢弃。
            recipients = [x.get("address", "").lower() for x in (item.get("to") or []) if isinstance(x, dict)]
            item_address = str(item.get("address") or item.get("to") or "").lower()
            if recipients and address.lower() not in recipients:
                continue
            if not recipients and item_address and address.lower() not in item_address:
                continue

            detail = cf_get_mail_detail(api_base, address_jwt, mail_id)
            subject, combined = flatten_mail_text(item, detail)
            code = extract_verification_code(combined, subject)
            if code:
                return code

        time.sleep(interval)

    raise RuntimeError(f"Cloudflare 在 {timeout}s 内未收到验证码邮件: {address}")
```

### Step 4：接入注册流程

目标项目通常已有如下抽象：

```python
email, token = get_email_and_token()
submit_email_to_target_site(email)
code = wait_code(token, email)
submit_code_to_target_site(code)
```

适配后分支应类似：

```python
if email_provider == "cloudflare":
    email, address_jwt = create_cf_temp_address(config)
    submit_email_to_target_site(email)
    code = wait_cf_verification_code(config, email, address_jwt)
    submit_code_to_target_site(code)
```

## 5. Agent 执行检查清单

让 Agent 在其他项目中适配时按此顺序执行：

1. 找到项目现有邮箱 provider 抽象：`get_email_and_token`、`wait_code`、`poll_mail`、`extract_code` 等。
2. 新增 Cloudflare 配置项，避免硬编码 API 地址和 admin 密码。
3. 实现 `/admin/new_address` 创建邮箱，必须带 `x-admin-auth`。
4. 保存创建接口返回的 `jwt`，后续 `/api/*` 使用 `Authorization: Bearer <jwt>`。
5. 优先轮询 `/api/parsed_mails`，不可用时回退 `/api/mails`。
6. 获取详情时按 `/api/parsed_mail/:id` -> `/api/mail/:id` -> `/api/mails/:id` 回退。
7. 对列表返回结构做兼容：`results`、`hydra:member`、`data`、`messages`。
8. 对正文字段做兼容：`text`、`raw`、`source`、`content`、`intro`、`body`、`snippet`、`html`。
9. 验证码提取至少支持：
   - `ABC-123` / `ABC-DEF`
   - `verification code: 123456`
   - `your code: 123456`
   - `confirmation code: 123456`
   - `验证码：123456`
10. 加入超时、轮询间隔、按邮件 ID 去重、同一邮件最多重试解析 5 次。
11. 错误日志中不要打印完整 `x-admin-auth` 或完整 JWT。

## 6. 常见错误与处理

| 错误 | 常见原因 | 处理 |
| --- | --- | --- |
| `401` on `/admin/new_address` | `x-admin-auth` 缺失或错误 | 检查 `cloudflare_admin_password` |
| `401 InvalidAddressCredentialMsg` | 地址 JWT 错误、过期或 API Base 不匹配 | 重新创建邮箱，确认同一个 `cloudflare_api_base` |
| `404 /api/parsed_mails` | 部署版本较旧 | 回退 `/api/mails` 并自行解析字段 |
| 创建成功但收不到邮件 | 域名未启用 Email Routing、Catch-all 未绑定 Worker、域名选错 | 检查 Cloudflare Email Routing 与 `defaultDomains` |
| 邮件列表有数据但提取不到码 | 只读了列表，未读详情；或只读取 `text` 未处理 `raw/html` | 拉详情并合并多字段解析 |
| 并发时验证码串号 | 没有按收件人地址过滤 | 用 `to[].address` / `address` 字段匹配目标邮箱 |

## 7. 最小自测脚本

```bash
BASE="https://mail.example.com"
ADMIN_AUTH="your-admin-password"

curl -s -X POST "$BASE/admin/new_address" \
  -H "Content-Type: application/json" \
  -H "x-admin-auth: $ADMIN_AUTH" \
  -d '{"enablePrefix":true,"name":"agenttest001","domain":"example.com"}'
```

拿到返回的 `jwt` 后：

```bash
JWT="<ADDRESS_JWT>"

curl -s "$BASE/api/settings" \
  -H "Authorization: Bearer $JWT"

curl -s "$BASE/api/parsed_mails?limit=20&offset=0" \
  -H "Authorization: Bearer $JWT"
```

如果 `/api/parsed_mails` 返回 `404`：

```bash
curl -s "$BASE/api/mails?limit=20&offset=0" \
  -H "Authorization: Bearer $JWT"
```

## 8. 适配完成标准

满足以下条件才算完成：

- 能通过配置切换到 Cloudflare 临时邮箱 provider。
- 创建邮箱请求确认为 `POST /admin/new_address`，并携带 `x-admin-auth`。
- 创建后能拿到 `address` 和 `jwt`。
- 注册流程实际提交的是 `address`。
- 收件轮询使用的是 `Authorization: Bearer <jwt>`。
- 能在真实验证码邮件中提取 code 并提交。
- 超时、401、404、非 JSON、空列表等异常都有明确错误信息。
- 日志不泄露完整 admin 密码和完整地址 JWT。
