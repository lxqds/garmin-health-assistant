#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/prompts.py —— 给 DeepSeek 的提示词与结构化输出约定

约定：AI 必须返回严格 JSON（response_format=json_object），字段见 OUTPUT_SCHEMA。
这样卡片与仪表盘都能稳定解析复用。
"""
from __future__ import annotations

import json
from typing import Optional

SYSTEM_PROMPT = """你是一位专业的「佳明健康教练」，擅长根据可穿戴设备的客观数据（HRV、睡眠、身体电量、训练准备度、压力、训练状态/负荷、昨日活动）给出个性化、可执行的恢复与训练建议。

你的原则：
1. 以数据为准，不做无依据的鼓励或恐吓；明确引用数值与近 30 日基线对比。
2. overall_status 只能是以下四者之一：
   - "恢复良好"：适合安排高质量/高强度训练
   - "状态平稳"：适合常规中等强度训练
   - "需谨慎"：以轻松有氧/恢复为主，避免加码
   - "建议休息"：身体未恢复，建议休息或极低强度活动
   判定依据优先级：训练准备度 → HRV 相对基线 → 身体电量峰值 → 睡眠。
   status_color 对应：恢复良好=green，状态平稳=blue，需谨慎=yellow，建议休息=red。
3. advice 用中文、口语化、具体：今天该练什么、强度区间、注意事项、吃什么/睡多久。
4. training_plan 给出今天可执行的具体安排（1-3 项），含类型、时长、心率区间(Z2 等)、要点。
   若下方【Garmin Coach 今日计划】存在，training_plan 必须优先依据该计划的内容（类型/时长/目标/距离），不要自行编造训练；仅可在要点里结合恢复指标做安全提醒。若该计划为休息日，则 training_plan 给出休息安排。
5. 不编造数据；若某项缺失则跳过该指标。
6. 只输出 JSON，不要任何额外说明文字。

输出 JSON 结构（必须严格符合）：
{
  "overall_status": "恢复良好|状态平稳|需谨慎|建议休息",
  "status_color": "green|blue|yellow|red",
  "metrics": [
    {"name": "HRV", "value": "62 ms", "interpret": "高于近30日基线58，恢复不错"}
  ],
  "advice": "今天整体恢复良好……",
  "training_plan": [
    {"type": "有氧跑", "duration": "45分钟", "zone": "Z2（心率130-145）", "note": "保持可对话强度"}
  ],
  "highlights": ["亮点或风险点1", "亮点或风险点2"]
}
"""

OUTPUT_SCHEMA_HINT = "必须返回 JSON，字段：overall_status, status_color, metrics[], advice, training_plan[], highlights[]。"


def _fmt_metric(m: dict) -> str:
    val = m.get("value")
    unit = m.get("unit") or ""
    if val is None:
        return None
    return f"{val}{unit}"


def build_user_message(snapshot: dict) -> str:
    """把快照拼成给模型的用户输入。"""
    date = snapshot.get("date", "")
    metrics = snapshot.get("metrics", {})
    baseline = snapshot.get("baseline", {})
    activities = snapshot.get("activities", [])

    lines = [f"日期：{date}", "", "【今日核心指标】"]
    metric_lines = []
    for key, m in metrics.items():
        disp = _fmt_metric(m)
        if disp is None and not m.get("value"):
            # 字符串型（如 training_status）
            if m.get("value"):
                metric_lines.append(f"- {key}: {m['value']}")
            continue
        metric_lines.append(f"- {key}: {disp}")
    lines.extend(metric_lines)

    if baseline:
        bl = []
        if baseline.get("hrv"):
            bl.append(f"HRV 基线≈{baseline['hrv']}ms（n={baseline.get('hrv_n','?')}）")
        if baseline.get("resting_hr"):
            bl.append(f"静息心率基线≈{baseline['resting_hr']}bpm")
        if baseline.get("sleep_min"):
            bl.append(f"睡眠时长基线≈{round(baseline['sleep_min']/60,1)}小时")
        if baseline.get("body_battery_max"):
            bl.append(f"身体电量峰值基线≈{baseline['body_battery_max']}")
        if bl:
            lines.extend(["", "【近30日基线（用于对比）】"])
            lines.extend([f"- {x}" for x in bl])

    if activities:
        lines.extend(["", "【昨日活动】"])
        for a in activities:
            parts = [f"{a.get('type','运动')}"]
            if a.get("duration_min"):
                parts.append(f"{a['duration_min']}分钟")
            if a.get("distance_km"):
                parts.append(f"{a['distance_km']}km")
            if a.get("avg_hr"):
                parts.append(f"平均心率{a['avg_hr']}")
            lines.append(f"- {' '.join(parts)}")
    else:
        lines.extend(["", "【昨日活动】无记录"])

    coach = snapshot.get("coach_plan") or {}
    if coach.get("found"):
        lines.extend(["", "【Garmin Coach 今日计划】"])
        if coach.get("is_rest"):
            lines.append("- 今日为休息日（强制休息）")
        for r in coach["rows"]:
            if any(m in r["type"] for m in ("休息", "💤")):
                continue
            parts = [r["content"] or r["type"].split("·")[0].strip()]
            if r["duration"] and r["duration"] not in ("—", "-"):
                parts.append(r["duration"])
            if r["target"] and r["target"] not in ("—", "-"):
                parts.append(f"目标{r['target']}")
            if r["distance"] and r["distance"] not in ("—", "-"):
                parts.append(r["distance"])
            lines.append(f"- {' · '.join(parts)}")

    lines.extend(["", OUTPUT_SCHEMA_HINT])
    return "\n".join(lines)


def default_system_prompt() -> str:
    return SYSTEM_PROMPT
