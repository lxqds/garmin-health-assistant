"""desktop/main.py —— 主窗口：左侧 WebView 嵌仪表盘，右侧 AI 聊天，顶部菜单

启动流程（见 launch）：
  1. 后台起 Flask 服务（仪表盘 + agent API）
  2. 已登录则后台自动拉取佳明数据 + 分析今日（"打开 exe 就跑"）
  3. 起 PyQt 事件循环
"""
from __future__ import annotations

import os
import sys
import threading
import time
import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app_paths import RES_DIR

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSplitter,
)
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView

from desktop.chat_panel import ChatPanel

PORT = int(os.getenv("APP_PORT", "8500"))
BASE_URL = f"http://127.0.0.1:{PORT}/"
ICON_PATH = str(RES_DIR / "assets" / "icon.png")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("佳明健康助手")
        self.setWindowIcon(QIcon(ICON_PATH))
        self.resize(1320, 820)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.web = QWebEngineView()
        self.web.load(QUrl(BASE_URL))
        self.chat = ChatPanel()
        splitter.addWidget(self.web)
        splitter.addWidget(self.chat)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)


def _auto_sync_on_startup():
    """exe 打开时若已登录则后台自动拉取佳明数据 + 分析今日，让数据尽快出现。"""
    time.sleep(3)  # 让 UI 先加载
    try:
        from agent.garmin_login import garmin_status
        s = garmin_status()
        if not s.get("logged_in"):
            return
        from vendor.garmin_sync import cmd_fetch
        cmd_fetch(days=2, force=False, token_only=True)
        # 顺手分析今日（已有就跳过，不烧 token）
        try:
            from ai.ai_analyze import analyze
            d = datetime.date.today().isoformat()
            analyze(d, force=False, plan_base_date=d)
        except Exception:
            pass
    except Exception as e:
        print(f"[auto-sync] {e}", file=sys.stderr)


def launch():
    # 1) 后端 Flask（Werkzeug）后台线程
    from server.app import app as flask_app
    srv = threading.Thread(
        target=lambda: flask_app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    srv.start()
    time.sleep(1.5)  # 等端口监听

    # 1.5) 已登录则后台自动同步（"打开 exe 就跑"，无需用户手动触发）
    threading.Thread(target=_auto_sync_on_startup, daemon=True).start()

    # 2) PyQt 事件循环
    qapp = QApplication(sys.argv)
    qapp.setWindowIcon(QIcon(ICON_PATH))     # 任务栏图标
    win = MainWindow()
    win.show()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    launch()
