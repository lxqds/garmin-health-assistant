# 中国区账号登录修复记录（garminconnect 0.3.2）

> 日期：2026-08-24 · 环境：fnOS NAS (Python 3.11) · 实测验证：✅ DI token 交换成功

## 问题背景

中国区佳明账号（connect.garmin.cn）用 `garminconnect` 0.3.2 登录一直失败：

```
DI token exchange failed (DI token exchange failed for all client IDs), falling back to JWT_WEB
❌ 认证失败：Failed to retrieve social profile
```

MFA 验证码能通过，但登录在「换取 DI token → 拉取用户资料」这步挂掉。

## 根因（三层问题）

### 1. CN 修复从未生效（0.3.2 的 bug）

`garminconnect/__init__.py` 第 128-132 行有「CN 修复」：

```python
if is_cn:
    IOS_SERVICE_URL = "https://connect.garmin.cn/app"
    DI_TOKEN_URL = "https://sso.garmin.cn/di-oauth2-service/oauth/token"
    DI_GRANT_SERVICE_TICKET_URL = "https://connectapi.garmin.cn/..."
```

但 `client.py` 内部用的是**它自己模块级**的常量（`diauth.garmin.com`），
`__init__.py` 的赋值只改了包命名空间，从未作用于 `client.py`。
→ 中国区账号的 ticket 被拿去国际区端点换 token，必然失败。

### 2. DI grant_type 必须是 .com（关键！）

上游 PR #360（`cyberjunky/python-garminconnect`）说明：

> Initial token exchange succeeds — the `.com` DI endpoint accepts the CN ticket and issues a token.
> (token refresh 才需要 .cn 端点)

实测确认：
- `_di_token_url` = `https://diauth.garmin.cn/...`（域感知，PR #360 做法）✅
- `grant_type` = `https://connectapi.garmin.com/...`（**保持 .com 原样**）✅
- 若把 grant_type 也改成 `.cn` → CN 端点返回 `unsupported_grant_type` ❌

### 3. CN 域必须跳过 mobile 登录策略

上游 PR #363：`mobile.integration.garmin.com` 在 CN 域不存在，
CN 账号应跳过 `mobile+cffi` / `mobile+requests`，直接走 portal 网页登录。

## 修复补丁（对 venv 内 garminconnect/client.py）

### 补丁 A：`Client.__init__` 加域感知 DI token 端点

```python
# 位置: __init__ 中 _portal_service_url 之后
self._di_token_url = f"https://diauth.{domain}/di-oauth2-service/oauth/token"
```

### 补丁 B：`login()` 跳过 mobile 策略

```python
# 位置: strategies 定义之后
if self.domain != "garmin.com":
    strategies = [
        (n, fn) for n, fn in strategies if not n.startswith("mobile")
    ]
```

### 补丁 C：`_exchange_service_ticket` 用 `self._di_token_url`

```python
# 原: di_token_url = DI_TOKEN_URL
di_token_url = self._di_token_url
```

### 补丁 D：`_refresh_di_token` 同样用 `self._di_token_url`

```python
# 原: di_token_url = DI_TOKEN_URL
di_token_url = self._di_token_url
```

⚠️ **注意：`DI_GRANT_TYPE` 保持 `.com` 原样，不要改成域感知！**

## 其他配套修复

### 登录不做退避重试（避免验证码轰炸）

`garmin_sync.py` 的 `do_login()` 原来用 `call_with_backoff(client.login)`，
登录失败会重试 5 次，**每次重试都触发佳明发一次新 MFA 验证码**。
改为直接调用：

```python
def do_login(client, tokenstore: Path):
    """登录并保存令牌。注意：不做退避重试——每次重试都会重新触发佳明发送 MFA 验证码。"""
    return client.login(str(tokenstore))
```

## 验证结果

| 步骤 | 结果 |
|------|------|
| MFA 验证码 | ✅ 通过 |
| DI token 交换 (diauth.garmin.cn + .com grant_type) | ✅ HTTP 200, access_token 返回 |
| 登录 | ✅ LOGIN SUCCESS |

## 后续注意

- 本机 garminconnect 版本 0.3.2（pip 源最新版）；GitHub 最新 0.3.12 要求 Python ≥3.12，
  本机只有 3.11，故手动打补丁而非升级。升级到 0.3.12+ 后这些补丁已内置，无需再打。
- 升级 Python 到 3.12 后建议直接 `pip install garminconnect>=0.3.12` 并移除本地补丁。
