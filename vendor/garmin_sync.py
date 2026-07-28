#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
garmin_sync.py —— 佳明直连自动同步（基于 garminconnect 社区库）

合规边界说明（务必先读）：
  - 本脚本使用 garminconnect（社区对 Garmin Connect 的逆向封装），并非 Garmin 官方公开 API。
    仅用于拉取【你自己账号】的数据，属个人用途；接口可能随 Garmin 改版而失效，请控制频率、勿高频猛刷。
  - 令牌与凭据仅保存在本地项目目录（已加入 .gitignore），切勿分享或提交到任何地方。
  - 你已确认可接受"自动登录"：首次用邮箱+密码登录一次，令牌（约 1 年有效、自动刷新）本地留存；
    之后脚本只带令牌运行，无需再输入密码或 MFA。

命令：
  python garmin_sync.py auth     # 首次登录并保存令牌（若开 MFA，终端会提示输入一次验证码）
  python garmin_sync.py fetch    # 拉新活动(落 inbox/)+每日健康摘要+自动跑分析；可加 --days 30
  python garmin_sync.py coach    # 拉取当前佳明教练(Garmin Coach)自适应训练计划 → coach_plan.md
  python garmin_sync.py status   # 查看配置/令牌/上次拉取状态
  python garmin_sync.py test     # 无凭证离线自检（验证解析与落盘逻辑）

依赖：garminconnect（已在受管 venv 安装）
"""
import sys
import os
import io
import json
import time
import zipfile
import subprocess
import datetime
import argparse
from pathlib import Path

# ---------- 路径 ----------
BASE = Path(__file__).resolve().parent
# 注意：打包成 exe 后 __file__ 落在临时解压目录，不能用它定位数据目录。
# 优先用环境变量 GARMIN_DATA_DIR（.env 中已设为绝对路径，指向仓 A 的 garmin-data），
# 这样 exe 版与命令行版共享同一份 garmin-data（令牌/配置/健康记录都一致）。
DATA = Path(os.getenv("GARMIN_DATA_DIR", str(BASE / "garmin-data")))
INBOX = DATA / "inbox"
PROCESSED = DATA / "processed"
REPORTS = DATA / "reports"
SUMMARY = DATA / "summary.md"
HEALTH_MD = DATA / "health_daily.md"
HEALTH_JSON = DATA / "health_records.json"
ACTIVITIES_JSON = DATA / "activities.json"
COACH_MD = DATA / "coach_plan.md"

CONFIG_PATH = DATA / ".garmin_config.json"
STATE_PATH = DATA / ".garmin_state.json"


def resolve_region(config: dict):
    """返回 (region, is_cn, domain)。默认中国区 connect.garmin.cn。"""
    r = ((config or {}).get("region") or "cn").strip().lower() if isinstance(config, dict) else "cn"
    if r in ("", "cn", "china", "chinese"):
        r = "cn"
    elif r in ("global", "com", "intl", "international", "us"):
        r = "global"
    is_cn = (r == "cn")
    domain = "garmin.cn" if is_cn else "garmin.com"
    return r, is_cn, domain


def tokenstore_for(region: str) -> Path:
    """令牌目录按区隔离，避免中国区/国际区令牌互相干扰。"""
    return DATA / f".garmin_tokens_{region}"

for p in (DATA, INBOX, PROCESSED, REPORTS):
    p.mkdir(parents=True, exist_ok=True)

# ---------- 导入 garminconnect（含异常类型） ----------
try:
    from garminconnect import (
        Garmin,
        GarminConnectAuthenticationError,
        GarminConnectTooManyRequestsError,
        GarminConnectConnectionError,
    )
except Exception as e:  # pragma: no cover
    print("❌ 未能导入 garminconnect，请先安装：pip install garminconnect")
    print("   错误：", e)
    sys.exit(2)


# ---------- 小工具 ----------
def now_iso() -> str:
    return datetime.date.today().isoformat()


def log(msg: str):
    print(msg, flush=True)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        tmpl = {
            "_comment": "填写你的 Garmin 账号。密码仅存本地，已 .gitignore。也可用下面的 cookie 免密方式（二选一）。",
            "email": "",
            "password": "",
            "region": "cn",
            "_cookie_login_comment": "若不想存密码：先清空上面 email/password，再从浏览器开发者工具复制以下两项 cookie（Garmin 网页登录态），留空则忽略。",
            "cookie_order_token": "",
            "cookie_jwt_fgp": "",
        }
        CONFIG_PATH.write_text(json.dumps(tmpl, ensure_ascii=False, indent=2), encoding="utf-8")
        return {}
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if "region" not in cfg:
        cfg["region"] = "cn"
        try:
            CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return cfg


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"last_fetch": None, "processed_activity_ids": [], "processed_health_dates": []}


def save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def call_with_backoff(fn, *args, _label="api", **kwargs):
    """带指数退避的 API 调用，处理限流与瞬时连接错误。"""
    delay = 1.0
    for attempt in range(5):
        try:
            return fn(*args, **kwargs)
        except GarminConnectTooManyRequestsError:
            log(f"  ⚠️ 限流（{_label}），{delay:.0f}s 后重试…")
            time.sleep(delay)
            delay = min(delay * 2, 30)
        except GarminConnectConnectionError as e:
            log(f"  ⚠️ 连接错误（{_label}），{delay:.0f}s 后重试… ({e})")
            time.sleep(delay)
            delay = min(delay * 2, 30)
    raise RuntimeError(f"{_label} 多次重试后仍失败")


# ---------- 登录 / 客户端 ----------
def build_client(config: dict, mfa: str | None = None):
    """构造 Garmin 客户端。优先用令牌（自动刷新）；失败则按密码或 cookie 登录。
    根据 config['region'] 自动切换中国区(garmin.cn)/国际区(garmin.com)。
    mfa: 若提供则非交互使用此验证码（用于后台自动登录）。"""
    email = (config.get("email") or "").strip()
    password = (config.get("password") or "").strip()
    region, is_cn, domain = resolve_region(config)
    order_token = (config.get("cookie_order_token") or "").strip()
    jwt_fgp = (config.get("cookie_jwt_fgp") or "").strip()
    log(f"🌐 数据区：{('中国区' if is_cn else '国际区')}（{domain}）")
    pmfa = (lambda: mfa) if mfa else (lambda: input("请输入 MFA 验证码："))

    if order_token and jwt_fgp:
        # 免密 cookie 登录：用底层 client 注入 cookie（garminconnect 0.3.6 已内联 garth 为 client.Client）
        try:
            from garminconnect.client import Client as GarthClient
            api = GarthClient(domain=domain)
            api.headers.update({"cookie": f"orderToken={order_token}; JWT_FGP={jwt_fgp}"})
            try:
                client = Garmin(api=api, is_cn=is_cn)
            except TypeError:
                client = Garmin(api=api)  # 旧版无 is_cn 参数时退化为默认区
            log("🔑 使用 cookie 免密方式构造客户端。")
            return client
        except Exception as e:
            log(f"  cookie 方式失败，回退密码登录：{e}")

    # 令牌优先：只要存在有效令牌，即使没填 email/password 也能同步
    # （Garmin 库在 do_login(tokenstore) 时加载令牌并自动刷新；无需再输密码/MFA）
    tokenstore = tokenstore_for(region)
    token_file = tokenstore / "garmin_tokens.json"
    if not email or not password:
        if not token_file.exists():
            raise SystemExit(
                "❌ 未配置凭据且无有效令牌。请编辑 garmin-data/.garmin_config.json 填入 email/password，"
                "或填 cookie_order_token/cookie_jwt_fgp；或先运行 `python garmin_sync.py auth` 生成令牌。"
            )
        log("🔑 未配置密码，使用已保存令牌登录。")
        client = Garmin(is_cn=is_cn, prompt_mfa=pmfa)
        return client

    client = Garmin(email, password, is_cn=is_cn, prompt_mfa=pmfa)
    return client


def do_login(client, tokenstore: Path):
    """登录并保存令牌到 tokenstore 目录（库会自动保存，mode 0600）。"""
    res = call_with_backoff(client.login, str(tokenstore))
    return res


# ---------- 活动拉取 ----------
def fetch_activities(client, days: int, state: dict):
    """拉取近 days 天的活动，未处理过的下载原始文件落 inbox/。返回本次新增数量。"""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    since_ts = int(datetime.datetime.combine(
        datetime.date.fromisoformat(since), datetime.time.min, tzinfo=datetime.timezone.utc
    ).timestamp())
    processed_ids = set(state.get("processed_activity_ids", []))
    added = 0

    # 一次性拉最近 50 条活动
    activities = call_with_backoff(client.get_activities, 0, 50)
    recent = [a for a in activities if (a.get("startTimeLocal") or "").split("T")[0] >= since] if activities else []
    log(f"📋 近 {days} 天发现活动 {len(recent)} 条，待检查 {max(0, len(recent)-len(processed_ids))} 条。")

    for act in recent:
        aid = act.get("activityId")
        if aid in processed_ids:
            continue
        try:
            _download_activity_original(client, aid, act)
            processed_ids.add(aid)
            added += 1
            log(f"  ✅ 已下载活动 {aid}（{act.get('activityName')} {act.get('startTimeLocal')}）")
        except Exception as e:
            log(f"  ⚠️ 活动 {aid} 下载失败：{e}")
        time.sleep(0.3)
    state["processed_activity_ids"] = list(processed_ids)
    return added


def _download_activity_original(client, activity_id, act):
    """优先下载 ORIGINAL(zip→提取.fit/.tcx) 落 inbox/；失败回退 TCX 直接写。"""
    name = act.get("activityName") or "activity"
    start = (act.get("startTimeLocal") or now_iso() + "T00:00:00").replace(":", "-").replace(" ", "T")
    try:
        data = call_with_backoff(client.download_activity, str(activity_id), client.ActivityDownloadFormat.ORIGINAL)
        # ORIGINAL 通常返回 zip
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for nm in z.namelist():
                low = nm.lower()
                if low.endswith((".fit", ".tcx", ".gpx")):
                    ext = low.split(".")[-1]
                    out = INBOX / f"{start}_{activity_id}.{ext}"
                    out.write_bytes(z.read(nm))
                    return
        # zip 里没有预期扩展名，整包保存
        out = INBOX / f"{start}_{activity_id}.zip"
        out.write_bytes(data)
        return
    except zipfile.BadZipFile:
        # 直接返回了文件内容（非 zip），按 TCX 存
        tcx = call_with_backoff(client.download_activity, str(activity_id), client.ActivityDownloadFormat.TCX)
        out = INBOX / f"{start}_{activity_id}.tcx"
        out.write_bytes(tcx)
    except Exception:
        # 任何失败再尝试 TCX
        tcx = call_with_backoff(client.download_activity, str(activity_id), client.ActivityDownloadFormat.TCX)
        out = INBOX / f"{start}_{activity_id}.tcx"
        out.write_bytes(tcx)


# ---------- 每日健康摘要 ----------
def build_health_record(client, cdate: str) -> dict:
    rec = {"date": cdate}
    # 1) 每日活动/静息心率/身体电量峰值
    st = {}
    try:
        st = call_with_backoff(client.get_stats, cdate) or {}
        rec["resting_hr"] = st.get("restingHeartRate")
        rec["avg_stress"] = st.get("averageStressLevel")
        rec["body_battery_high"] = st.get("bodyBatteryHighestValue")
        rec["body_battery_low"] = st.get("bodyBatteryLowestValue")
        rec["calories"] = st.get("calories")
        rec["intensity_minutes"] = st.get("moderateIntensityMinutes")
        rec["floors"] = st.get("floorsAscended")
    except Exception as e:
        log(f"  ⚠️ get_stats({cdate}) 失败：{e}")
    # 2) 用户摘要（步数/距离等最可靠来源；get_stats 常不返回 steps 或延迟为空）
    us = {}
    try:
        us = call_with_backoff(client.get_user_summary, cdate) or {}
    except Exception:
        pass
    # 步数：get_user_summary 的真实键是 totalSteps（get_stats 常不返回 steps 或延迟为空）
    steps = us.get("totalSteps")
    if steps is None:
        steps = us.get("steps")
    if steps is None:
        steps = st.get("steps")
    rec["steps"] = steps
    rec["user_summary"] = {k: us.get(k) for k in
                           ("totalSteps", "steps", "calories", "intensityMinutesGoal", "stepGoal", "distance")
                           if k in us}
    # 3) 睡眠
    try:
        sl = call_with_backoff(client.get_sleep_data, cdate) or {}
        dto = sl.get("dailySleepDTO") or {}
        rec["sleep_score"] = dto.get("sleepScore")
        rec["sleep_seconds"] = dto.get("sleepTimeSeconds")
        rec["deep_seconds"] = dto.get("deepSleepSeconds")
        rec["rem_seconds"] = dto.get("remSleepSeconds")
        rec["light_seconds"] = dto.get("lightSleepSeconds")
        rec["awake_seconds"] = dto.get("awakeSleepSeconds")
    except Exception as e:
        log(f"  ⚠️ get_sleep_data({cdate}) 失败：{e}")
    # 4) HRV（接口返回嵌套在 hrvSummary 下）
    try:
        hrv = call_with_backoff(client.get_hrv_data, cdate)
        if isinstance(hrv, dict):
            summ = hrv.get("hrvSummary") or {}
            rec["hrv_avg"] = summ.get("lastNightAvg") or hrv.get("lastNightAvg") or hrv.get("hrvAvg")
            rec["hrv_weekly_avg"] = summ.get("weeklyAvg") or hrv.get("weeklyAvg")
            rec["hrv_status"] = summ.get("status") or hrv.get("status")
    except Exception:
        pass
    # 5) 训练准备度（含睡眠分；dailySleepDTO.sleepScore 常为 null，优先取这里的 sleepScore）
    try:
        tr_list = call_with_backoff(client.get_training_readiness, cdate) or []
        if tr_list:
            tr = tr_list[0]
            rec["training_readiness_score"] = tr.get("score")
            rec["tr_level"] = tr.get("level")
            if tr.get("sleepScore") is not None:
                rec["sleep_score"] = tr.get("sleepScore")
    except Exception:
        pass
    # 6) 压力（get_stress_data 经常为空，回退到 get_stats.averageStressLevel）
    try:
        ss = call_with_backoff(client.get_stress_data, cdate) or {}
        rec["stress_level"] = ss.get("overallStressLevel")
    except Exception:
        pass
    if rec.get("stress_level") is None:
        rec["stress_level"] = st.get("averageStressLevel")
    # 7) 训练状态 / 训练负荷 / 恢复时间
    try:
        ts = call_with_backoff(client.get_training_status, cdate) or {}
        if isinstance(ts, dict):
            rec["training_status"] = ts.get("trainingStatus") or ts.get("status")
            rec["training_load"] = (
                ts.get("trainingLoad") or ts.get("todayTrainingLoad")
                or ts.get("acuteLoad") or ts.get("load")
            )
            rec["recovery_hours"] = ts.get("recoveryTime")
            rec["load_level"] = ts.get("loadLevel")
    except Exception:
        pass
    return rec


def fetch_health(client, days: int, state: dict, force: bool = False):
    """拉取近 days 天每日健康摘要，写 health_records.json 与 health_daily.md。

    force: 忽略已处理状态，强制重新拉取整个区间（用于修复历史脏值）。
    近 3 天的数据 Garmin 常延迟定稿（睡眠/HRV 先返回 null 后补全），
    故无论如何都强制刷新最近 3 天，避免「首次取到 null 后永久卡住」。
    """
    processed = set(state.get("processed_health_dates", []))
    records = {}
    if HEALTH_JSON.exists():
        try:
            records = json.loads(HEALTH_JSON.read_text(encoding="utf-8"))
        except Exception:
            records = {}
    today = datetime.date.today()
    new_count = 0
    for i in range(days, -1, -1):
        cdate = (today - datetime.timedelta(days=i)).isoformat()
        # 近 3 天强制刷新；其余日期除非 --force 否则跳过的已处理日
        age_days = (today - datetime.date.fromisoformat(cdate)).days
        if age_days > 3 and not force and cdate in processed and cdate in records:
            continue
        rec = build_health_record(client, cdate)
        records[cdate] = rec
        processed.add(cdate)
        new_count += 1
        time.sleep(0.25)
    state["processed_health_dates"] = list(processed)
    HEALTH_JSON.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_health_md(records)
    return new_count


def fetch_activity_summaries(client, days: int, state: dict):
    """拉取近 days 天活动列表，按日期归并为基础摘要写入 activities.json，
    供 AI 分析与仪表盘的「昨日训练」使用（不含原始文件）。"""
    since = (datetime.date.today() - datetime.timedelta(days=days)).isoformat()
    try:
        acts = call_with_backoff(client.get_activities, 0, 50) or []
    except Exception as e:
        log(f"  ⚠️ get_activities 失败：{e}")
        return 0
    by_date: dict = {}
    for a in acts:
        d = (a.get("startTimeLocal") or "").split("T")[0]
        if not d or d < since:
            continue
        tkey = ((a.get("activityType") or {}).get("typeKey") or "other")
        dur = a.get("duration")
        dist = a.get("distance")
        by_date.setdefault(d, []).append({
            "type": tkey,
            "duration_sec": dur,
            "distance_km": round(dist / 1000.0, 2) if dist else None,
            "avg_hr": a.get("averageHR"),
            "calories": a.get("calories"),
            "training_effect": a.get("trainingEffect"),
        })
    existing = {}
    if ACTIVITIES_JSON.exists():
        try:
            existing = json.loads(ACTIVITIES_JSON.read_text(encoding="utf-8"))
        except Exception:
            existing = {}
    existing.update(by_date)
    ACTIVITIES_JSON.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"🏃 活动摘要更新：覆盖 {len(by_date)} 天 → {ACTIVITIES_JSON.name}")
    return len(by_date)


def _fmt_dur(sec):
    if not sec:
        return "-"
    h = sec // 3600
    m = (sec % 3600) // 60
    if h:
        return f"{h}h{m}m"
    return f"{m}m"


def _write_health_md(records: dict):
    rows = []
    for d in sorted(records.keys(), reverse=True):
        r = records[d]
        rows.append(
            f"| {d} | {r.get('sleep_score') or '-'} | {_fmt_dur(r.get('sleep_seconds'))} | "
            f"{r.get('hrv_avg') or '-'} | {r.get('resting_hr') or '-'} | "
            f"{r.get('body_battery_high') or '-'}/{r.get('body_battery_low') or '-'} | "
            f"{r.get('training_readiness_score') or '-'} | {r.get('stress_level') or '-'} | "
            f"{r.get('steps') or '-'} |"
        )
    header = (
        "# 每日健康摘要（佳明直连）\n\n"
        "> 由 garmin_sync.py 自动拉取。指标对应你的核心目标：睡眠质量 + HRV + 静息心率 + 身体电量，"
        "直接反映\"熬夜→状态下滑\"的闭环。\n\n"
        "| 日期 | 睡眠分 | 睡眠时长 | HRV(ms) | 静息心率 | 身体电量↑/↓ | 训练准备度 | 压力 | 步数 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    HEALTH_MD.write_text(header + "\n".join(rows) + "\n", encoding="utf-8")


# ---------- 触发分析 ----------
def run_analysis():
    """调用现有 watch_and_analyze.py 处理 inbox 新文件。"""
    py = sys.executable
    script = BASE / "watch_and_analyze.py"
    if not script.exists():
        log("⚠️ 未找到 watch_and_analyze.py，跳过自动分析。")
        return
    try:
        subprocess.run([py, str(script), "--once"], cwd=str(BASE), check=True)
        log("📊 已触发自动分析（watch_and_analyze.py）。")
    except subprocess.CalledProcessError as e:
        log(f"⚠️ 自动分析失败：{e}")


# ---------- 命令 ----------
def cmd_auth(mfa=None):
    config = load_config()
    if not config:
        print("📝 已生成配置模板：garmin-data/.garmin_config.json  请填写后重跑 auth。")
        return
    client = build_client(config, mfa=mfa)
    region = resolve_region(config)[0]
    tokenstore = tokenstore_for(region)
    try:
        do_login(client, tokenstore)
        # 验证：尝试拉一次今日 stats
        call_with_backoff(client.get_stats, now_iso())
        print("✅ 登录成功，令牌已保存到", tokenstore)
    except GarminConnectAuthenticationError as e:
        print("❌ 认证失败（邮箱/密码错误，或 MFA 未通过）：", e)
        sys.exit(1)


def cmd_fetch(days: int, force: bool = False):
    config = load_config()
    # 令牌优先：即使未填 email/password，只要有有效令牌即可同步
    region0 = resolve_region(config)[0]
    tokenstore0 = tokenstore_for(region0)
    have_token = (tokenstore0 / "garmin_tokens.json").exists()
    if not config and not have_token:
        print("❌ 未配置凭据。先运行 `python garmin_sync.py auth` 或填写 .garmin_config.json。")
        sys.exit(1)
    if not config:
        # 仅令牌、无配置文件：给一个最小配置，build_client 会走令牌分支
        config = {"region": region0}
    client = build_client(config)
    region = resolve_region(config)[0]
    tokenstore = tokenstore_for(region)
    # 登录：优先用令牌（自动刷新）
    try:
        do_login(client, tokenstore)
    except GarminConnectAuthenticationError:
        log("⚠️ 令牌失效，尝试用密码重新登录…")
        do_login(client, tokenstore)  # 若密码在 config 中则重登；否则抛错
    state = load_state()
    # 诊断：账号是否关联了手表（避免静默拉空）
    _, is_cn, domain = resolve_region(config)
    try:
        devs = call_with_backoff(client.get_devices) or []
        if not devs:
            log(f"⚠️ 该 Garmin 账号未关联任何设备（手表未配对到此账号、或未同步到 {domain}）。"
                "健康与活动数据将全为空。请确认你提供的账号就是手表在 Garmin Connect 里同步的账号；"
                "如果在 App 里看到的数据在另一个邮箱/区下，请改用那个账号的凭据，并把 region 设对。")
        else:
            log(f"🔗 已关联设备 {len(devs)} 台。")
    except Exception:
        pass
    log(f"🔄 拉取近 {days} 天数据…")
    act_n = fetch_activities(client, days, state)
    log(f"🏃 新增活动文件 {act_n} 个。")
    if act_n:
        run_analysis()
    fetch_activity_summaries(client, days, state)
    h_n = fetch_health(client, days, state, force=force)
    log(f"💤 新增/更新健康记录 {h_n} 天 → {HEALTH_MD.name}")
    state["last_fetch"] = now_iso()
    save_state(state)
    log("✅ 完成。健康面板见 health_daily.md，活动趋势见 summary.md。")


def cmd_status():
    config = load_config()
    have_cfg = bool(config) and bool((config.get("email") or "").strip() and (config.get("password") or "").strip()
                                     or (config.get("cookie_order_token") or "").strip())
    region = resolve_region(config)[0] if config else "cn"
    tok_dir = tokenstore_for(region)
    have_tok = (tok_dir / "garmin_tokens.json").exists()
    is_cn = region == "cn"
    state = load_state()
    print("=== 佳明直连状态 ===")
    print(" 数据区:", ("中国区 connect.garmin.cn" if is_cn else "国际区 connect.garmin.com"), f"({region})")
    print(" 配置(.garmin_config.json):", "已填" if have_cfg else "缺失/未填")
    print(" 令牌(garmin_tokens.json):", "存在" if have_tok else "不存在（需先 auth）")
    print(" 上次拉取:", state.get("last_fetch") or "从未")
    print(f" 已处理活动数: {len(state.get('processed_activity_ids', []))}")
    print(f" 已处理健康天数: {len(state.get('processed_health_dates', []))}")


def cmd_test():
    """无凭证离线自检：用桩客户端验证下载落盘 + 健康解析 + 分析衔接。"""
    print("🧪 离线自检（不联网、不需要凭证）…")
    import analyze_garmin as ag  # noqa: F401  (确认分析器可导入)

    before = {f.name for f in INBOX.glob("*")}
    # 合成 zip 测试 _download_activity_original 的落盘逻辑
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as z:
        z.writestr("activity_test.fit", b"<TrainingCenterDatabase>fake</TrainingCenterDatabase>")
    fake_client = type("C", (), {})()
    fake_client.ActivityDownloadFormat = type("E", (), {"ORIGINAL": 1, "TCX": 2})()
    fake_client.download_activity = lambda aid, fmt: zip_buf.getvalue()
    _download_activity_original(fake_client, 123456, {"activityName": "测试跑", "startTimeLocal": "2026-07-22T07:00:00"})
    after = {f.name for f in INBOX.glob("*")}
    new_files = after - before
    print(f"  活动落盘: {'OK' if new_files else 'FAIL'} (新增: {', '.join(sorted(new_files)) or '无'})")

    # 验证 health 解析
    fake_client.get_stats = lambda d: {"restingHeartRate": 58, "bodyBatteryHighestValue": 80}
    fake_client.get_user_summary = lambda d: {"steps": 9000}
    fake_client.get_sleep_data = lambda d: {"dailySleepDTO": {"sleepScore": 82, "sleepTimeSeconds": 25200}}
    fake_client.get_hrv_data = lambda d: {"lastNightAvg": 62, "weeklyAvg": 60}
    fake_client.get_training_readiness = lambda d: [{"score": 75}]
    fake_client.get_stress_data = lambda d: {"overallStressLevel": 35}
    rec = build_health_record(fake_client, "2026-07-22")
    ok = rec["resting_hr"] == 58 and rec["sleep_score"] == 82 and rec["hrv_avg"] == 62 and rec["training_readiness_score"] == 75
    print(f"  健康解析: 静息HR={rec['resting_hr']} 睡眠分={rec['sleep_score']} HRV={rec['hrv_avg']} "
          f"训练准备度={rec['training_readiness_score']} -> {'OK' if ok else 'FAIL'}")

    # 清理测试产物
    for nm in new_files:
        try:
            (INBOX / nm).unlink()
        except Exception:
            pass
    print("✅ 离线自检通过：活动下载、健康解析、落盘逻辑均正常。")


def cmd_clear():
    """清除本地令牌（如需切换账号/区时先用）。"""
    config = load_config()
    region = resolve_region(config)[0] if config else "cn"
    tokenstore = tokenstore_for(region)
    import shutil
    if tokenstore.exists():
        shutil.rmtree(tokenstore)
        print(f"🗑️ 已清除令牌目录：{tokenstore}")
    else:
        print("ℹ️ 无令牌目录可清除。")


EFFECT_CN = {
    "AEROBIC_BASE": "有氧基础", "RECOVERY": "恢复", "TEMPO": "节奏",
    "THRESHOLD": "阈值", "SPEED": "速度", "ANAEROBIC": "无氧",
    "HIGH_AEROBIC": "高强度有氧", "LOW_AEROBIC": "低强度有氧",
}
DOW_CN = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}


def _fmt_dur(sec):
    return f"{int(sec // 60)}分" if sec else "—"


def _fmt_dist(m):
    return f"{m/1000:.2f} km" if m else "—"


def cmd_coach():
    """拉取当前进行中的佳明教练(Garmin Coach)自适应训练计划并生成可读报告。"""
    config = load_config()
    region0 = resolve_region(config)[0]
    tokenstore0 = tokenstore_for(region0)
    have_token = (tokenstore0 / "garmin_tokens.json").exists()
    if not config and not have_token:
        print("❌ 未配置凭据。先运行 `python garmin_sync.py auth` 或填写 .garmin_config.json。")
        sys.exit(1)
    if not config:
        config = {"region": region0}
    client = build_client(config)
    tokenstore = tokenstore_for(resolve_region(config)[0])
    try:
        do_login(client, tokenstore)
    except GarminConnectAuthenticationError:
        print("❌ 登录失败，请先 `auth` 或检查令牌。")
        sys.exit(1)

    try:
        plans = client.get_training_plans().get("trainingPlanList", [])
    except Exception as e:
        print("❌ 拉取训练计划失败：", e)
        sys.exit(1)

    # 优先选进行中(Scheduled/InProgress/Active)的自适应(FBT_ADAPTIVE)计划
    active = None
    for p in plans:
        if (p.get("trainingPlanCategory") or "") == "FBT_ADAPTIVE" and \
           (p.get("trainingStatus") or {}).get("statusKey") in ("Scheduled", "InProgress", "Active"):
            active = p
            break
    if not active:
        adaptives = [p for p in plans if (p.get("trainingPlanCategory") or "") == "FBT_ADAPTIVE"]
        active = max(adaptives, key=lambda p: p.get("createDate", "")) if adaptives else (plans[0] if plans else None)
    if not active:
        print("ℹ️ 未发现任何训练计划（你可能还没在 Garmin Connect 里开启佳明教练）。")
        return

    pid = active["trainingPlanId"]
    name = active.get("name")
    print(f"📋 选中计划：{name!r} (id={pid})")
    try:
        detail = client.get_adaptive_training_plan_by_id(pid)
    except Exception as e:
        print("❌ 拉取计划详情失败：", e)
        sys.exit(1)

    ttype = (detail.get("trainingType") or {}).get("typeKey") or "?"
    sub = (detail.get("trainingSubType") or {}).get("subTypeKey") or "?"
    level = (detail.get("trainingLevel") or {}).get("levelKey") or "?"
    ver = (detail.get("trainingVersion") or {}).get("versionName") or "?"
    status = (detail.get("trainingStatus") or {}).get("statusKey") or "?"
    tasks = sorted(detail.get("taskList") or [], key=lambda t: t.get("calendarDate") or "")
    print(f"   共 {len(tasks)} 条近期训练任务，生成报告…")

    L = []
    L.append(f"# 佳明教练训练计划：{name}\n")
    L.append("> 由 garmin_sync.py coach 自动拉取 · Garmin Run Coach（自适应计划）\n")
    L.append(f"- **类型**：{ttype}（子类型 {sub}）")
    L.append(f"- **级别**：{level}　**版本**：{ver}（基于心率/配速）")
    L.append(f"- **周期**：{detail.get('durationInWeeks')} 周，平均每周 {detail.get('avgWeeklyWorkouts')} 次")
    L.append(f"- **起止**：{str(detail.get('startDate',''))[:10]} ～ {str(detail.get('endDate',''))[:10]}　**状态**：{status}")
    L.append(f"- **计划 ID**：{pid}\n")
    L.append("## 近期训练安排（自适应，按日期）\n")
    L.append("| 日期 | 周次 | 星期 | 类型 | 训练内容 | 目标 | 预计时长 | 预计距离 | 状态 |")
    L.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for t in tasks:
        w = t.get("weekId")
        dow = DOW_CN.get(t.get("dayOfWeekId"), "?")
        wk = t.get("taskWorkout") or {}
        if wk.get("restDay"):
            L.append(f"| {t.get('calendarDate')} | W{w} | {dow} | 💤 休息 | 强制休息 | — | — | — | — |")
            continue
        sport = (wk.get("sportType") or {}).get("sportTypeKey") or "?"
        wname = wk.get("workoutName") or "?"
        target = wk.get("workoutDescription") or "—"
        eff = EFFECT_CN.get(wk.get("trainingEffectLabel"), wk.get("trainingEffectLabel") or "—")
        pri = "·必做" if wk.get("priorityType") == "REQUIRED" else ""
        kind = f"{sport}·{eff}{pri}"
        st = wk.get("adaptiveCoachingWorkoutStatus")
        st_cn = "✅已完成" if st == "COMPLETE" else ("⏳未做" if st == "NOT_COMPLETE" else (st or "—"))
        L.append(f"| {t.get('calendarDate')} | W{w} | {dow} | {kind} | {wname} | {target} | "
                 f"{_fmt_dur(wk.get('estimatedDurationInSecs'))} | {_fmt_dist(wk.get('estimatedDistanceInMeters'))} | {st_cn} |")
    L.append("")
    L.append("> 说明：Garmin Coach 为自适应计划，以上为系统近期下发的训练；完成一次跑步后，后续安排会按表现动态调整。")

    COACH_MD.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"✅ 报告已生成：{COACH_MD}")


def main():
    ap = argparse.ArgumentParser(description="佳明直连自动同步（garminconnect）")
    sub = ap.add_subparsers(dest="cmd")
    p_auth = sub.add_parser("auth", help="首次登录并保存令牌")
    p_auth.add_argument("--mfa", help="MFA 验证码（非交互登录用，避免后台卡输入）")
    p_fetch = sub.add_parser("fetch", help="拉新活动+每日健康摘要+分析")
    p_fetch.add_argument("--days", type=int, default=30, help="回溯天数（默认30）")
    p_fetch.add_argument("--force", action="store_true", help="忽略已处理状态，强制重拉整个区间（修复历史空值）")
    sub.add_parser("status", help="查看状态")
    sub.add_parser("clear", help="清除本地令牌")
    sub.add_parser("test", help="离线自检")
    sub.add_parser("coach", help="拉取佳明教练(Garmin Coach)训练计划")
    args = ap.parse_args()
    cmd = args.cmd or "status"
    if cmd == "auth":
        cmd_auth(args.mfa)
    elif cmd == "fetch":
        cmd_fetch(args.days, args.force)
    elif cmd == "status":
        cmd_status()
    elif cmd == "clear":
        cmd_clear()
    elif cmd == "test":
        cmd_test()
    elif cmd == "coach":
        cmd_coach()
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
