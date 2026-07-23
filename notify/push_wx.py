#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify/push_wx.py —— 经 WxPusher 把每日训练计划推送到个人微信

为什么用 WxPusher：微信个人号无官方富卡片推送 API，WxPusher 是免费、稳定的第三方方案，
用 appToken + 你的 uid 即可把消息推到微信（关注公众号后在「我的」复制 uid）。

内容聚焦「今日训练计划」（你最初的需求点）：状态徽章 + AI 建议摘要 + 训练计划列表 + 仪表盘链接。

接口：POST https://wxpusher.zjiecode.com/api/send/message
文档：https://wxpusher.zjiecode.com/doc

用法：
  python notify/push_wx.py --date 2026-07-22 --dry-run   # 仅打印 payload，不发送
  python notify/push_wx.py                              # 推送昨天（需配置 WXPUSHER_*）
"""
from __future__ import annotations

import os
import sys
import json
import argparse
import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
load_dotenv(BASE / ".env")

# 读取：本仓 AI 分析产出（与 push_feishu 一致）
OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
AI_JSON = OUTPUT_DIR / "ai_daily_{date}.json"

WXPUSHER_API = "https://wxpusher.zjiecode.com/api/send/message"
STATUS_COLOR = {"green": "#00b42a", "blue": "#1668dc", "yellow": "#ff7d00", "red": "#f53f3f"}
STATUS_EMOJI = {"恢复良好": "🟢", "状态平稳": "🔵", "需谨慎": "🟡", "建议休息": "🔴"}


def load_ai(date: str) -> dict:
    p = Path(str(AI_JSON).format(date=date))
    if not p.exists():
        sys.exit(f"❌ 未找到 {p.name}，请先运行：python ai/ai_analyze.py --date {date}")
    return json.loads(p.read_text(encoding="utf-8"))


def build_html(ai_data: dict, date: str) -> str:
    status = ai_data.get("overall_status", "状态平稳")
    color = STATUS_COLOR.get(ai_data.get("status_color", "blue"), "#1668dc")
    emoji = STATUS_EMOJI.get(status, "🔵")
    advice = ai_data.get("advice", "")
    dash_url = os.getenv("DASH_PUBLIC_URL", "")

    plan_items = ""
    for p in ai_data.get("training_plan", []) or []:
        head = f"<b>{p.get('type','训练')}</b>"
        extra = []
        if p.get("duration"):
            extra.append(p["duration"])
        if p.get("zone"):
            extra.append(p["zone"])
        if extra:
            head += " · " + " · ".join(extra)
        note = f"<div style='color:#86909c;font-size:13px;margin:2px 0 8px;'>{p['note']}</div>" if p.get("note") else ""
        plan_items += f"<div style='margin:6px 0;'>{head}</div>{note}"

    plan_section = plan_items or "<div style='color:#86909c;'>今日以休息/恢复为主。</div>"

    dash_link = (
        f"<a href='{dash_url}' style='display:inline-block;margin-top:10px;padding:8px 14px;"
        f"background:{color};color:#fff;border-radius:8px;text-decoration:none;'>📊 查看完整仪表盘</a>"
    ) if dash_url else ""

    return f"""
<div style="font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:420px;">
  <div style="font-size:18px;font-weight:700;color:{color};">{emoji} {status}</div>
  <div style="color:#4e5969;font-size:13px;margin:2px 0 10px;">佳明健康日报 · {date}</div>
  <div style="background:#f2f3f5;border-radius:8px;padding:10px;font-size:14px;line-height:1.6;color:#1d2129;">{advice}</div>
  <div style="font-weight:700;font-size:15px;margin:14px 0 4px;">🏃 今日训练计划</div>
  {plan_section}
  {dash_link}
</div>
"""


def build_payload(ai_data: dict, date: str) -> dict:
    status = ai_data.get("overall_status", "状态平稳")
    summary = f"今日训练计划 · {status}（{date}）"
    content = build_html(ai_data, date)
    app_token = os.getenv("WXPUSHER_APP_TOKEN", "")
    uids = [u.strip() for u in (os.getenv("WXPUSHER_UIDS", "") or "").split(",") if u.strip()]
    topic_ids = [int(t) for t in (os.getenv("WXPUSHER_TOPIC_IDS", "") or "").split(",") if t.strip()]
    dash_url = os.getenv("DASH_PUBLIC_URL", "")
    return {
        "appToken": app_token,
        "content": content,
        "summary": summary,
        "contentType": 1,  # 1=HTML
        "topicIds": topic_ids,
        "uids": uids,
        "url": dash_url,
        "verifyPay": False,
    }


def send(payload: dict):
    import requests
    last_err = None
    for attempt in range(3):
        try:
            r = requests.post(WXPUSHER_API, json=payload, timeout=10)
            if r.status_code == 200:
                body = r.json()
                if body.get("code") == 1000:  # WxPusher 成功码
                    return True, body
                return False, body
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = str(e)
        import time
        time.sleep(1)
    return False, last_err


def main():
    ap = argparse.ArgumentParser(description="WxPusher 个人微信推送（训练计划）")
    ap.add_argument("--date", help="目标日期 YYYY-MM-DD（默认昨天）")
    ap.add_argument("--dry-run", action="store_true", help="仅打印 payload，不发送")
    args = ap.parse_args()
    td = args.date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()

    ai_data = load_ai(td)
    payload = build_payload(ai_data, td)

    if args.dry_run:
        # 隐藏 token/uid 明文，避免误泄
        safe = dict(payload)
        safe["appToken"] = ("***" if safe["appToken"] else "(空)")
        safe["uids"] = [("***" if u else "(空)") for u in safe["uids"]]
        print(json.dumps(safe, ensure_ascii=False, indent=2))
        return

    if not payload["appToken"]:
        sys.exit("❌ 未配置 WXPUSHER_APP_TOKEN（在 .env 中填写）。或加 --dry-run 预览。")
    if not payload["uids"] and not payload["topicIds"]:
        sys.exit("❌ 未配置 WXPUSHER_UIDS / WXPUSHER_TOPIC_IDS（二选一）。")

    ok, resp = send(payload)
    if ok:
        print(f"✅ 已推送到微信（{td}）：{resp.get('msg')}")
    else:
        print(f"❌ 微信推送失败：{resp}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
