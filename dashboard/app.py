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


def _rec(d: str) -> dict:
    """取仓 A 健康记录（容错）。"""
    try:
        recs = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return recs.get(d, {})


def _unpack(v):
    """把 {'value': X, 'unit':...} 这种结构解出数值；裸值直接返回。"""
    if v is None:
        return None
    if isinstance(v, dict):
        return v.get("value")
    return v


def _metric(d: str, ai: dict | None, snap: dict | None, key_ai: str, key_snap: str, key_rec: str):
    """统一取值：AI 日报 > snapshot.metrics > health_records。"""
    for src in ((ai or {}), ((snap or {}).get("metrics") or {}), _rec(d)):
        if not src:
            continue
        for k in (key_ai, key_snap, key_rec):
            v = _unpack(src.get(k))
            if v is not None:
                return v
    return None


def build_week_strip(date: str, all_days: list, window: int = 5) -> list:
    """以 date 为中心，前后各 (window-1)/2 天，共 window 天；不足则向两端补齐。
    返回 [{date, weekday, hrv, sleep_h, rhr, bb, steps, color, is_today}]"""
    import datetime as _dt
    try:
        center = _dt.date.fromisoformat(date)
    except Exception:
        return []

    half = (window - 1) // 2
    days_set = set(all_days)
    # 候选：以 center 为中心 + window 天，按可用的（all_days）过滤
    candidates = []
    for offset in range(-half, half + 1):
        d = (center + _dt.timedelta(days=offset)).isoformat()
        candidates.append((d, offset == 0))

    strip = []
    for d, is_today in candidates:
        rec = _rec(d)
        ai = load_ai(d)
        snap = load_snap(d)

        def g(ai_k, snap_k, rec_k):
            return _metric(d, ai, snap, ai_k, snap_k, rec_k)

        hrv = g("hrv", "hrv_avg", "hrv_avg")
        sleep_s = g("sleep_seconds", "sleep_seconds", "sleep_seconds")
        rhr = g("resting_hr", "resting_hr", "resting_hr")
        bb = g("body_battery_high", "body_battery_high", "body_battery_high")
        steps = g("steps", "steps", "steps")

        # 颜色：基于该日 AI 报告的 overall_status / status_color
        color = "#1668dc"
        if ai:
            color = COLOR_MAP.get(ai.get("status_color", ""), "#1668dc")

        # 睡眠：秒 → 小时（保留 1 位小数）
        sleep_h = None
        if sleep_s:
            sleep_h = round(sleep_s / 3600, 1)

        try:
            weekday = ["一", "二", "三", "四", "五", "六", "日"][_dt.date.fromisoformat(d).weekday()]
        except Exception:
            weekday = ""

        strip.append({
            "date": d,
            "has_data": d in days_set,
            "is_today": is_today,
            "weekday": weekday,
            "md": d[5:],  # MM-DD
            "color": color,
            "hrv": hrv,
            "sleep_h": sleep_h,
            "rhr": rhr,
            "bb": bb,
            "steps": steps,
        })
    return strip


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
    week_strip = build_week_strip(date, all_days)
    return render_template(
        "day.html",
        date=date,
        activity_date=activity_date,
        ai=ai,
        snap=snap,
        trend=trend,
        all_days=all_days,
        week_strip=week_strip,
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
