#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dashboard/app.py —— 佳明健康助手 Web 仪表盘（M4）

本地 Flask 服务，展示：
  - 最新一天的 AI 健康日报（状态 / 建议 / 训练计划）
  - 当天核心健康指标卡片（HRV / 睡眠 / 身体电量 / 静息心率 / 压力 / 训练准备度 等）
  - 昨日活动回顾
  - 近 30 天趋势图（HRV / 静息心率 / 睡眠时长，ECharts）

数据来源：
  - AI 日报： $ASSISTANT_DATA_DIR/ai_daily_<date>.json
  - 快照：     $ASSISTANT_DATA_DIR/snapshots/daily_snapshot_<date>.json
  - 趋势：     $GARMIN_DATA_DIR/health_records.json（仓 A 产出）

用法：
  python dashboard/app.py                 # 起服务（DASH_HOST:PORT，默认 127.0.0.1:8000）
  # 浏览器打开 http://127.0.0.1:8000
"""
from __future__ import annotations

import os
import sys
import json
import datetime
from pathlib import Path

from flask import Flask, render_template, redirect, url_for, send_from_directory
from pathlib import Path as _Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
load_dotenv(BASE / ".env")

OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
SNAP_DIR = OUTPUT_DIR / "snapshots"
GARMIN_DIR = Path(os.getenv("GARMIN_DATA_DIR", str(BASE.parent / "Garmin_auto_sync" / "garmin-data")))
HEALTH_JSON = GARMIN_DIR / "health_records.json"

app = Flask(__name__, template_folder=str(BASE / "dashboard" / "templates"))
ASSETS_DIR = BASE / "assets"


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(ASSETS_DIR, "icon.ico", mimetype="image/x-icon")


@app.route("/assets/<path:fname>")
def assets_file(fname):
    return send_from_directory(ASSETS_DIR, fname)


@app.template_filter("fmt_dur")
def fmt_dur(minv):
    """分钟 -> 'XhYm' / 'Ym' / '—'"""
    try:
        minv = int(round(float(minv)))
    except Exception:
        return "—"
    h, m = divmod(minv, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


COLOR_MAP = {"green": "#00b42a", "blue": "#1668dc", "yellow": "#ff7d00", "red": "#f53f3f"}
STATUS_EMOJI = {"恢复良好": "🟢", "状态平稳": "🔵", "需谨慎": "🟡", "建议休息": "🔴"}


def load_ai(date: str):
    p = OUTPUT_DIR / f"ai_daily_{date}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def load_snap(date: str):
    p = SNAP_DIR / f"daily_snapshot_{date}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def available_days() -> list:
    days = []
    for p in OUTPUT_DIR.glob("ai_daily_*.json"):
        d = p.name[len("ai_daily_"):-5]
        days.append(d)
    return sorted(days, reverse=True)


def load_trend(days: int = 30) -> dict | None:
    if not HEALTH_JSON.exists():
        return None
    try:
        recs = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return None
    dates = sorted(recs.keys())[-days:]
    series = {"dates": [], "hrv": [], "sleep": [], "rhr": []}
    for d in dates:
        r = recs.get(d, {})
        series["dates"].append(d[5:])  # MM-DD
        series["hrv"].append(r.get("hrv_avg"))
        s = r.get("sleep_seconds")
        series["sleep"].append(round(s / 3600, 2) if s else None)
        series["rhr"].append(r.get("resting_hr"))
    return series


@app.route("/")
def index():
    days = available_days()
    if not days:
        return redirect(url_for("need_data"))
    return redirect(url_for("day", date=days[0]))


@app.route("/day/<date>")
def day(date: str):
    ai = load_ai(date)
    snap = load_snap(date)
    trend = load_trend()
    all_days = available_days()
    activity_date = ai.get("activity_date") if ai else None
    return render_template(
        "day.html",
        date=date,
        activity_date=activity_date,
        ai=ai,
        snap=snap,
        trend=trend,
        all_days=all_days,
        color_map=COLOR_MAP,
        status_emoji=STATUS_EMOJI,
    )


@app.route("/need-data")
def need_data():
    return (
        "<h2 style='font-family:sans-serif;padding:40px'>还没有分析数据</h2>"
        "<p style='font-family:sans-serif'>请先运行 <code>python ai/ai_analyze.py</code> 生成每日日报。</p>"
    )


if __name__ == "__main__":
    host = os.getenv("DASH_HOST", "127.0.0.1")
    port = int(os.getenv("DASH_PORT", "8000"))
    print(f"🌐 仪表盘已启动： http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
