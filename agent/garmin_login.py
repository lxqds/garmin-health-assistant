"""agent/garmin_login.py —— 网页端佳明账号登录（含两步验证 MFA）

设计要点：
  - 佳明中国区账号（connect.garmin.cn）与国际区（connect.garmin.com）数据隔离，
    登录时按 region 切到对应域名，令牌按区分别保存到 garmin-data/.garmin_tokens_<region>/。
  - 登录走两步：
      第一步（提交邮箱/密码）：用 return_on_mfa=True 调 Garmin.login，
        若账号开启了两步验证，则登录不会阻塞，而是返回 mfa_status（客户端会话状态），
        我们把 Garmin 对象与会话状态暂存在进程内存，提示用户去手机取验证码。
      第二步（提交验证码）：用同一个 Garmin 对象调 resume_login 完成登录并落盘令牌。
  - 登录成功后令牌（约 1 年有效、可自动刷新）本地留存，之后「触发同步」只带令牌跑，
    无需再输密码/MFA。凭据（邮箱/密码）保存在 garmin-data/.garmin_config.json（已 .gitignore）。
"""
from __future__ import annotations

import os
import sys
import json
import contextlib
import threading
import datetime
from pathlib import Path

# 仓 A（佳明运动数据同步）与本工程是平级目录（同处工作区根目录下），按约定通过相对路径定位。
# 与 agent/tools.py 保持一致：BASE = 本工程根(garmin-health-assistant)，其上级即工作区根。
BASE = Path(__file__).resolve().parent.parent
_GARMIN_SYNC_DIR = BASE.parent / "佳明运动数据同步"
if str(_GARMIN_SYNC_DIR) not in sys.path:
    sys.path.insert(0, str(_GARMIN_SYNC_DIR))

from garminconnect import (  # noqa: E402
    GarminConnectAuthenticationError,
    GarminConnectTooManyRequestsError,
)

# 复用仓 A 的登录相关辅助（region 解析 / 令牌目录 / 配置路径）
from garmin_sync import (  # noqa: E402
    load_config, CONFIG_PATH, resolve_region, tokenstore_for,
    Garmin as _Garmin,
)

# 进程内保存「进行中的 MFA 登录会话」（同一次浏览器会话的两步请求共享）。
_login_lock = threading.Lock()
_mfa_session: dict = {}


def _write_config(email: str, password: str, region: str):
    """把邮箱/密码/区写入 .garmin_config.json，保留已有 cookie 字段。"""
    cfg = {}
    if CONFIG_PATH.exists():
        try:
            cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            cfg = {}
    cfg["email"] = email
    cfg["password"] = password
    cfg["region"] = region
    for k in ("_comment", "_cookie_login_comment", "cookie_order_token", "cookie_jwt_fgp"):
        cfg.setdefault(k, "")
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def garmin_login(email: str = "", password: str = "", region: str = "cn", mfa: str = "") -> dict:
    """两步登录。

    当 mfa 为空 → 第一步（邮箱/密码）。返回 {ok, mfa_required, message}。
    当 mfa 非空   → 第二步（验证码）。需要此前已在内存里保留 MFA 会话，否则报错。
    """
    email = (email or "").strip()
    password = (password or "").strip()
    region = (region or "cn").strip().lower()
    mfa = (mfa or "").strip()

    if not mfa:
        # ---- 第一步：邮箱/密码 ----
        if not email or not password:
            return {"ok": False, "error": "请填写佳明账号（邮箱）和密码。"}
        _write_config(email, password, region)
        _, is_cn, _domain = resolve_region({"region": region})
        tokenstore = tokenstore_for(resolve_region({"region": region})[0])
        try:
            client = _Garmin(email, password, is_cn=is_cn, return_on_mfa=True)
            mfa_status, _legacy = client.login(str(tokenstore))
        except GarminConnectAuthenticationError as e:
            return {"ok": False, "error": f"账号或密码错误，或 MFA 未通过：{e}"}
        except GarminConnectTooManyRequestsError:
            return {"ok": False, "error": "登录尝试过于频繁，请几分钟后再试。"}
        except Exception as e:
            return {"ok": False, "error": f"登录失败：{e}"}

        if mfa_status:  # 需要两步验证
            with _login_lock:
                _mfa_session.clear()
                _mfa_session.update({
                    "client": client,
                    "client_state": mfa_status,
                    "tokenstore": tokenstore,
                    "email": email,
                    "region": region,
                })
            return {
                "ok": True,
                "mfa_required": True,
                "message": "该账号开启了两步验证，请输入手机上收到的 6 位验证码。",
            }
        # 无需 MFA，登录已完成且令牌已落盘
        with _login_lock:
            _mfa_session.clear()
        return {"ok": True, "mfa_required": False, "message": "✅ 登录成功，令牌已保存（约 1 年有效，之后自动刷新）。"}
    else:
        # ---- 第二步：MFA 验证码 ----
        with _login_lock:
            sess = dict(_mfa_session)
        client = sess.get("client")
        client_state = sess.get("client_state")
        tokenstore = sess.get("tokenstore")
        if not client or not client_state:
            return {"ok": False, "error": "登录会话已过期，请重新输入邮箱和密码发起登录。"}
        try:
            client.resume_login(client_state, mfa)
        except GarminConnectAuthenticationError as e:
            return {"ok": False, "error": f"验证码错误或未通过：{e}"}
        except Exception as e:
            return {"ok": False, "error": f"完成登录失败：{e}"}
        # 落盘令牌
        try:
            client.client._tokenstore_path = str(tokenstore)
            with contextlib.suppress(Exception):
                client.client.dump(str(tokenstore))
        except Exception:
            pass
        with _login_lock:
            _mfa_session.clear()
        return {"ok": True, "mfa_required": False, "message": "✅ 登录成功，令牌已保存（约 1 年有效，之后自动刷新）。"}


def garmin_status() -> dict:
    """返回当前登录状态：是否已保存令牌、数据区、账号邮箱。"""
    cfg = load_config() or {}
    region = (cfg.get("region") or "cn").strip().lower()
    _, _, _domain = resolve_region(cfg)
    tokenstore = tokenstore_for(resolve_region(cfg)[0])
    token_file = tokenstore / "garmin_tokens.json"
    has_token = token_file.exists()
    email = (cfg.get("email") or "").strip()
    # 邮箱脱敏展示
    masked = ""
    if email and "@" in email:
        u, d = email.split("@", 1)
        masked = (u[:2] + "***" + u[-1:] + "@" + d) if len(u) > 3 else ("***@" + d)
    return {
        "logged_in": has_token,
        "region": region,
        "domain": _domain,
        "email": masked,
        "tokenstore": tokenstore.name,
    }


def garmin_clear() -> dict:
    """清除本地令牌（切换账号/区前用）。

    注意：不能用 shutil.rmtree(tokenstore, ignore_errors=True)。
    Windows 上令牌文件若被占用（例如 garminconnect 持有的句柄未释放），
    rmtree 会静默失败——返回成功但文件仍在，导致「假清除」、状态仍显示已登录。
    这里逐文件删除并校验，删除失败会如实报错。
    """
    import shutil
    cfg = load_config() or {}
    tokenstore = tokenstore_for(resolve_region(cfg)[0])
    with _login_lock:
        _mfa_session.clear()
    if not tokenstore.exists():
        return {"ok": True, "message": "无令牌可清除（本就未登录）。"}
    errors = []
    for name in os.listdir(tokenstore):
        p = tokenstore / name
        try:
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        except Exception as e:
            errors.append(f"{name}: {e}")
    try:
        tokenstore.rmdir()
    except Exception as e:
        errors.append(f"rmdir: {e}")
    if tokenstore.exists():
        detail = "; ".join(errors) if errors else "目录仍存在（可能被其他进程占用）"
        return {"ok": False, "error": f"清除不完整：{detail}。请关闭占用程序后重试。"}
    return {"ok": True, "message": f"已清除令牌目录：{tokenstore.name}"}
