#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
notify/preview_card.py —— 把 AI 日报渲染成飞书卡片的「视觉近似」HTML，本地预览样式

不依赖飞书账号：用浏览器打开生成的 HTML 即可判断「好不好看」，再决定是否接真实 webhook。
用法：
  python notify/preview_card.py --date 2026-07-22
  python notify/preview_card.py            # 昨天
产出 $ASSISTANT_DATA_DIR/preview_card_<date>.html（已 gitignore）
"""
from __future__ import annotations

import sys
import json
import argparse
import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

sys.path.insert(0, str(BASE / "notify"))
from push_feishu import load_ai, build_status_columns, build_plan_div  # 复用卡片构建逻辑

import os
# 读取：本仓 AI 分析产出（默认 ./assistant-data）
OUTPUT_DIR = Path(os.getenv("ASSISTANT_DATA_DIR", str(BASE / "assistant-data")))
AI_JSON = OUTPUT_DIR / "ai_daily_{date}.json"

STATUS_EMOJI = {"恢复良好": "🟢", "状态平稳": "🔵", "需谨慎": "🟡", "建议休息": "🔴"}
COLOR_MAP = {"green": "#00b42a", "blue": "#1668dc", "yellow": "#ff7d00", "red": "#f53f3f"}


def safe_print(*args, **kwargs) -> None:
    """Print status text even when the Windows console cannot encode emoji."""
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        stream = kwargs.get("file") or sys.stdout
        encoding = stream.encoding or "utf-8"
        stream.write(text.encode(encoding, errors="replace").decode(encoding))
        stream.write(kwargs.get("end", "\n"))


def render_html(ai_data: dict, date: str) -> str:
    status = ai_data.get("overall_status", "状态平稳")
    color = COLOR_MAP.get(ai_data.get("status_color", "blue"), "#1668dc")
    emoji = STATUS_EMOJI.get(status, "🔵")
    advice = ai_data.get("advice", "")
    dash_url = (Path(BASE / ".env").read_text(encoding="utf-8") if (BASE / ".env").exists() else "")
    # 简单取 DASH_PUBLIC_URL（不从 dotenv 加载完整，避免引入依赖；这里直接读文本）
    import re
    m = re.search(r"DASH_PUBLIC_URL=(\\S+)", dash_url)
    dash = m.group(1) if m else ""

    # 指标列
    metrics_el = build_status_columns(ai_data)
    chips_html = ""
    if metrics_el:
        for col in metrics_el["columns"]:
            content = col["elements"][0]["text"]["content"]
            name, val = content.replace("**", "").split("\n", 1) if "\n" in content else (content, "")
            chips_html += f'<div class="chip"><div class="chip-name">{name}</div><div class="chip-val">{val}</div></div>'

    plan_el = build_plan_div(ai_data)
    plan_html = ""
    if plan_el:
        md = plan_el["text"]["content"]
        # 极简 markdown → html
        for line in md.split("\n"):
            line = line.replace("**", "")
            if line.startswith("- "):
                plan_html += f'<li>{line[2:]}</li>'
            elif line.startswith("  - "):
                plan_html += f'<div class="note">{line[4:]}</div>'
            elif line.startswith("**"):
                plan_html += f'<div class="sec">{line.strip("*")}</div>'
        plan_html = f'<ul class="plan">{plan_html}</ul>' if plan_html else ""

    btn = f'<a class="btn" href="{dash}">📊 查看完整仪表盘</a>' if dash else ""

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>飞书卡片预览 · {date}</title>
<style>
body{{background:#f2f3f5;font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;display:flex;justify-content:center;padding:24px;margin:0}}
.card{{width:420px;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 4px 16px rgba(0,0,0,.12)}}
.head{{background:{color};color:#fff;padding:16px 18px;font-size:17px;font-weight:600}}
.badge{{padding:14px 18px;font-size:20px;font-weight:700}}
.summary{{padding:0 18px 12px;color:#4e5969;font-size:14px}}
.metrics{{display:flex;flex-wrap:wrap;gap:8px;padding:0 18px 14px}}
.chip{{flex:1 1 28%;background:#f2f3f5;border-radius:8px;padding:10px;text-align:center}}
.chip-name{{font-size:12px;color:#86909c}}
.chip-val{{font-size:16px;font-weight:700;margin-top:2px}}
hr{{border:none;border-top:1px solid #f0f0f0;margin:4px 0}}
.sec{{padding:14px 18px 0;font-weight:700;font-size:15px}}
.advice{{padding:6px 18px 12px;color:#1d2129;font-size:14px;line-height:1.6}}
.plan{{padding:0 30px 12px;margin:0;font-size:14px;line-height:1.7}}
.note{{color:#86909c;font-size:12px;font-style:italic;list-style:none;margin-left:-12px}}
.btn{{display:block;margin:8px 18px 18px;background:{color};color:#fff;text-align:center;padding:11px;border-radius:8px;text-decoration:none;font-weight:600}}
.foot{{text-align:center;color:#c9cdd4;font-size:12px;padding-bottom:16px}}
</style></head><body>
<div class="card">
  <div class="head">佳明健康日报 · {date}</div>
  <div class="badge">{emoji} {status}</div>
  <div class="summary">{advice.split('。')[0]}</div>
  <div class="metrics">{chips_html}</div>
  <hr>
  <div class="sec">💡 AI 建议</div>
  <div class="advice">{advice}</div>
  {plan_html}
  {btn}
  <div class="foot">飞书交互卡片预览（视觉近似）</div>
</div>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="飞书卡片 HTML 预览")
    ap.add_argument("--date", help="目标日期（默认昨天）")
    args = ap.parse_args()
    td = args.date or (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    ai_data = load_ai(td)
    html = render_html(ai_data, td)
    out = OUTPUT_DIR / f"preview_card_{td}.html"
    out.write_text(html, encoding="utf-8")
    safe_print(f"✅ 预览已生成：{out}")


if __name__ == "__main__":
    main()
