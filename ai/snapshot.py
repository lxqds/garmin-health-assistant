#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/snapshot.py —— 把 Garmin 原始健康记录归一化为「每日快照」并计算滚动基线

职责：
  - 读 $GARMIN_DATA_DIR/health_records.json（仓 A garmin_sync.py 产出，即「数据契约」）
  - 读 $GARMIN_DATA_DIR/activities.json（每日活动摘要，由仓 A 产出；可选）
  - 归一化字段命名，方便 AI 与仪表盘统一消费
  - 计算近 N 日滚动基线（HRV / 静息心率 / 睡眠时长 / 身体电量峰值），用于上下文解读
  - 写出 $ASSISTANT_DATA_DIR/daily_snapshot_<date>.json

本文件不依赖网络与任何密钥，纯本地计算，可单独测试。
仓 B 不持有佳明令牌：输入来自 GARMIN_DATA_DIR，输出写入 ASSISTANT_DATA_DIR。
"""
from __future__ import annotations

import os
import json
import datetime
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent.parent
# 读取：仓 A 产出的数据契约（默认取同级 ../Garmin_auto_sync/garmin-data）
INPUT_DIR = Path(os.getenv("GARMIN_DATA_DIR", str(BASE.parent / "Garmin_auto_sync" / "garmin-data")))
# 写入：本仓自身分析产出（默认 ./assistant-data）
OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
HEALTH_JSON = INPUT_DIR / "health_records.json"
ACTIVITIES_JSON = INPUT_DIR / "activities.json"
SNAPSHOT_DIR = OUTPUT_DIR / "snapshots"

# 活动类型中文映射
ACTIVITY_TYPE_CN = {
    "running": "跑步", "cycling": "骑行", "swimming": "游泳",
    "walking": "步行", "hiking": "徒步", "strength_training": "力量训练",
    "cardio": "有氧", "yoga": "瑜伽", "elliptical": "椭圆机",
    "indoor_cycling": "室内骑行", "training": "训练", "other": "其他",
}


def _sec_to_min(sec) -> Optional[float]:
    if sec is None:
        return None
    try:
        return round(sec / 60.0, 1)
    except Exception:
        return None


def _num(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def load_health_records(data_dir: Path = INPUT_DIR) -> dict:
    p = data_dir / "health_records.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_activities(data_dir: Path = INPUT_DIR) -> dict:
    p = data_dir / "activities.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def compute_baseline(records: dict, target_date: str, window: int = 30) -> dict:
    """近 window 天（不含 target_date 当天）的滚动均值基线。"""
    t = datetime.date.fromisoformat(target_date)
    vals = {"hrv": [], "resting_hr": [], "sleep_min": [], "body_battery_max": [], "training_readiness": []}
    for i in range(1, window + 1):
        d = (t - datetime.timedelta(days=i)).isoformat()
        r = records.get(d)
        if not r:
            continue
        if _num(r.get("hrv_avg")) is not None:
            vals["hrv"].append(_num(r["hrv_avg"]))
        if _num(r.get("resting_hr")) is not None:
            vals["resting_hr"].append(_num(r["resting_hr"]))
        if _num(r.get("sleep_seconds")) is not None:
            vals["sleep_min"].append(_sec_to_min(r["sleep_seconds"]))
        if _num(r.get("body_battery_high")) is not None:
            vals["body_battery_max"].append(_num(r["body_battery_high"]))
        if _num(r.get("training_readiness_score")) is not None:
            vals["training_readiness"].append(_num(r["training_readiness_score"]))
    baseline = {}
    for k, v in vals.items():
        baseline[k] = round(sum(v) / len(v), 1) if v else None
        baseline[f"{k}_n"] = len(v)
    return baseline


def build_snapshot(target_date: str, data_dir: Path = INPUT_DIR, window: int = 30) -> dict:
    """构造单日归一化快照。"""
    records = load_health_records(data_dir)
    activities_all = load_activities(data_dir)
    rec = records.get(target_date) or {}

    sleep_min = _sec_to_min(rec.get("sleep_seconds"))

    # 活动明细：仓 A 的 activities.json 键为带时间戳的完整字符串
    #（如 "2026-07-22 20:33:24"），需按纯日期前缀匹配
    raw_acts = []
    for k, v in activities_all.items():
        if k.startswith(target_date):
            if isinstance(v, list):
                raw_acts.extend(v)
            else:
                raw_acts.append(v)
    activities = []
    for a in raw_acts:
        activities.append({
            "type": ACTIVITY_TYPE_CN.get((a.get("type") or "").lower(), a.get("type") or "运动"),
            "duration_min": a.get("duration_min") if a.get("duration_min") is not None else _sec_to_min(a.get("duration_sec")),
            "distance_km": _num(a.get("distance_km")),
            "avg_hr": _num(a.get("avg_hr")),
            "calories": _num(a.get("calories")),
            "training_effect": a.get("training_effect"),
        })

    metrics = {
        "hrv": {"value": _num(rec.get("hrv_avg")), "unit": "ms", "raw": "hrv_avg"},
        "sleep_score": {"value": _num(rec.get("sleep_score")), "unit": "分", "raw": "sleep_score"},
        "sleep_duration_h": {"value": round(sleep_min / 60, 1) if sleep_min is not None else None, "unit": "小时", "raw": "sleep_seconds"},
        "body_battery_max": {"value": _num(rec.get("body_battery_high")), "unit": "", "raw": "body_battery_high"},
        "body_battery_min": {"value": _num(rec.get("body_battery_low")), "unit": "", "raw": "body_battery_low"},
        "resting_hr": {"value": _num(rec.get("resting_hr")), "unit": "bpm", "raw": "resting_hr"},
        "stress": {"value": _num(rec.get("stress_level")), "unit": "", "raw": "stress_level"},
        "training_readiness": {"value": _num(rec.get("training_readiness_score")), "unit": "分", "raw": "training_readiness_score"},
        "training_status": {"value": rec.get("training_status"), "unit": "", "raw": "training_status"},
        "training_load": {"value": _num(rec.get("training_load")), "unit": "", "raw": "training_load"},
        "steps": {"value": _num(rec.get("steps")), "unit": "步", "raw": "steps"},
    }

    baseline = compute_baseline(records, target_date, window)

    snapshot = {
        "date": target_date,
        "generated_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "metrics": metrics,
        "activities": activities,
        "baseline": baseline,
        "source": "garmin_sync.py" if rec else "empty",
    }
    return snapshot


def save_snapshot(snapshot: dict, data_dir: Path = OUTPUT_DIR) -> Path:
    SNAPSHOT_DIR = data_dir / "snapshots"
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    out = SNAPSHOT_DIR / f"daily_snapshot_{snapshot['date']}.json"
    out.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def build_and_save(target_date: str, data_dir: Path = OUTPUT_DIR, window: int = 30) -> dict:
    snap = build_snapshot(target_date, data_dir, window)
    save_snapshot(snap, data_dir)
    return snap


if __name__ == "__main__":
    import sys
    td = sys.argv[1] if len(sys.argv) > 1 else (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    s = build_and_save(td)
    print(json.dumps(s, ensure_ascii=False, indent=2))
