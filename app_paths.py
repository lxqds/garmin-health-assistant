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

import sys
from pathlib import Path

FROZEN = getattr(sys, "frozen", False)

if FROZEN:
    # exe 所在目录：.env、assistant-data 等用户文件放这里（持久）
    EXE_DIR = Path(sys.executable).resolve().parent
    # 资源解压目录（只读）：templates / assets
    RES_DIR = Path(getattr(sys, "_MEIPASS", str(EXE_DIR)))
    # 代码/配置基准 = exe 目录
    BASE = EXE_DIR
else:
    # 开发期：本文件在仓 B 根目录
    BASE = Path(__file__).resolve().parent
    RES_DIR = BASE
    EXE_DIR = BASE


def garmin_sync_dir() -> Path:
    """兄弟仓 A 目录（仅开发期使用；打包后同步引擎已 vendor 进本工程 vendor/）。"""
    return BASE.parent / "佳明运动数据同步"
