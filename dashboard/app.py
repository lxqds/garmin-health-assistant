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
import threading
from pathlib import Path

from flask import Flask, render_template, redirect, url_for, send_from_directory, request, jsonify
from pathlib import Path as _Path

from app_paths import BASE, RES_DIR, GARMIN_DATA_DIR, ASSISTANT_DATA_DIR

from dotenv import load_dotenv
load_dotenv(BASE / ".env")

OUTPUT_DIR = ASSISTANT_DATA_DIR
SNAP_DIR = OUTPUT_DIR / "snapshots"
GARMIN_DIR = GARMIN_DATA_DIR
HEALTH_JSON = GARMIN_DIR / "health_records.json"

app = Flask(__name__, template_folder=str(RES_DIR / "dashboard" / "templates"))
ASSETS_DIR = RES_DIR / "assets"


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(ASSETS_DIR, "icon.ico", mimetype="image/x-icon")


@app.route("/assets/<path:fname>")
def assets_file(fname):
    return send_from_directory(ASSETS_DIR, fname)


@app.route("/manifest.webmanifest")
def manifest_file():
    return send_from_directory(ASSETS_DIR, "manifest.webmanifest", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    resp = send_from_directory(ASSETS_DIR, "service-worker.js", mimetype="application/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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

# ---------------------------------------------------------------- 应用元信息（/api/about）
APP_VERSION = "1.5.0"

APP_FEATURES = [
    ("📊 每日健康日报", "自动从 Garmin 同步 HRV、睡眠、静息心率、身体电量、步数等，生成可视化卡片与趋势图。"),
    ("🤖 AI 分析与建议", "基于 DeepSeek（可切换 OpenAI / 本地 Ollama）解读当日状态，并结合 Garmin Coach 训练计划给出训练建议。"),
    ("🗓️ 今日 / 明日计划", "直接读取 Garmin Coach 训练计划，区分「今日计划」与「明日计划」。"),
    ("🕑 历史补分析", "对历史上未生成 AI 摘要的日期，可单日或批量补生成（顶部「补分析历史数据」）。"),
    ("🔄 一键同步", "在仪表盘顶部即可触发一次 Garmin 数据同步，无需进菜单。"),
    ("💬 内置 AI 助手", "右侧聊天面板可与 AI 对话、查询健康数据、推送飞书 / 微信、设置提醒。"),
    ("📲 飞书 / 微信推送", "把每日健康日报一键推送到飞书群或微信（WxPusher）。"),
]

APP_CHANGELOG = [
    ("1.5.0", "2026-07-24", [
        "顶部新增「功能介绍 / AI设置 / 版本更新信息」按钮",
        "「触发同步」移至顶部可见按钮，不再藏在菜单下拉",
    ]),
    ("1.4.0", "2026-07-24", [
        "无 AI 分析日灰显但仍可点击，展示基础健康数据",
        "新增单日「生成 AI 分析」与「补分析历史数据」批量按钮",
        "历史分析按目标日渲染训练计划",
    ]),
    ("1.3.0", "2026-07-23", [
        "桌面应用（PyQt6 壳 + 内置 Agent 框架）",
        "AI 聊天面板支持 Markdown 排版",
        "顶部 5 天数据横排",
    ]),
    ("1.2.0", "2026-07-23", [
        "新增「明日计划」段，今日 / 明日计划标签改为「计划（日期）」",
        "拆分报告日与活动日，昨日活动正确显示前一天",
    ]),
    ("1.1.0", "2026-07-22", [
        "飞书 / 微信推送卡片重排",
        "训练计划改为显示「今天」该做的（真实当前日期）",
    ]),
    ("1.0.0", "2026-07-22", [
        "本地 Flask 仪表盘 + 每日编排器",
        "佳明 AI 健康助手（仓 B）首个版本",
    ]),
]


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


def all_health_dates() -> list:
    """仓 A 健康记录里所有有数据的日期（用于历史补分析）。"""
    try:
        recs = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
    except Exception:
        return []
    return sorted(d for d, v in recs.items() if v)


def basic_metrics(date: str) -> list:
    """无 AI 日报时，从仓 A 健康记录里取基础指标兜底展示。
    返回 [{name, value, interpret}]，与 ai.status_metrics 结构一致。"""
    rec = _rec(date)
    if not rec:
        return []
    pairs = [
        ("HRV", rec.get("hrv_avg"), "ms"),
        ("睡眠时长", (round((rec.get("sleep_seconds") or 0) / 3600, 1)
                      if rec.get("sleep_seconds") else None), "小时"),
        ("睡眠分", rec.get("sleep_score"), "分"),
        ("静息心率", rec.get("resting_hr"), "bpm"),
        ("身体电量峰值", rec.get("body_battery_high"), ""),
        ("压力", rec.get("avg_stress"), ""),
        ("训练准备度", rec.get("training_readiness_score"), ""),
        ("步数", rec.get("steps"), "步"),
    ]
    out = []
    for name, val, unit in pairs:
        if val is None:
            continue
        out.append({"name": name, "value": f"{val}{unit}", "interpret": ""})
    return out


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
    返回 [{date, weekday, hrv, sleep_h, rhr, bb, steps, color, is_today,
           has_ai(有AI分析), has_health(有健康数据), has_data(=has_ai, 兼容)}]"""
    import datetime as _dt
    try:
        center = _dt.date.fromisoformat(date)
    except Exception:
        return []

    half = (window - 1) // 2
    ai_days = set(all_days)          # 有 AI 分析的日期
    health_days = set(all_health_dates())  # 有健康数据的日期
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

        # 颜色：基于该日 AI 报告的 overall_status / status_color；无 AI 则用灰蓝
        color = "#86909c"
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

        has_ai = d in ai_days
        has_health = (d in health_days) or bool(rec)
        strip.append({
            "date": d,
            "has_ai": has_ai,
            "has_health": has_health,
            "has_data": has_ai,  # 兼容旧模板引用
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
    has_health = bool(_rec(date)) or (date in set(all_health_dates()))
    basics = basic_metrics(date) if not ai else []
    return render_template(
        "day.html",
        date=date,
        activity_date=activity_date,
        ai=ai,
        snap=snap,
        trend=trend,
        all_days=all_days,
        week_strip=week_strip,
        has_health=has_health,
        basic_metrics=basics,
        color_map=COLOR_MAP,
        status_emoji=STATUS_EMOJI,
    )


@app.route("/need-data")
def need_data():
    """无数据时的引导页：根据登录态给登录 / 等待同步 / 立即分析。"""
    try:
        from agent.garmin_login import garmin_status
        s = garmin_status()
    except Exception:
        s = {"logged_in": False}
    return render_template(
        "need_data.html",
        logged_in=bool(s.get("logged_in")),
        email=(s.get("email") or ""),
    )


@app.route("/api/has-data")
def api_has_data():
    """供 need-data 页轮询：是否已产生可展示的数据。"""
    return jsonify({"ok": True, "has": bool(available_days())})


@app.route("/api/analyze/today", methods=["POST"])
def api_analyze_today():
    """为今天补生成 AI 分析（need-data 页「立即分析今日」用）。"""
    try:
        from ai.ai_analyze import analyze
        d = datetime.date.today().isoformat()
        result = analyze(d, force=True, plan_base_date=d)
        return jsonify({"ok": True, "engine": result.get("engine")})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ---------------------------------------------------------------- 历史 AI 分析（后台任务 + 进度）
_history_job = {"running": False, "total": 0, "done": 0, "last": "", "errors": 0}
_history_lock = threading.Lock()


def _run_history(dates: list):
    """逐日补生成 AI 分析（后台线程）。"""
    global _history_job
    try:
        from ai.ai_analyze import analyze
    except Exception as e:
        with _history_lock:
            _history_job["running"] = False
            _history_job["errors"] = len(dates)
        return
    for d in dates:
        try:
            # 历史日：计划基准日=目标日，避免把今天的训练错当历史日计划；已存在也重算拿真实 AI
            analyze(d, force=True, plan_base_date=d)
        except Exception:
            with _history_lock:
                _history_job["errors"] += 1
        with _history_lock:
            _history_job["done"] += 1
            _history_job["last"] = d
    with _history_lock:
        _history_job["running"] = False


@app.route("/api/analyze/<date>", methods=["POST"])
def api_analyze_date(date: str):
    """为指定日期补生成（或重算）AI 分析。"""
    try:
        from ai.ai_analyze import analyze
        result = analyze(date, force=True, plan_base_date=date)
        return jsonify({"ok": True, "date": date, "engine": result.get("engine"), "message": "已生成"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/analyze-history", methods=["POST"])
def api_analyze_history():
    """批量补分析所有「有健康数据但无 AI 分析」的日期（后台线程执行）。"""
    global _history_job
    with _history_lock:
        if _history_job["running"]:
            return jsonify({"ok": False, "busy": True, "message": "历史分析正在进行中"})
    # 有健康数据 且 尚无 AI 日报的日期
    dates = [d for d in all_health_dates() if not load_ai(d)]
    with _history_lock:
        _history_job = {"running": True, "total": len(dates), "done": 0, "last": "", "errors": 0}
    t = threading.Thread(target=_run_history, args=(dates,), daemon=True)
    t.start()
    return jsonify({"ok": True, "total": len(dates)})


@app.route("/api/analyze-status")
def api_analyze_status():
    """轮询历史分析进度。"""
    with _history_lock:
        return jsonify(dict(_history_job))


@app.route("/api/about")
def api_about():
    """返回应用元信息：版本、功能介绍、更新日志（供顶部弹窗读取）。"""
    return jsonify({
        "version": APP_VERSION,
        "features": [{"title": t, "desc": d} for t, d in APP_FEATURES],
        "changelog": [{"version": v, "date": dt, "items": items}
                      for v, dt, items in APP_CHANGELOG],
    })


if __name__ == "__main__":
    host = os.getenv("DASH_HOST", "127.0.0.1")
    port = int(os.getenv("DASH_PORT", "8000"))
    print(f"🌐 仪表盘已启动： http://{host}:{port}")
    app.run(host=host, port=port, debug=False)
