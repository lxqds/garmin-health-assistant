"""app_paths.py —— 统一处理开发期 / PyInstaller 打包(frozen) 模式的路径

为什么需要：
  - 开发期：脚本以 .py 形式运行，__file__ 指向真实项目目录，路径好算。
  - 打包成单文件 exe 后：PyInstaller 会把脚本解压到一个临时目录(sys._MEIPASS)，
    且 sys.executable 是 exe 本身。此时：
      * 用户数据/配置应落在 exe 所在目录(EXE_DIR)，必须持久、不能进临时目录；
      * 模板/图标等只读资源由 PyInstaller 解压到 sys._MEIPASS(RES_DIR)，从那里读。

用法：各模块 `from app_paths import BASE, RES_DIR`，不要再自己用 __file__ 算 BASE。
"""
from __future__ import annotations

import os
import sys
import io
from pathlib import Path

# Windows 下双击 exe（--windowed，控制台为 GBK 编码）打印 emoji/中文会抛
# 'gbk' codec can't encode ... → 导致同步等任意 print 流程崩溃。
# 强制 stdio 用 UTF-8，编码失败时替换为 '?' 而非抛异常。
for _s in (sys.stdout, sys.stderr):
    if _s is None:
        continue
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        try:
            _buf = getattr(_s, "buffer", None)
            _new = io.TextIOWrapper(_buf, encoding="utf-8", errors="replace") if _buf else io.StringIO()
            if _s is sys.stdout:
                sys.stdout = _new
            else:
                sys.stderr = _new
        except Exception:
            pass

# PyQt6 QWebEngineView（左侧仪表盘）依赖 QtWebEngineProcess 这个 Chromium 渲染子进程。
# 在 PyInstaller 单文件打包环境下，Chromium 沙箱常常初始化失败 → 渲染进程起不来 → 页面空白。
# 关闭沙箱并强制软件渲染(GPU)，保证 WebView 在任何环境下都能正常显示。
# 必须在 import PyQt6 / 创建 QApplication 之前设置才会生效。
os.environ.setdefault(
    "QTWEBENGINE_CHROMIUM_FLAGS",
    "--no-sandbox --disable-gpu --disable-dev-shm-usage",
)

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # exe 所在目录：.env、数据、配置都放这里（持久、可整体拷贝）
    EXE_DIR = Path(sys.executable).resolve().parent
    # 资源解压目录（只读）：templates / assets
    RES_DIR = Path(getattr(sys, "_MEIPASS", str(EXE_DIR)))
    # 代码/配置基准 = exe 目录
    BASE = EXE_DIR
    # 数据目录固定在 exe 同级，保证 exe 与数据打包在一起、可整体拷贝
    GARMIN_DATA_DIR = EXE_DIR / "garmin-data"
    ASSISTANT_DATA_DIR = EXE_DIR / "assistant-data"
else:
    # 开发期：本文件在仓 B 根目录
    BASE = Path(__file__).resolve().parent
    RES_DIR = BASE
    EXE_DIR = BASE
    # 开发期沿用 .env 的绝对路径（与仓 A 共享同一份 garmin-data）
    GARMIN_DATA_DIR = Path(os.getenv(
        "GARMIN_DATA_DIR",
        str(BASE.parent / "佳明运动数据同步" / "garmin-data"),
    ))
    ASSISTANT_DATA_DIR = Path(os.getenv(
        "ASSISTANT_DATA_DIR",
        str(BASE / "assistant-data"),
    ))


def garmin_sync_dir() -> Path:
    """兄弟仓 A 目录（仅开发期使用；打包后同步引擎已 vendor 进本工程 vendor/）。"""
    return BASE.parent / "佳明运动数据同步"


# 统一把数据目录写进环境变量：dashboard.app / config / vendor 同步引擎
# 都从 os.getenv 读取，确保打包后数据落在 exe 同级目录而非系统绝对路径。
os.environ["GARMIN_DATA_DIR"] = str(GARMIN_DATA_DIR)
os.environ["ASSISTANT_DATA_DIR"] = str(ASSISTANT_DATA_DIR)
