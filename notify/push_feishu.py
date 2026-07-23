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

# 卡片上展示的指标（按优先级，最多 6 个）
SHOWN_METRICS = ("HRV", "睡眠分", "身体电量峰值", "静息心率", "压力", "训练准备度", "训练状态")


def load_ai(date: str) -> dict:
    p = Path(str(AI_JSON).format(date=date))
    if not p.exists():
        sys.exit(f"❌ 未找到 {p.name}，请先运行：python ai/ai_analyze.py --date {date}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_metrics_columns(ai_data: dict):
    chips = []
    for m in ai_data.get("metrics", []):
        if m.get("name") in SHOWN_METRICS and m.get("value") not in (None, ""):
            chips.append((m["name"], str(m["value"])))
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
    plan = ai_data.get("training_plan") or []
    if not plan:
        return None
    src = ai_data.get("plan_source") or ""
    src_tag = f" _{src}_" if src else ""
    lines = [f"**🏃 今日训练计划{src_tag}**"]
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
    tpl_text = TEMPLATE.read_text(encoding="utf-8")
    card = json.loads(tpl_text)  # 含占位元素 metrics_placeholder / plan_placeholder / dashbtn_placeholder

    # 1) 结构占位元素替换（缺失则整段删除）
    dash_url = os.getenv("DASH_PUBLIC_URL", "")
    new_elements = []
    for el in card["elements"]:
        tag = el.get("tag")
        if tag == "metrics_placeholder":
            m = build_metrics_columns(ai_data)
            if m:
                new_elements.append(m)
        elif tag == "plan_placeholder":
            p = build_plan_div(ai_data)
            if p:
                new_elements.append(p)
        elif tag == "dashbtn_placeholder":
            b = build_dash_button(dash_url)
            if b:
                new_elements.append(b)
        else:
            new_elements.append(el)
    card["elements"] = new_elements

    # 2) 标量占位符替换（先 dump 成字符串做替换，再 parse 回 dict 校验）
    status = ai_data.get("overall_status", "状态平稳")
    emoji = STATUS_EMOJI.get(status, "🔵")
    color = COLOR_MAP.get(ai_data.get("status_color", "blue"), "blue")
    advice = ai_data.get("advice", "")
    summary = advice.split("。")[0].strip()
    if len(summary) > 48:
        summary = summary[:48] + "…"
    badge = f"{emoji} {status}"
    title = f"佳明健康日报 · {date}"

    text = json.dumps(card, ensure_ascii=False)
    text = (text.replace("{{TITLE}}", title)
                .replace("{{COLOR}}", color)
                .replace("{{BADGE}}", badge)
                .replace("{{SUMMARY}}", summary)
                .replace("{{ADVICE}}", advice))
    return json.loads(text)


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
    ap.add_argument("--date", help="目标日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印卡片 JSON，不发送")
    args = ap.parse_args()
    td = args.date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

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
