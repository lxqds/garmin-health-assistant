#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai/ai_analyze.py —— 调用 DeepSeek 生成每日健康建议与训练计划

流程：
  1. 由 ai/snapshot.py 取昨日归一化快照（含基线）
  2. 若配置了 DEEPSEEK_API_KEY → 调 DeepSeek（JSON 模式）生成结构化建议
     否则 / 调用失败 → 用本地规则兜底（rule_based），保证离线也能出结果
  3. 校验并规整输出结构
  4. 写出 $ASSISTANT_DATA_DIR/ai_daily_<date>.json 与人读 ai_daily_<date>.md

注：本仓不持有佳明令牌。输入数据来自仓 A 的 GARMIN_DATA_DIR（经 ai/snapshot 读取），
    分析产出写入本仓的 ASSISTANT_DATA_DIR。

用法：
  python ai/ai_analyze.py                 # 分析昨天
  python ai/ai_analyze.py --date 2026-07-22
  python ai/ai_analyze.py --force         # 已存在也重新生成
  python ai/ai_analyze.py --no-ai         # 强制走规则兜底（不联网）
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime
from pathlib import Path
from typing import Optional

# 允许以 `python ai/ai_analyze.py` 或仓库根 `python -m ai.ai_analyze` 运行
BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
load_dotenv(BASE / ".env")

from ai.snapshot import build_snapshot, save_snapshot
from ai import prompts

# 写入：本仓分析产出（默认 ./assistant-data）
OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
AI_OUT_JSON = OUTPUT_DIR / "ai_daily_{date}.json"
AI_OUT_MD = OUTPUT_DIR / "ai_daily_{date}.md"

VALID_STATUS = {"恢复良好", "状态平稳", "需谨慎", "建议休息"}
STATUS_COLOR = {"恢复良好": "green", "状态平稳": "blue", "需谨慎": "yellow", "建议休息": "red"}


# ---------------------------------------------------------------- 规则兜底
def _ratio(v, base):
    if v is None or not base:
        return None
    try:
        return v / base
    except Exception:
        return None


def rule_based(snapshot: dict) -> dict:
    m = snapshot.get("metrics", {})
    bl = snapshot.get("baseline", {})
    hrv = m.get("hrv", {}).get("value")
    base_hrv = bl.get("hrv")
    tr = m.get("training_readiness", {}).get("value")
    bb_max = m.get("body_battery_max", {}).get("value")
    sleep = m.get("sleep_duration_min", {}).get("value")
    sleep_score = m.get("sleep_score", {}).get("value")
    stress = m.get("stress", {}).get("value")
    activities = snapshot.get("activities", [])

    # 恢复评分：优先用训练准备度（本身即 0-100 恢复估计），否则由 HRV 比值推算
    if tr is not None:
        score = float(tr)
    else:
        r = _ratio(hrv, base_hrv)
        score = 50 + (r - 1) * 100 if r else 50
    if bb_max is not None and bb_max < 25:
        score -= 15
    if sleep_score is not None and sleep_score < 60:
        score -= 10
    if sleep is not None and sleep < 360:
        score -= 10
    if stress is not None and stress > 60:
        score -= 8
    score = max(0, min(100, score))

    if score >= 75:
        status = "恢复良好"
    elif score >= 55:
        status = "状态平稳"
    elif score >= 35:
        status = "需谨慎"
    else:
        status = "建议休息"
    color = STATUS_COLOR[status]

    # 指标解读
    metrics_out = []
    if hrv is not None:
        r = _ratio(hrv, base_hrv)
        if r and base_hrv:
            interp = f"高于近30日基线{base_hrv}ms（×{r:.2f}）" if r >= 1.05 else (
                f"低于近30日基线{base_hrv}ms（×{r:.2f}）" if r < 0.95 else f"接近基线{base_hrv}ms")
        else:
            interp = "无基线可对比"
        metrics_out.append({"name": "HRV", "value": f"{hrv} ms", "interpret": interp})
    for key, label in (("sleep_score", "睡眠分"), ("sleep_duration_min", "睡眠时长"),
                        ("body_battery_max", "身体电量峰值"), ("resting_hr", "静息心率"),
                        ("stress", "压力"), ("training_readiness", "训练准备度")):
        mv = m.get(key, {}).get("value")
        if mv is not None:
            unit = m.get(key, {}).get("unit") or ""
            metrics_out.append({"name": label, "value": f"{mv}{unit}", "interpret": ""})
    ts = m.get("training_status", {}).get("value")
    if ts:
        metrics_out.append({"name": "训练状态", "value": str(ts), "interpret": ""})

    # 建议 + 训练计划
    advice, plan = _plan_for(status, snapshot, score)
    highlights = _highlights(snapshot, status)

    return {
        "overall_status": status,
        "status_color": color,
        "metrics": metrics_out,
        "advice": advice,
        "training_plan": plan,
        "highlights": highlights,
        "engine": "rules",
    }


def _plan_for(status: str, snapshot: dict, score: float):
    m = snapshot.get("metrics", {})
    loadv = m.get("training_load", {}).get("value")
    activities = snapshot.get("activities", [])
    had_hard = any((a.get("avg_hr") or 0) >= 150 for a in activities)

    if status == "恢复良好":
        if loadv and loadv > 450:
            advice = ("今天整体恢复良好，但近期训练负荷偏高，适合安排一次「中等有氧 + 核心/力量」"
                      "巩固体能，不强行冲高强度，给身体留出适应空间。")
            plan = [
                {"type": "有氧跑", "duration": "50分钟", "zone": "Z2（心率130-145）", "note": "保持可对话强度"},
                {"type": "核心/力量", "duration": "20分钟", "note": "平板、深蹲、臀桥，强化跑姿稳定"},
            ]
        else:
            advice = ("恢复良好，是安排高质量训练的好日子。可上一次阈值或间歇，把身体推向新刺激；"
                      "练前动态热身 10 分钟，练后拉伸 + 蛋白质补充。")
            plan = [
                {"type": "间歇跑", "duration": "总50分钟", "zone": "4×4′ 或 8×1′（心率168-182）", "note": "组间慢跑恢复，控制配速"},
                {"type": "动态热身+拉伸", "duration": "15分钟", "note": "确保关节活动开"},
            ]
    elif status == "状态平稳":
        advice = ("状态平稳，按原计划推进中等强度训练即可。保持节奏、稳定积累，是长期进步的关键。"
                  "注意补水和睡眠，别在平稳日偷偷加码。")
        plan = [
            {"type": "中等有氧", "duration": "45分钟", "zone": "Z2-Z3（心率140-160）", "note": "舒适可说话偏喘"},
            {"type": "力量训练", "duration": "25分钟", "note": "上下肢均衡，2-3 组"},
        ]
    elif status == "需谨慎":
        advice = ("今天指标偏弱（HRV/身体电量或睡眠未完全恢复），建议以轻松有氧和恢复为主，"
                  "避免高强度与力量大重量。明天再看数据决定是否加码。")
        plan = [
            {"type": "恢复跑/快走", "duration": "30分钟", "zone": "Z1-Z2（心率120-135）", "note": "极轻松，促进血流恢复"},
            {"type": "拉伸/泡沫轴", "duration": "15分钟", "note": "放松下肢，改善睡眠"},
        ]
    else:  # 建议休息
        advice = ("身体尚未恢复（训练准备度低 / 身体电量不足 / 睡眠欠佳），今天建议休息或极低强度活动。"
                  "硬练反而累积疲劳、增加受伤风险。保证 7.5h+ 睡眠，多喝水。")
        plan = [
            {"type": "完全休息/散步", "duration": "20分钟", "zone": "Z1 以下", "note": "仅活动筋骨，不追求强度"},
            {"type": "睡眠优先", "duration": "—", "note": "今晚争取 22:30 前睡，7.5h+"},
        ]
    return advice, plan


def _highlights(snapshot: dict, status: str):
    m = snapshot.get("metrics", {})
    bl = snapshot.get("baseline", {})
    out = []
    hrv = m.get("hrv", {}).get("value"); base_hrv = bl.get("hrv")
    if hrv and base_hrv:
        r = hrv / base_hrv
        if r >= 1.1:
            out.append(f"🌟 HRV {hrv}ms 创近30日新高（基线{base_hrv}）")
        elif r < 0.9:
            out.append(f"⚠️ HRV {hrv}ms 明显低于基线{base_hrv}，注意恢复")
    bb = m.get("body_battery_max", {}).get("value")
    if bb is not None and bb < 25:
        out.append("🔋 身体电量峰值偏低，今日不宜高强度")
    ss = m.get("sleep_score", {}).get("value")
    if ss is not None and ss < 60:
        out.append("😴 睡眠分偏低，今晚务必早睡")
    if status == "恢复良好":
        out.append("💪 恢复良好，可安排高质量训练")
    return out


# ---------------------------------------------------------------- DeepSeek
def call_deepseek(snapshot: dict) -> Optional[dict]:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        return None
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    try:
        from openai import OpenAI
    except Exception:
        return None
    client = OpenAI(api_key=api_key, base_url=base_url)
    user_msg = prompts.build_user_message(snapshot)
    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompts.SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            response_format={"type": "json_object"},
            temperature=0.6,
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        data["engine"] = "deepseek"
        return data
    except Exception as e:
        print(f"⚠️ DeepSeek 调用失败，回退规则兜底：{e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------- 规整校验
def coerce(result: dict) -> dict:
    result.setdefault("overall_status", "状态平稳")
    if result["overall_status"] not in VALID_STATUS:
        result["overall_status"] = "状态平稳"
    result["status_color"] = STATUS_COLOR.get(result["overall_status"], "blue")
    result.setdefault("metrics", [])
    result.setdefault("advice", "")
    result.setdefault("training_plan", [])
    result.setdefault("highlights", [])
    result.setdefault("engine", "unknown")
    # 确保 training_plan 每项字段齐全
    for p in result["training_plan"]:
        p.setdefault("type", "训练")
        p.setdefault("duration", "")
        p.setdefault("zone", "")
        p.setdefault("note", "")
    return result


def render_md(date: str, snap: dict, result: dict) -> str:
    color_emoji = {"green": "🟢", "blue": "🔵", "yellow": "🟡", "red": "🔴"}
    emoji = color_emoji.get(result["status_color"], "🔵")
    lines = [
        f"# 佳明健康日报 · {date}",
        "",
        f"> 总体状态：**{emoji} {result['overall_status']}**（由 {result.get('engine','?')} 生成）",
        "",
        "## 核心指标",
    ]
    for mt in result["metrics"]:
        interp = f" — {mt['interpret']}" if mt.get("interpret") else ""
        lines.append(f"- **{mt['name']}**：{mt['value']}{interp}")
    if result.get("highlights"):
        lines.extend(["", "## 亮点 / 风险"])
        for h in result["highlights"]:
            lines.append(f"- {h}")
    lines.extend(["", "## 今日建议"])
    lines.append(result["advice"])
    lines.extend(["", "## 今日训练计划"])
    if result["training_plan"]:
        for i, p in enumerate(result["training_plan"], 1):
            parts = [f"{i}. **{p['type']}**"]
            if p.get("duration"):
                parts.append(f"· {p['duration']}")
            if p.get("zone"):
                parts.append(f"· {p['zone']}")
            lines.append(" ".join(parts))
            if p.get("note"):
                lines.append(f"   - 要点：{p['note']}")
    else:
        lines.append("- 今日以休息/恢复为主。")
    # 昨日活动回顾
    acts = snap.get("activities", [])
    if acts:
        lines.extend(["", "## 昨日活动回顾"])
        for a in acts:
            parts = [f"- {a.get('type','运动')}"]
            if a.get("duration_min"):
                parts.append(f"{a['duration_min']}分钟")
            if a.get("distance_km"):
                parts.append(f"{a['distance_km']}km")
            if a.get("avg_hr"):
                parts.append(f"平均心率{a['avg_hr']}")
            lines.append(" ".join(parts))
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口
def analyze(target_date: str, force: bool = False, no_ai: bool = False) -> dict:
    json_path = Path(str(AI_OUT_JSON).format(date=target_date))
    if json_path.exists() and not force:
        print(f"ℹ️ {target_date} 的 AI 分析已存在，跳过（用 --force 重算）。")
        return json.loads(json_path.read_text(encoding="utf-8"))

    snap = build_snapshot(target_date)
    save_snapshot(snap)
    if snap.get("source") == "empty":
        print(f"⚠️ {target_date} 无健康数据（先跑 garmin_sync.py fetch）。仍生成空模板。", file=sys.stderr)

    use_ai = (not no_ai) and os.getenv("AI_FALLBACK_RULES", "true").lower() != "only"
    result = None
    if use_ai:
        result = call_deepseek(snap)
    if result is None:
        result = rule_based(snap)
    result = coerce(result)

    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path = Path(str(AI_OUT_MD).format(date=target_date))
    md_path.write_text(render_md(target_date, snap, result), encoding="utf-8")
    print(f"✅ 已生成：{json_path.name}（{result['engine']}） / {md_path.name}")
    return result


def main():
    ap = argparse.ArgumentParser(description="佳明健康 AI 分析")
    ap.add_argument("--date", help="目标日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--force", action="store_true", help="已存在也重新生成")
    ap.add_argument("--no-ai", action="store_true", help="强制走规则兜底（不联网）")
    args = ap.parse_args()
    td = args.date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    analyze(td, force=args.force, no_ai=args.no_ai)


if __name__ == "__main__":
    main()
