#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify/push_feishu.py —— 把每日 AI 健康日报渲染成飞书交互卡片并推送

特性：
  - 读 $ASSISTANT_DATA_DIR/ai_daily_<date>.json（无则提示先跑 ai/ai_analyze.py）
  - 用 notify/templates/feishu_card.json 模板渲染（header 状态色 + 指标列 + AI 建议 + 训练计划 + 仪表盘按钮）
  - 群机器人 Webhook + HMAC-SHA256 签名校验（防伪造）
  - --dry-run：仅打印最终卡片 JSON，不发送（无需真实 webhook 即可验收样式）

用法：
  python notify/push_feishu.py --date 2026-07-22 --dry-run
  python notify/push_feishu.py                # 推送昨天（需配置 FEISHU_WEBHOOK_URL）
"""
from __future__ import annotations

import os
import sys
import json
import time
import hmac
import hashlib
import base64
import argparse
import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
load_dotenv(BASE / ".env")

# 读取：本仓 AI 分析产出（默认 ./assistant-data）
OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
AI_JSON = OUTPUT_DIR / "ai_daily_{date}.json"
TEMPLATE = BASE / "notify" / "templates" / "feishu_card.json"

STATUS_EMOJI = {"恢复良好": "🟢", "状态平稳": "🔵", "需谨慎": "🟡", "建议休息": "🔴"}
# 飞书 header template 不支持 yellow，黄色映射为 orange
COLOR_MAP = {"green": "green", "blue": "blue", "yellow": "orange", "red": "red"}

def _fmt_dur(minv):
    """分钟 -> 'XhYm' / 'Ym' / '—'"""
    if minv is None:
        return "—"
    minv = int(round(minv))
    h, m = divmod(minv, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def load_ai(date: str) -> dict:
    p = Path(str(AI_JSON).format(date=date))
    if not p.exists():
        sys.exit(f"❌ 未找到 {p.name}，请先运行：python ai/ai_analyze.py --date {date}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_activity_div(ai_data: dict):
    """第 1 段：昨日活动情况（步数 + 运动明细）"""
    # 「昨日活动」的日期 = activity_date（报告日 - 1），与睡眠/状态/计划段区分开
    date = ai_data.get("activity_date") or ai_data.get("date", "")
    steps = ai_data.get("steps")
    acts = ai_data.get("activities") or []
    lines = [f"**🏃 昨日活动情况（{date}）**"]
    if steps is not None:
        try:
            lines.append(f"- 步数：{int(steps):,} 步")
        except Exception:
            lines.append(f"- 步数：{steps} 步")
    else:
        lines.append("- 步数：未同步")
    if acts:
        for a in acts:
            parts = [f"{a.get('type', '运动')}"]
            if a.get("duration_min"):
                parts.append(f"{a['duration_min']}分钟")
            if a.get("distance_km"):
                parts.append(f"{a['distance_km']}km")
            if a.get("avg_hr"):
                parts.append(f"平均心率{a['avg_hr']}")
            if a.get("calories"):
                parts.append(f"{a['calories']}kcal")
            if a.get("training_effect"):
                parts.append(f"训练效果{a['training_effect']}")
            lines.append("- " + " · ".join(parts))
    else:
        lines.append("- 无运动记录（休息日）")
    return {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}


def build_sleep_div(ai_data: dict):
    """第 2 段：昨晚睡眠状态（评分 + 时长 + 分期）"""
    sl = ai_data.get("sleep") or {}
    score = sl.get("score")
    dur = sl.get("duration_h")
    st = sl.get("stages") or {}
    lines = ["**😴 昨晚睡眠状态**"]
    if score is not None:
        lines.append(f"- 睡眠分：**{score}分**")
    if dur is not None:
        lines.append(f"- 睡眠时长：**{dur}小时**")
    if any(st.get(k) is not None for k in ("deep_min", "rem_min", "light_min", "awake_min")):
        lines.append(
            f"- 睡眠分期：深睡 {_fmt_dur(st.get('deep_min'))} · 浅睡 {_fmt_dur(st.get('light_min'))} · "
            f"REM {_fmt_dur(st.get('rem_min'))} · 清醒 {_fmt_dur(st.get('awake_min'))}"
        )
    if len(lines) == 1:
        lines.append("- 无睡眠数据")
    return {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}


def build_status_columns(ai_data: dict):
    """第 3 段：今日状态指标（HRV / 睡眠 / 身体电量 / 心率 / 压力 …）"""
    metrics = ai_data.get("status_metrics") or []
    chips = []
    for mt in metrics:
        val = mt.get("value")
        if val in (None, ""):
            continue
        chips.append((mt["name"], str(val)))
        if len(chips) >= 6:
            break
    if not chips:
        return None
    columns = []
    for name, val in chips:
        columns.append({
            "tag": "column", "width": "weighted", "weight": 1,
            "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": f"**{name}**\n{val}"}}],
        })
    return {"tag": "column_set", "columns": columns, "flex_mode": "bisect", "background_style": "grey"}


def build_plan_div(ai_data: dict):
    """第 5 段：今日佳明教练训练计划"""
    plan = ai_data.get("training_plan") or []
    if not plan:
        return None
    src = ai_data.get("plan_source") or ""
    src_tag = f" _{src}_" if src else ""
    lines = [f"**🏃 今日佳明教练训练计划{src_tag}**"]
    for p in plan:
        head = f"- **{p.get('type', '训练')}**"
        extra = []
        if p.get("duration"):
            extra.append(p["duration"])
        if p.get("zone"):
            extra.append(p["zone"])
        if extra:
            head += " · " + " · ".join(extra)
        lines.append(head)
        if p.get("note"):
            lines.append(f"  - _{p['note']}_")
    return {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(lines)}}


def build_dash_button(url: str):
    if not url:
        return None
    return {
        "tag": "button",
        "text": {"tag": "plain_text", "content": "📊 查看完整仪表盘"},
        "type": "primary",
        "multi_url": {"url": url},
    }


def render_card(ai_data: dict, date: str) -> dict:
    card = json.loads(TEMPLATE.read_text(encoding="utf-8"))

    status = ai_data.get("overall_status", "状态平稳")
    emoji = STATUS_EMOJI.get(status, "🔵")
    color = COLOR_MAP.get(ai_data.get("status_color", "blue"), "blue")
    advice = ai_data.get("advice", "")
    # AI 建议需结合 Garmin Coach 计划评估的标注
    if (ai_data.get("plan_source") == "Garmin Coach") and advice:
        advice = advice.rstrip() + "\n\n> 本建议已结合 Garmin Coach 今日计划综合评估。"
    badge = f"{emoji} {status}"
    title = f"佳明健康日报 · {date}"
    dash_url = os.getenv("DASH_PUBLIC_URL", "")

    # 1) 结构占位元素替换（缺失则整段删除）；标量占位符直接赋值，避免 JSON 字符串转义问题
    new_elements = []
    for el in card["elements"]:
        tag = el.get("tag")
        if tag == "activity_placeholder":
            a = build_activity_div(ai_data)
            if a:
                new_elements.append(a)
        elif tag == "sleep_placeholder":
            s = build_sleep_div(ai_data)
            if s:
                new_elements.append(s)
        elif tag == "status_placeholder":
            st = build_status_columns(ai_data)
            if st:
                new_elements.append(st)
        elif tag == "plan_placeholder":
            p = build_plan_div(ai_data)
            if p:
                new_elements.append(p)
        elif tag == "dashbtn_placeholder":
            b = build_dash_button(dash_url)
            if b:
                new_elements.append(b)
        else:
            # 文本类 div 内的标量占位符直接替换（保留真实换行，不做 JSON 字符串替换）
            content = el.get("text", {}).get("content", "")
            if "{{BADGE}}" in content:
                el["text"]["content"] = content.replace("{{BADGE}}", badge)
            if "{{ADVICE}}" in content:
                el["text"]["content"] = content.replace("{{ADVICE}}", advice)
            new_elements.append(el)
    card["elements"] = new_elements

    # 2) header 标题与配色直接赋值
    card["header"]["title"]["content"] = title
    card["header"]["template"] = color
    return card


def make_signature(secret: str):
    timestamp = str(int(time.time()))
    string_to_sign = f"{timestamp}\n{secret}"
    hmac_code = hmac.new(secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = base64.b64encode(hmac_code).decode("utf-8")
    return timestamp, sign


def send_card(card: dict, webhook: str, secret: str):
    payload = {"msg_type": "interactive", "card": card}
    if secret:
        ts, sgn = make_signature(secret)
        payload["timestamp"] = ts
        payload["sign"] = sgn
    import requests
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(webhook, json=payload, timeout=10)
            if r.status_code == 200:
                body = r.json()
                if body.get("code") == 0:
                    return True, body
                return False, body
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        time.sleep(1)
    return False, last_err


def main():
    ap = argparse.ArgumentParser(description="飞书健康日报卡片推送")
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD（默认今天；活动段自动取前一天）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印卡片 JSON，不发送")
    args = ap.parse_args()
    td = args.date or datetime.date.today().isoformat()

    ai_data = load_ai(td)
    card = render_card(ai_data, td)

    if args.dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=2))
        return

    webhook = os.getenv("FEISHU_WEBHOOK_URL")
    secret = os.getenv("FEISHU_SECRET", "")
    if not webhook:
        sys.exit("❌ 未配置 FEISHU_WEBHOOK_URL（在 .env 中填写）。或加 --dry-run 预览卡片。")
    ok, resp = send_card(card, webhook, secret)
    if ok:
        print(f"✅ 飞书卡片已推送（{td}）：{resp.get('msg')}")
    else:
        print(f"❌ 飞书推送失败：{resp}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
