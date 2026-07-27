#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_daily.py —— 佳明健康助手 每日编排器（M4）

把「分析 → 推送」串成一步，每日定时跑即可：
  报告日 = 今天（默认；--date 可指定历史某天）。
  卡片/日报内容：今日睡眠·状态·教练计划 + 昨日（报告日-1）活动回顾。
  1. ai/ai_analyze.py        分析今日 → assistant-data/ai_daily_<date>.json/.md
  2. notify/push_feishu.py   推送飞书卡片（未配置 FEISHU_WEBHOOK_URL 则自动跳过）
  3. notify/push_wx.py       推送微信（未配置 WXPUSHER_* 则自动跳过）

未配置某渠道时不会报错，只打印提示跳过，保证其他步骤继续。

用法：
  python run_daily.py                 # 分析今天（活动取昨天）+ 双推
  python run_daily.py --date 2026-07-23
  python run_daily.py --no-push       # 只分析，不推送（本地预览用）
  python run_daily.py --serve         # 分析+推送后顺起本地仪表盘（等价于再跑 python dashboard/app.py）

说明：本脚本不持有任何密钥；.env 里的 DEEPSEEK_API_KEY 为空时 ai_analyze 自动走规则兜底。
"""
from __future__ import annotations

import os
import sys
import argparse
import datetime
import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from dotenv import load_dotenv
load_dotenv(BASE / ".env")


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


def run_step(cmd: list, label: str) -> bool:
    safe_print(f"\n=== {label} ===")
    r = subprocess.run(
        [sys.executable, *cmd],
        cwd=str(BASE),
        capture_output=True,
        text=True,
    )
    out = (r.stdout or "") + (r.stderr or "")
    # 仅打印尾部，避免刷屏
    out = out.strip()
    safe_print(out[-1500:] if len(out) > 1500 else out)
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser(description="佳明健康助手 每日编排")
    ap.add_argument("--date", help="报告日期 YYYY-MM-DD（默认今天；活动段自动取前一天）")
    ap.add_argument("--no-push", action="store_true", help="只分析，不推送")
    ap.add_argument("--serve", action="store_true", help="完成后顺起本地仪表盘")
    args = ap.parse_args()

    td = args.date or datetime.date.today().isoformat()

    # 1) 分析（无 Key 时 ai_analyze 自动规则兜底）
    ok = run_step(
        [str(BASE / "ai" / "ai_analyze.py"), "--date", td, "--force"],
        f"AI 分析 {td}",
    )
    if not ok:
        safe_print("⚠️ AI 分析失败，终止。")
        return

    # 2) 推送（未配置渠道会自动跳过）
    if not args.no_push:
        run_step([str(BASE / "notify" / "push_feishu.py"), "--date", td], "飞书推送")
        run_step([str(BASE / "notify" / "push_wx.py"), "--date", td], "微信推送")
    else:
        safe_print("\n=== 跳过推送（--no-push）===")

    dash_host = os.getenv("DASH_HOST", "127.0.0.1")
    dash_port = os.getenv("DASH_PORT", "8000")
    dash_url = os.getenv("DASH_PUBLIC_URL") or f"http://{dash_host}:{dash_port}"
    safe_print(f"\n✅ 完成。仪表盘地址：{dash_url}（本地运行 `python dashboard/app.py` 启动服务）")

    if args.serve:
        import importlib.util
        spec = importlib.util.spec_from_file_location("dashapp", str(BASE / "dashboard" / "app.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        mod.app.run(host=dash_host, port=int(dash_port))


if __name__ == "__main__":
    main()
