#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/coach_plan.py —— 解析仓 A(garmin_sync.py coach)产出的 coach_plan.md

Garmin Coach 自适应计划由仓 A 拉取后写成 markdown 表格：
  | 日期 | 周次 | 星期 | 类型 | 训练内容 | 目标 | 预计时长 | 预计距离 | 状态 |

本模块按日期取「今日训练安排」，转成与 ai_analyze 一致的 training_plan 结构，
使每日推送的训练计划直接来自佳明教练，而非凭状态自行编造。

本文件纯本地、无网络、无密钥。输入来自 $GARMIN_DATA_DIR（即仓 A 的 garmin-data）。
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

BASE = Path(__file__).resolve().parent.parent
INPUT_DIR = Path(os.getenv("GARMIN_DATA_DIR",
                          str(BASE.parent / "Garmin_auto_sync" / "garmin-data")))

REST_MARKERS = ["休息", "💤"]
DASHES = ("—", "-")


def _is_dash(v: str) -> bool:
    return v in DASHES or set(v) <= set("-: ")


def load_coach_plan(plan_date: str, data_dir: Path = INPUT_DIR) -> Optional[dict]:
    """读取 coach_plan.md，返回 plan_date 当天的训练安排；无文件/无该行返回 None。"""
    p = data_dir / "coach_plan.md"
    if not p.exists():
        return None
    text = p.read_text(encoding="utf-8")

    rows = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 5:
            continue
        if cells[0] == "日期" or _is_dash(cells[0]):
            continue  # 表头 / 分隔行
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", cells[0]):
            continue  # 只收日期行
        rows.append({
            "date": cells[0],
            "weekday": cells[2] if len(cells) > 2 else "",
            "type": cells[3] if len(cells) > 3 else "",
            "content": cells[4] if len(cells) > 4 else "",
            "target": cells[5] if len(cells) > 5 else "",
            "duration": cells[6] if len(cells) > 6 else "",
            "distance": cells[7] if len(cells) > 7 else "",
            "status": cells[8] if len(cells) > 8 else "",
        })

    today_rows = [r for r in rows if r["date"] == plan_date]
    if not today_rows:
        return None
    is_rest = any(any(m in r["type"] for m in REST_MARKERS) for r in today_rows)
    return {"date": plan_date, "found": True, "is_rest": is_rest, "rows": today_rows}


def _norm_duration(d: str) -> str:
    """'23分' -> '23分钟'；'—'/'' -> ''"""
    if not d or d in DASHES:
        return ""
    return re.sub(r"(\d)分$", r"\1分钟", d)


def to_training_plan(coach: dict) -> list:
    """把教练计划行转成 training_plan 列表（供规则兜底 / 卡片 / 仪表盘复用）。"""
    plan = []
    for r in coach["rows"]:
        if any(m in r["type"] for m in REST_MARKERS):
            continue
        # 类型形如 running·有氧基础·必做 -> 取 '有氧基础'
        t = re.sub(r"^(running|strength_training|cycling|cardio|swimming)\s*·\s*", "", r["type"])
        t = t.split("·")[0].strip()
        content = r["content"] or t
        dur = _norm_duration(r["duration"])
        zone = r["target"] if (r["target"] and r["target"] not in DASHES) else ""
        dist = r["distance"] if (r["distance"] and r["distance"] not in DASHES) else ""
        note_parts = []
        if dist:
            note_parts.append(f"预计{dist}")
        if "必做" in r["type"]:
            note_parts.append("必做")
        elif "选做" in r["type"]:
            note_parts.append("选做")
        plan.append({
            "type": content,
            "duration": dur,
            "zone": zone,
            "note": " · ".join(note_parts),
        })
    return plan


def coach_summary(coach: dict) -> str:
    """给 advice / 提示词用的一句概括。"""
    if coach["is_rest"]:
        return "Garmin Coach 今日安排：强制休息。"
    items = []
    for r in coach["rows"]:
        if any(m in r["type"] for m in REST_MARKERS):
            continue
        parts = [r["content"] or r["type"].split("·")[0].strip()]
        if r["duration"] and r["duration"] not in DASHES:
            parts.append(r["duration"])
        if r["distance"] and r["distance"] not in DASHES:
            parts.append(r["distance"])
        items.append(" · ".join(parts))
    return "Garmin Coach 今日计划：" + "；".join(items) + "。"
