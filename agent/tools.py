"""agent/tools.py —— agent 可调用的工具集合

每个工具 = (name, description, parameters(JSON schema), callable)。
Agent 用 function-calling 决定调用哪个；callable 返回字符串结果（回灌给 LLM）。

覆盖能力：
  - 查佳明健康/计划（只读）
  - 发飞书 / 微信（推送）
  - 触发一次数据同步（写：跑仓 A 的 garmin_sync）
  - 读写应用配置（写：改 .env）
  - 设提醒（写：落本地 reminders.json，由 GUI 弹窗）
"""
from __future__ import annotations

import os
import sys
import json
import subprocess
import datetime
from pathlib import Path
from typing import Callable, Dict, List

from app_paths import BASE
from dotenv import load_dotenv, set_key

load_dotenv(BASE / ".env")

OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))

# 配置键（供 get/set_config 使用，集中管理避免误写）
CONFIG_KEYS = [
    "FEISHU_WEBHOOK_URL", "FEISHU_SECRET",
    "WXPUSHER_APP_TOKEN", "WXPUSHER_UIDS", "WXPUSHER_TOPIC_IDS",
    "AI_PROVIDER", "AI_BASE_URL", "AI_API_KEY", "AI_MODEL",
]


def _today() -> str:
    return datetime.date.today().isoformat()


def _load_ai(date: str) -> dict:
    p = OUTPUT_DIR / f"ai_daily_{date}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_snap(date: str) -> dict:
    p = OUTPUT_DIR / "snapshots" / f"daily_snapshot_{date}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _ensure_llm_advice(d: dict, date: str) -> dict:
    """若 ai_daily_<date>.json 的 advice 是规则兜底（advice_source != 'llm'），
    现场调一次 LLM（DeepSeek/可切）生成并写回 .json；
    仪表盘也能直接看到 AI 内容，无需手动跑分析脚本。"""
    if d.get("advice_source") == "llm":
        return d
    snap = _load_snap(date)
    if not snap:
        return d
    try:
        from ai.ai_analyze import call_deepseek  # noqa: WPS433
        fresh = call_deepseek(snap)
    except Exception as e:
        return d
    if not fresh or not fresh.get("advice"):
        return d
    # 只覆盖 AI 相关的字段，保留 status_metrics/training_plan/plan_date 等
    for k in ("overall_status", "status_color", "metrics", "status_metrics", "advice"):
        if k in fresh:
            d[k] = fresh[k]
    d["advice_source"] = "llm"
    d["engine"] = fresh.get("engine", "deepseek")
    p = OUTPUT_DIR / f"ai_daily_{date}.json"
    try:
        p.write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return d


# ---------------------------------------------------------------- 工具实现
def t_query_health(date: str = "") -> str:
    """返回指定日期（默认今天）的健康日报摘要。
    若当前是规则兜底（advice_source != 'llm'），会现场调 LLM 生成并写回，
    因此用户无需先手动跑分析脚本也能拿到 AI 建议。"""
    date = date or _today()
    d = _load_ai(date)
    if not d:
        return f"暂无 {date} 的 AI 分析数据（可先触发同步并生成日报）。"
    d = _ensure_llm_advice(d, date)
    lines = [f"【{date} 健康日报 · {d.get('engine','?')}】",
             f"总体状态：{d.get('overall_status','-')}"]
    for m in (d.get("status_metrics") or []):
        lines.append(f"- {m['name']}：{m['value']}")
    if d.get("advice"):
        lines.append("")
        lines.append("【AI 建议】")
        lines.append(d["advice"])
    return "\n".join(lines)


def t_query_plan(which: str = "today") -> str:
    """返回今日或明日的训练计划。which=today|tomorrow。"""
    if which not in ("today", "tomorrow"):
        which = "today"
    d = _load_ai(_today())
    if not d:
        return "暂无今日分析数据。"
    if which == "today":
        plan = d.get("training_plan") or []
        label = f"今日计划（{d.get('plan_date','')}）"
    else:
        plan = d.get("tomorrow_plan") or []
        label = f"明日计划（{d.get('next_date','')}）"
    if not plan:
        return f"{label}：休息 / 未安排训练。"
    items = [f"- {p.get('type','训练')}"
             + (f" · {p['duration']}" if p.get('duration') else "")
             + (f" · {p['zone']}" if p.get('zone') else "")
             for p in plan]
    return f"{label}：\n" + "\n".join(items)


def t_push_feishu(content: str) -> str:
    """把一段文本/Markdown 推送到飞书群。"""
    from notify import push_feishu
    webhook = os.getenv("FEISHU_WEBHOOK_URL")
    if not webhook:
        return "未配置 FEISHU_WEBHOOK_URL，无法推送。"
    card = {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "佳明健康助手 · 消息"},
                   "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": content}}],
    }
    ok, resp = push_feishu.send_card(card, webhook, os.getenv("FEISHU_SECRET", ""))
    return "飞书推送成功" if ok else f"飞书推送失败：{resp}"


def t_push_wx(content: str) -> str:
    """把一段 HTML 推送到微信（WxPusher）。"""
    from notify import push_wx
    app_token = os.getenv("WXPUSHER_APP_TOKEN", "")
    uids = [u.strip() for u in (os.getenv("WXPUSHER_UIDS", "") or "").split(",") if u.strip()]
    if not app_token:
        return "未配置 WXPUSHER_APP_TOKEN，无法推送。"
    html = (f"<div style='font-family:-apple-system,sans-serif;max-width:420px;'>"
            f"<div style='font-size:15px;font-weight:700;'>{content}</div></div>")
    payload = {"appToken": app_token, "content": html, "contentType": 1,
               "uids": uids, "summary": "佳明健康助手消息"}
    ok, resp = push_wx.send(payload)
    return "微信推送成功" if ok else f"微信推送失败：{resp}"


def t_trigger_sync() -> str:
    """触发一次佳明数据同步（调用 vendor 的 garmin_sync.fetch），拉取最新健康/活动数据。"""
    try:
        from vendor.garmin_sync import cmd_fetch
        cmd_fetch(days=30)
        return "✅ 同步完成（已拉取最近数据，可在仪表盘查看）。"
    except Exception as e:
        return f"同步失败：{e}"


def t_garmin_login(email: str = "", password: str = "", region: str = "cn", mfa: str = "") -> str:
    """登录佳明账号（中国区 connect.garmin.cn / 国际区 connect.garmin.com）。
    若账号开启两步验证，第一步返回「需要验证码」，再带 mfa 调用一次即可完成。
    登录成功后令牌本地留存，之后同步无需再输密码。"""
    from agent import garmin_login as gl
    res = gl.garmin_login(email=email, password=password, region=region, mfa=mfa)
    if not res.get("ok"):
        return "佳明登录失败：" + res.get("error", "未知错误")
    if res.get("mfa_required"):
        return "✅ 已发起登录，但该账号开启了两步验证，请把手机收到的 6 位验证码发我（我会用 mfa 参数再次调用完成登录）。"
    return "✅ " + res.get("message", "登录成功")


def t_get_config() -> str:
    """返回当前应用配置（敏感字段脱敏）。"""
    shown = {}
    for k in CONFIG_KEYS:
        v = os.getenv(k, "")
        if not v:
            shown[k] = "(空)"
        elif any(s in k for s in ("SECRET", "API_KEY", "TOKEN", "UIDS")):
            shown[k] = v[:4] + "****" + v[-2:] if len(v) > 6 else "****"
        else:
            shown[k] = v
    return "当前配置：\n" + "\n".join(f"- {k} = {v}" for k, v in shown.items())


def t_set_config(key: str, value: str) -> str:
    """写入一项应用配置（落 .env，并立即生效到环境变量）。"""
    key = key.strip().upper()
    if key not in CONFIG_KEYS:
        return f"不支持的配置键：{key}（可选：{', '.join(CONFIG_KEYS)}）"
    env_path = BASE / ".env"
    set_key(str(env_path), key, value)
    os.environ[key] = value
    return f"已保存 {key}（重启服务后完全生效，本次会话已更新环境变量）。"


def t_set_reminder(text: str, when: str = "") -> str:
    """设置一个本地提醒（落 reminders.json，由桌面端弹窗）。when 形如 HH:MM 或留空（默认 1 小时后）。"""
    reminders_path = OUTPUT_DIR / "reminders.json"
    try:
        data = json.loads(reminders_path.read_text(encoding="utf-8")) if reminders_path.exists() else []
    except Exception:
        data = []
    when = when or "1小时后"
    data.append({"text": text, "when": when, "created": _today()})
    reminders_path.parent.mkdir(parents=True, exist_ok=True)
    reminders_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return f"已设置提醒：{text}（{when}）"


# ---------------------------------------------------------------- 工具注册
class Tool:
    def __init__(self, name: str, description: str, parameters: dict, fn: Callable):
        self.name = name
        self.description = description
        self.parameters = parameters
        self.fn = fn

    def schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __call__(self, **kwargs):
        return self.fn(**kwargs)


def build_tools() -> Dict[str, Tool]:
    return {
        "query_health": Tool(
            "query_health",
            "查询指定日期（默认今天）的佳明健康日报摘要（状态/指标/建议）。",
            {"type": "object", "properties": {
                "date": {"type": "string", "description": "日期 YYYY-MM-DD，留空=今天"}}},
            t_query_health,
        ),
        "query_plan": Tool(
            "query_plan",
            "查询今日或明日的训练计划（来自 Garmin Coach）。",
            {"type": "object", "properties": {
                "which": {"type": "string", "enum": ["today", "tomorrow"], "description": "today=今日, tomorrow=明日"}}},
            t_query_plan,
        ),
        "push_feishu": Tool(
            "push_feishu",
            "把一段内容推送到飞书群（需已配置 FEISHU_WEBHOOK_URL）。",
            {"type": "object", "properties": {
                "content": {"type": "string", "description": "要推送的文本/Markdown 内容"}}},
            t_push_feishu,
        ),
        "push_wx": Tool(
            "push_wx",
            "把一段内容推送到微信（需已配置 WXPUSHER_APP_TOKEN/UIDS）。",
            {"type": "object", "properties": {
                "content": {"type": "string", "description": "要推送的内容"}}},
            t_push_wx,
        ),
        "trigger_sync": Tool(
            "trigger_sync",
            "触发一次佳明数据同步，拉取最新健康与活动数据（跑仓 A 同步脚本）。",
            {"type": "object", "properties": {}},
            t_trigger_sync,
        ),
        "garmin_login": Tool(
            "garmin_login",
            "登录佳明账号（中国区/国际区），凭据用于同步数据；若开启两步验证需再带 mfa 验证码调用一次。",
            {"type": "object", "properties": {
                "email": {"type": "string", "description": "佳明账号邮箱"},
                "password": {"type": "string", "description": "佳明账号密码"},
                "region": {"type": "string", "enum": ["cn", "global"], "description": "cn=中国区(默认), global=国际区"},
                "mfa": {"type": "string", "description": "两步验证码（仅当第一步提示需要时才填）"}}},
            t_garmin_login,
        ),
        "get_config": Tool(
            "get_config",
            "查看当前应用配置（飞书/微信/AI，敏感字段脱敏）。",
            {"type": "object", "properties": {}},
            t_get_config,
        ),
        "set_config": Tool(
            "set_config",
            "写入一项应用配置（落 .env 并立即生效）。常用于 AI 帮用户填写 API。",
            {"type": "object", "properties": {
                "key": {"type": "string", "description": "配置键，如 FEISHU_WEBHOOK_URL / WXPUSHER_APP_TOKEN / AI_API_KEY / AI_MODEL"},
                "value": {"type": "string", "description": "配置值"}}},
            t_set_config,
        ),
        "set_reminder": Tool(
            "set_reminder",
            "设置一个本地提醒，由桌面端弹窗提示。",
            {"type": "object", "properties": {
                "text": {"type": "string", "description": "提醒内容"},
                "when": {"type": "string", "description": "时间，如 21:30，留空默认 1 小时后"}}},
            t_set_reminder,
        ),
    }
