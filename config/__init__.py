"""config/settings.py —— 应用配置读写（.env 为真相源）

GUI 设置页、agent 工具、服务端共用此模块，保证配置只在一处维护。
敏感字段在读取时脱敏，写入时落盘到 .env（python-dotenv）。
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv, set_key

BASE = Path(__file__).resolve().parent.parent
load_dotenv(BASE / ".env")

# 与 agent/tools.py 保持一致的键集合
CONFIG_KEYS = [
    "FEISHU_WEBHOOK_URL", "FEISHU_SECRET",
    "WXPUSHER_APP_TOKEN", "WXPUSHER_UIDS", "WXPUSHER_TOPIC_IDS",
    "AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL",
]

_SECRET_HINTS = ("SECRET", "API_KEY", "TOKEN", "UIDS")


def _mask(v: str) -> str:
    if not v:
        return ""
    if len(v) <= 6:
        return "****"
    return v[:4] + "****" + v[-2:]


def get_config() -> dict:
    """返回全部配置（敏感字段脱敏），供设置页展示。"""
    out = {}
    for k in CONFIG_KEYS:
        v = os.getenv(k, "")
        out[k] = _mask(v) if any(s in k for s in _SECRET_HINTS) else v
    return out


def set_config(key: str, value: str) -> bool:
    """写入一项配置到 .env，并立即更新环境变量。成功返回 True。"""
    key = key.strip().upper()
    if key not in CONFIG_KEYS:
        return False
    set_key(str(BASE / ".env"), key, value)
    os.environ[key] = value
    return True


def get_ai_cfg() -> dict:
    """返回 agent 使用的 LLM 配置；AI_API_KEY 为空时回退 DEEPSEEK_API_KEY。"""
    api_key = os.getenv("AI_API_KEY") or os.getenv("DEEPSEEK_API_KEY", "")
    return {
        "provider": os.getenv("AI_PROVIDER", "deepseek"),
        "base_url": os.getenv("AI_BASE_URL", ""),
        "api_key": api_key,
        "model": os.getenv("AI_MODEL", ""),
    }
