"""desktop/chat_panel.py —— 应用内 AI 聊天面板

用户在右侧输入，消息经 /api/chat 交给内置 Agent；Agent 可调用工具（查数据/推送/同步/配置），
回复显示在面板里。网络请求放在 QThread，避免卡 UI。

AI 回复渲染为 Markdown（标题/列表/表格/代码块/加粗都正常显示），
用户消息保持纯文本气泡样式。
"""
from __future__ import annotations

import html as html_lib
import os

import markdown as _markdown
import requests
from PyQt6.QtCore import QThread, pyqtSignal, Qt
from PyQt6.QtGui import QFont, QTextCursor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QTextBrowser, QLineEdit,
    QPushButton, QLabel,
)

PORT = os.getenv("APP_PORT", "8500")
API_BASE = f"http://127.0.0.1:{PORT}"

# —— 全局样式：气泡 + Markdown 元素 ——
# 把这些样式用 QTextBrowser.setStyleSheet 注入；同时给 .ai/.you/.sys 气泡类加白名单。
_CSS = """
QTextBrowser { background:#f7f8fa; border:1px solid #e5e6eb; border-radius:8px; padding:6px; }
.who  { font-size:11px; color:#86909c; margin:8px 4px 2px; }
.you  { background:#d9eaff; border-radius:10px; padding:8px 12px;
        margin:2px 24px 8px 60px; color:#1d2129; }
.ai   { background:#ffffff; border-radius:10px; padding:8px 12px;
        margin:2px 60px 8px 24px; color:#1d2129; border:1px solid #e5e6eb; }
.sys  { color:#c9cdd4; font-size:11px; margin:4px 4px 8px; text-align:center; }

h1, h2, h3, h4 { color:#165dff; margin:8px 0 4px; font-weight:600; }
h1 { font-size:18px; }
h2 { font-size:16px; }
h3 { font-size:14px; }
h4 { font-size:13px; }
p   { margin:4px 0; line-height:1.6; }
ul, ol { margin:4px 0 4px 22px; }
li { margin:2px 0; line-height:1.6; }
strong { color:#1d2129; font-weight:700; }
em     { color:#4e5969; }
code   { background:#f2f3f5; padding:1px 6px; border-radius:4px;
         font-family:Consolas, "Courier New", monospace; font-size:12px; }
pre    { background:#f2f3f5; padding:8px; border-radius:6px;
         font-family:Consolas, "Courier New", monospace; font-size:12px;
         white-space:pre-wrap; }
blockquote { border-left:3px solid #c9cdd4; padding-left:10px;
             color:#4e5969; margin:4px 0; }
table { border-collapse:collapse; margin:6px 0; font-size:12px; }
th, td { border:1px solid #e5e6eb; padding:4px 8px; text-align:left; }
th { background:#f2f3f5; font-weight:600; }
tr:nth-child(even) td { background:#fafbfc; }
hr { border:none; border-top:1px dashed #e5e6eb; margin:8px 0; }
a { color:#165dff; text-decoration:none; }
"""


def _md_to_html(text: str) -> str:
    """AI 回复：Markdown → 漂亮的 HTML（标题/列表/表格/代码块/加粗）。"""
    try:
        html = _markdown.markdown(
            text or "",
            extensions=["extra", "tables", "sane_lists", "nl2br"],
            output_format="html5",
        )
    except Exception:
        # 极端情况：纯文本 + 换行
        html = html_lib.escape(text or "").replace("\n", "<br>")
    return html


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

        # —— 改用 QTextBrowser，支持富文本渲染（Markdown 表格/列表/标题） ——
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(True)
        self.view.document().setDefaultStyleSheet(_CSS)
        self.view.setStyleSheet(_CSS)
        layout.addWidget(self.view, stretch=1)

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

        # 初始欢迎语
        self._add(
            "ai",
            "你好，我是你的佳明健康助手。可以帮你**查健康数据**、**看训练计划**、"
            "**发飞书/微信推送**、**触发佳明同步**，也能帮你**填写 API 设置**。",
        )

    def send(self):
        text = self.input.text().strip()
        if not text or (self.worker and self.worker.isRunning()):
            return
        self._add("you", text)
        self.history.append({"role": "user", "content": text})
        self.input.clear()
        self.worker = ChatWorker(text, self.history)
        self.worker.finished.connect(self._on_done)
        self.worker.start()

    def _on_done(self, ok: bool, text: str):
        if ok:
            self._add("ai", text)
            self.history.append({"role": "assistant", "content": text})
        else:
            self._add("sys", "请求出错：" + html_lib.escape(text))

    def _add(self, who: str, text: str):
        """追加一条气泡。

        - you：纯文本右对齐（用户原始输入，避免渲染 markdown）
        - ai：渲染 Markdown（标题/列表/表格/加粗/代码）
        - sys：灰色居中小字
        """
        cursor = self.view.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.End)

        if who == "you":
            label = '<div class="who" style="text-align:right;">你</div>'
            body = f'<div class="you">{html_lib.escape(text).replace(chr(10), "<br>")}</div>'
            cursor.insertHtml(label + body)
        elif who == "ai":
            label = '<div class="who">AI 助手</div>'
            body = f'<div class="ai">{_md_to_html(text)}</div>'
            cursor.insertHtml(label + body)
        else:
            body = f'<div class="sys">{html_lib.escape(text)}</div>'
            cursor.insertHtml(body)

        # 自动滚到底部
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())