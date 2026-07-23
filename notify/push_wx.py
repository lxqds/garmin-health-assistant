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


def _fmt_dur(minv):
    if minv is None:
        return "—"
    minv = int(round(minv))
    h, m = divmod(minv, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def build_html(ai_data: dict, date: str) -> str:
    status = ai_data.get("overall_status", "状态平稳")
    color = STATUS_COLOR.get(ai_data.get("status_color", "blue"), "#1668dc")
    emoji = STATUS_EMOJI.get(status, "🔵")
    advice = ai_data.get("advice", "")
    dash_url = os.getenv("DASH_PUBLIC_URL", "")

    SECTION = "margin:14px 0 4px;font-weight:700;font-size:15px;color:#1d2129;"
    SUB = "color:#4e5969;font-size:13px;line-height:1.7;"

    # 第 1 段：昨日活动
    steps = ai_data.get("steps")
    acts = ai_data.get("activities") or []
    act_lines = []
    if steps is not None:
        try:
            act_lines.append(f"步数：{int(steps):,} 步")
        except Exception:
            act_lines.append(f"步数：{steps} 步")
    else:
        act_lines.append("步数：未同步")
    if acts:
        for a in acts:
            parts = [f"{a.get('type','运动')}"]
            if a.get("duration_min"):
                parts.append(f"{a['duration_min']}分钟")
            if a.get("distance_km"):
                parts.append(f"{a['distance_km']}km")
            if a.get("avg_hr"):
                parts.append(f"平均心率{a['avg_hr']}")
            if a.get("calories"):
                parts.append(f"{a['calories']}kcal")
            act_lines.append(" · ".join(parts))
    else:
        act_lines.append("无运动记录（休息日）")
    activity_html = (
        f"<div style='{SECTION}'>🏃 昨日活动情况（{date}）</div>"
        f"<div style='{SUB}'>" + "<br>".join(f"• {x}" for x in act_lines) + "</div>"
    )

    # 第 2 段：昨晚睡眠
    sl = ai_data.get("sleep") or {}
    sleep_parts = []
    if sl.get("score") is not None:
        sleep_parts.append(f"睡眠分：<b>{sl['score']}分</b>")
    if sl.get("duration_h") is not None:
        sleep_parts.append(f"睡眠时长：<b>{sl['duration_h']}小时</b>")
    st = sl.get("stages") or {}
    if any(st.get(k) is not None for k in ("deep_min", "rem_min", "light_min", "awake_min")):
        sleep_parts.append(
            f"分期：深睡 {_fmt_dur(st.get('deep_min'))} · 浅睡 {_fmt_dur(st.get('light_min'))} · "
            f"REM {_fmt_dur(st.get('rem_min'))} · 清醒 {_fmt_dur(st.get('awake_min'))}"
        )
    if not sleep_parts:
        sleep_parts.append("无睡眠数据")
    sleep_html = (
        f"<div style='{SECTION}'>😴 昨晚睡眠状态</div>"
        f"<div style='{SUB}'>" + "<br>".join(f"• {x}" for x in sleep_parts) + "</div>"
    )

    # 第 3 段：今日状态
    chips = []
    for mt in (ai_data.get("status_metrics") or []):
        v = mt.get("value")
        if v in (None, ""):
            continue
        chips.append(f"{mt['name']} {v}")
        if len(chips) >= 6:
            break
    status_html = (
        f"<div style='{SECTION}'>📊 今日状态</div>"
        f"<div style='{SUB}'>" + "　".join(f"<b>{c}</b>" for c in chips) + "</div>"
    ) if chips else ""

    # 第 4 段：AI 建议（结合教练计划）
    adv = advice
    if ai_data.get("plan_source") == "Garmin Coach" and advice:
        adv = advice.rstrip() + "<br><span style='color:#86909c;font-size:12px;'>（已结合 Garmin Coach 今日计划综合评估）</span>"
    advice_html = (
        f"<div style='{SECTION}'>💡 AI 建议</div>"
        f"<div style='background:#f2f3f5;border-radius:8px;padding:10px;font-size:14px;line-height:1.6;color:#1d2129;'>{adv}</div>"
    )

    # 第 5 段：今日训练计划
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
    plan_src = ai_data.get("plan_source", "")
    src_html = f" <span style='font-weight:400;font-size:12px;color:#86909c;'>({plan_src})</span>" if plan_src else ""
    plan_html = f"<div style='{SECTION}'>🏃 今日佳明教练训练计划{src_html}</div>{plan_section}"

    dash_link = (
        f"<a href='{dash_url}' style='display:inline-block;margin-top:10px;padding:8px 14px;"
        f"background:{color};color:#fff;border-radius:8px;text-decoration:none;'>📊 查看完整仪表盘</a>"
    ) if dash_url else ""

    return f"""
<div style="font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;max-width:420px;">
  <div style="font-size:18px;font-weight:700;color:{color};">{emoji} {status}</div>
  <div style="color:#4e5969;font-size:13px;margin:2px 0 10px;">佳明健康日报 · {date}</div>
  {activity_html}
  {sleep_html}
  {status_html}
  {advice_html}
  {plan_html}
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
