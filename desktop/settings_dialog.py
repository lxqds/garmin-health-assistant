"""desktop/settings_dialog.py —— 设置对话框（飞书 / 微信 / AI）

读取 /api/config 展示当前值（脱敏），保存时逐条 POST /api/config。
AI 页提供厂商下拉（DeepSeek / OpenAI / 本地 Ollama），选中即填预设 base_url/model。
"""
from __future__ import annotations

import os
import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTabWidget, QWidget, QLabel,
    QLineEdit, QPushButton, QComboBox, QMessageBox, QFormLayout,
)

PORT = os.getenv("APP_PORT", "8500")
API_BASE = f"http://127.0.0.1:{PORT}"

FIELDS = {
    "飞书": [
        ("FEISHU_WEBHOOK_URL", "Webhook 地址", False),
        ("FEISHU_SECRET", "签名 Secret（可空）", True),
    ],
    "微信": [
        ("WXPUSHER_APP_TOKEN", "App Token", True),
        ("WXPUSHER_UIDS", "UIDs（逗号分隔）", True),
        ("WXPUSHER_TOPIC_IDS", "Topic IDs（可空）", False),
    ],
    "AI": [
        ("AI_PROVIDER", "厂商", False),
        ("AI_BASE_URL", "Base URL", False),
        ("AI_API_KEY", "API Key", True),
        ("AI_MODEL", "模型名", False),
    ],
}

PROVIDER_PRESETS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
    "ollama": ("http://127.0.0.1:11434/v1", "qwen2.5:7b"),
}


class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("偏好设置")
        self.resize(460, 360)
        self.inputs: dict[str, QLineEdit] = {}
        self.provider_combo: QComboBox | None = None
        self._build()
        self._load()

    def _build(self):
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        for tab_name, fields in FIELDS.items():
            tab = QWidget()
            form = QFormLayout(tab)
            for key, label, secret in fields:
                le = QLineEdit()
                le.setEchoMode(QLineEdit.EchoMode.Password if secret else QLineEdit.EchoMode.Normal)
                le.setPlaceholderText(label)
                self.inputs[key] = le
                form.addRow(label, le)
                if key == "AI_PROVIDER":
                    self.provider_combo = QComboBox()
                    self.provider_combo.addItems(["deepseek", "openai", "ollama"])
                    self.provider_combo.currentTextChanged.connect(self._on_provider)
                    form.addRow("快速选择厂商", self.provider_combo)
            tabs.addTab(tab, tab_name)
        layout.addWidget(tabs)

        row = QHBoxLayout()
        row.addStretch(1)
        self.save_btn = QPushButton("保存")
        self.save_btn.clicked.connect(self.save)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        row.addWidget(self.save_btn)
        row.addWidget(self.cancel_btn)
        layout.addLayout(row)

    def _on_provider(self, name: str):
        base, model = PROVIDER_PRESETS.get(name, ("", ""))
        if base:
            self.inputs["AI_BASE_URL"].setText(base)
        if model:
            self.inputs["AI_MODEL"].setText(model)

    def _load(self):
        try:
            r = requests.get(f"{API_BASE}/api/config", timeout=10)
            data = r.json() if r.ok else {}
        except Exception:
            data = {}
        for key in self.inputs:
            val = data.get(key, "")
            # 脱敏值（****）不回填，避免覆盖真实值
            if val and "****" not in val:
                self.inputs[key].setText(val)
        prov = data.get("AI_PROVIDER", "")
        if prov and self.provider_combo:
            idx = self.provider_combo.findText(prov)
            if idx >= 0:
                self.provider_combo.setCurrentIndex(idx)

    def save(self):
        ok_count = 0
        for key, le in self.inputs.items():
            val = le.text().strip()
            if not val:
                continue
            masked = "****" in val
            if masked:
                continue  # 用户没改脱敏字段，跳过
            try:
                r = requests.post(f"{API_BASE}/api/config",
                                  json={"key": key, "value": val}, timeout=10)
                if r.ok and r.json().get("ok"):
                    ok_count += 1
            except Exception as e:
                QMessageBox.warning(self, "保存失败", f"{key}: {e}")
        QMessageBox.information(self, "已保存", f"成功写入 {ok_count} 项配置（已落 .env）。")
        self.accept()
