"""desktop/chat_panel.py —— 应用内 AI 聊天面板

用户在右侧输入，消息经 /api/chat 交给内置 Agent；Agent 可调用工具（查数据/推送/同步/配置），
回复显示在面板里。网络请求放在 QThread，避免卡 UI。
"""
from __future__ import annotations

import os
import requests
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QListWidget, QLineEdit,
    QPushButton, QLabel,
)
from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QFont

PORT = os.getenv("APP_PORT", "8500")
API_BASE = f"http://127.0.0.1:{PORT}"


class ChatWorker(QThread):
    finished = pyqtSignal(bool, str)

    def __init__(self, message: str, history: list):
        super().__init__()
        self.message = message
        self.history = history

    def run(self):
        try:
            r = requests.post(
                f"{API_BASE}/api/chat",
                json={"message": self.message, "history": self.history},
                timeout=180,
            )
            data = r.json()
            if data.get("ok"):
                self.finished.emit(True, data.get("reply", ""))
            else:
                self.finished.emit(False, data.get("error", "未知错误"))
        except Exception as e:
            self.finished.emit(False, str(e))


class ChatPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.history: list = []
        self.worker: ChatWorker | None = None
        self._build()

    def _build(self):
        layout = QVBoxLayout(self)
        title = QLabel("💬 AI 助手")
        title.setFont(QFont("PingFang SC", 13, QFont.Weight.Bold))
        layout.addWidget(title)

        self.list = QListWidget()
        self.list.setWordWrap(True)
        layout.addWidget(self.list, stretch=1)

        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("问点什么，比如：我昨天恢复得怎么样？/ 把今日计划发到飞书")
        self.input.returnPressed.connect(self.send)
        self.send_btn = QPushButton("发送")
        self.send_btn.clicked.connect(self.send)
        row.addWidget(self.input, stretch=1)
        row.addWidget(self.send_btn)
        layout.addLayout(row)

        hint = QLabel("提示：可直接让 AI 发飞书/微信、触发同步、填写 API、设置提醒。")
        hint.setStyleSheet("color:#86909c;font-size:11px;")
        layout.addWidget(hint)

        self._add("AI", "你好，我是你的佳明健康助手。可以帮你查健康数据、看训练计划、发推送、触发同步，也能帮你填 API。")

    def send(self):
        text = self.input.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self._add("你", text)
        self.history.append({"role": "user", "content": text})
        self.input.clear()
        self.worker = ChatWorker(text, self.history)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _on_done(self, ok: bool, text: str):
        if ok:
            self._add("AI", text)
            self.history.append({"role": "assistant", "content": text})
        else:
            self._add("系统", "请求出错：" + text)

    def _add(self, who: str, text: str):
        self.list.addItem(f"【{who}】 {text}")
        self.list.scrollToBottom()
