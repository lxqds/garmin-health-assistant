"""desktop/main.py —— 主窗口：左侧 WebView 嵌仪表盘，右侧 AI 聊天，顶部菜单

启动流程（见 launch）：先起 Flask 后端线程，再起 PyQt 事件循环。
"""
from __future__ import annotations

import os
import sys
import threading
import time

from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QSplitter, QMenuBar, QMenu,
)
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QIcon
from PyQt6.QtWebEngineWidgets import QWebEngineView

from desktop.chat_panel import ChatPanel
from desktop.settings_dialog import SettingsDialog

PORT = int(os.getenv("APP_PORT", "8500"))
BASE_URL = f"http://127.0.0.1:{PORT}/"
ICON_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "icon.png")


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
        self._build_menu()

    def _build_menu(self):
        menubar = self.menuBar()
        m_settings = menubar.addMenu("设置")
        m_settings.addAction("偏好设置（飞书 / 微信）", self.open_settings)
        m_tools = menubar.addMenu("工具")
        m_tools.addAction("刷新仪表盘", self.web.reload)

    def open_settings(self):
        SettingsDialog(self).exec()


def launch():
    # 1) 后端 Flask（Werkzeug）后台线程
    from server.app import app as flask_app
    srv = threading.Thread(
        target=lambda: flask_app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False),
        daemon=True,
    )
    srv.start()
    time.sleep(1.5)  # 等端口监听

    # 2) PyQt 事件循环
    qapp = QApplication(sys.argv)
    qapp.setWindowIcon(QIcon(ICON_PATH))     # 任务栏图标
    win = MainWindow()
    win.show()
    sys.exit(qapp.exec())


if __name__ == "__main__":
    launch()
