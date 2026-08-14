"""server/app.py —— 桌面应用统一后端

复用现有 dashboard.app 的 Flask 实例与仪表盘路由，额外暴露 agent / 配置 / 推送 / 同步 API。
桌面端（PyQt WebView + 聊天面板）与这些 API 通信。
"""
from __future__ import annotations

import os
import sys
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from flask import Flask, request, jsonify

# 复用现有仪表盘（含 / /day/<date> 等路由）
from dashboard.app import app as dash_app

# 本模块在 dash_app 上追加路由
app = dash_app

from agent.core import Agent
from agent.tools import build_tools
from agent import garmin_login
from config import get_config, set_config, get_ai_cfg

TOOLS = build_tools()


@app.route("/api/config", methods=["GET"])
def api_get_config():
    return jsonify(get_config())


@app.route("/api/config", methods=["POST"])
def api_set_config():
    data = request.get_json(silent=True) or {}
    key = data.get("key", "")
    value = data.get("value", "")
    if set_config(key, value):
        return jsonify({"ok": True, "key": key})
    return jsonify({"ok": False, "error": f"不支持的配置键：{key}"}), 400


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    history = data.get("history") or []
    if not message:
        return jsonify({"ok": False, "error": "message 为空"}), 400
    cfg = get_ai_cfg()
    if not cfg.get("api_key"):
        return jsonify({
            "ok": True,
            "reply": "还未配置 AI API Key。请在「设置 → AI」里填入 API Key（或让我帮你填：把 key 发我，我用 set_config 写入 AI_API_KEY）。",
            "used_tools": [],
        })
    try:
        agent = Agent(provider_cfg=cfg, tools=TOOLS)
        reply = agent.run(message, history=history)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Agent 执行失败：{e}"}), 500
    return jsonify({"ok": True, "reply": reply})


@app.route("/api/push/feishu", methods=["POST"])
def api_push_feishu():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content:
        return jsonify({"ok": False, "error": "content 为空"}), 400
    return jsonify({"ok": True, "result": TOOLS["push_feishu"](content=content)})


@app.route("/api/push/wx", methods=["POST"])
def api_push_wx():
    data = request.get_json(silent=True) or {}
    content = data.get("content", "")
    if not content:
        return jsonify({"ok": False, "error": "content 为空"}), 400
    return jsonify({"ok": True, "result": TOOLS["push_wx"](content=content)})


@app.route("/api/sync", methods=["POST"])
def api_sync():
    result = TOOLS["trigger_sync"]()
    # 同步失败时 t_trigger_sync 返回以「同步失败」开头的字符串，需如实上报 ok:false
    ok = not str(result).startswith("同步失败")
    return jsonify({"ok": ok, "result": result})


@app.route("/api/garmin-status", methods=["GET"])
def api_garmin_status():
    return jsonify(garmin_login.garmin_status())


@app.route("/api/garmin-login", methods=["POST"])
def api_garmin_login():
    data = request.get_json(silent=True) or {}
    res = garmin_login.garmin_login(
        email=data.get("email", ""),
        password=data.get("password", ""),
        region=data.get("region", "cn"),
        mfa=data.get("mfa", ""),
    )
    return jsonify(res)


@app.route("/api/garmin-clear", methods=["POST"])
def api_garmin_clear():
    return jsonify(garmin_login.garmin_clear())


@app.route("/api/reminders", methods=["GET"])
def api_reminders():
    p = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data"))) / "reminders.json"
    if not p.exists():
        return jsonify([])
    try:
        return jsonify(json.loads(p.read_text(encoding="utf-8")))
    except Exception:
        return jsonify([])


if __name__ == "__main__":
    port = int(os.getenv("APP_PORT", "8500"))
    app.run(host="127.0.0.1", port=port, debug=False)
