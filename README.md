# 佳明 AI 健康助手（仓 B）

读取[仓 A `garmin-auto-sync`](https://github.com/lxqds/Garmin_auto_sync) 产出的佳明健康数据（「数据契约」），生成每日 **AI 健康建议与训练计划**，并推送到**飞书** / **个人微信**，提供可回看的 **Web 仪表盘**。

> 本仓**不持有任何佳明令牌**，只读仓 A 产出的 JSON。

## 数据衔接（双仓解耦）
| 变量 | 含义 | 默认 |
|---|---|---|
| `GARMIN_DATA_DIR` | 仓 A 产出的 `garmin-data/` 目录（健康数据契约） | `../Garmin_auto_sync/garmin-data` |
| `ASSISTANT_DATA_DIR` | 本仓分析产出目录（快照/日报/预览） | `./assistant-data`（已 gitignore） |

## 快速开始
```bash
cp .env.example .env        # 填入 DeepSeek key / 飞书 webhook / WxPusher / Dash
pip install -r requirements.txt
python ai/ai_analyze.py             # 分析昨天 → assistant-data/ai_daily_<date>.json/.md
python notify/preview_card.py       # 本地浏览器预览飞书卡片样式（无需飞书账号）
python notify/push_feishu.py        # 推送到飞书群（需配置 FEISHU_WEBHOOK_URL）
python notify/push_feishu.py --dry-run   # 仅打印卡片 JSON 不发送
```

## 目录
- `ai/`：每日快照归一化 + 30 日基线；AI 分析（DeepSeek / 规则兜底）
- `notify/`：飞书卡片推送、WxPusher 微信推送（待建）、HTML 预览、卡片模板
- `dashboard/`：Web 仪表盘（待建）
- `run_daily.py`：每日编排器（待建）

## 能力进度
- ✅ M1 数据层（snapshot 归一化 + 30日基线）+ AI 分析层（DeepSeek / 规则兜底）+ 今日训练计划生成
- ✅ M2 飞书富卡片（HMAC 签名）+ 本地 HTML 预览
- ✅ M3 个人微信（WxPusher）富文本卡片推送
- ✅ M4 Web 仪表盘（Flask，本地，含指标卡 + 趋势图）+ 每日编排器 `run_daily.py`

## 每日自动运行（推荐）
用 cron / 系统定时任务 / WorkBuddy 自动化，在仓 A 完成 `fetch` 之后跑：
```bash
cd garmin-health-assistant
python run_daily.py            # 分析昨天 → 飞书 + 微信双推
python dashboard/app.py        # 常驻起本地仪表盘（另开进程/服务）
```
未配置某渠道（FEISHU_WEBHOOK_URL / WXPUSHER_*）时该步自动跳过，不影响其他步骤。

## 桌面应用（EXE，内置 Agent）

把仪表盘、AI 对话、配置绑定整合进一个桌面窗口：左侧 WebView 内嵌仪表盘，右侧是与**内置 Agent** 的聊天面板，顶部菜单可改设置、触发同步。

- `agent/`：轻量 agent 框架（等价 WorkBuddy 思路，自包含可分发）
  - `llm.py`：OpenAI 兼容客户端，默认 DeepSeek，UI 可切换 base_url/key/model（也支持 OpenAI / 本地 Ollama）
  - `tools.py`：工具集（查健康/计划、发飞书/微信、触发同步、读写配置、设提醒），支持 function-calling
  - `core.py`：Agent 主循环（system prompt + 工具调度）
- `config/`：配置读写（`.env` 为真相源，敏感字段脱敏）
- `server/app.py`：复用 `dashboard.app`，新增 `/api/chat`、`/api/config`、`/api/push/*`、`/api/sync`、`/api/reminders`
- `desktop/`：PyQt6 壳（`main.py` 主窗口 / `chat_panel.py` 聊天 / `settings_dialog.py` 设置）
- `run_app.py`：启动器（先起 Flask 线程，再起 PyQt 窗口）

### 开发期运行
```bash
pip install -r requirements.txt
python run_app.py          # 自动起后端(127.0.0.1:8500) + 桌面窗口
```
首次打开后：菜单「设置 → 偏好设置」填入飞书 Webhook、WxPusher、AI API Key；右侧聊天可直接说
「把今日计划发到飞书」「触发同步」「帮我填 AI_API_KEY=sk-xxx」「设个提醒 21:30 睡觉」。

### 打包成 EXE
```bash
pip install pyinstaller PyQt6 PyQt6-WebEngine
pyinstaller build.spec          # 生成 dist/佳明健康助手/（onedir）
```
把 `.env` 放到 `dist/佳明健康助手/` 同目录即可（密钥不打包进 exe）。双击 `佳明健康助手.exe` 运行。
注：仪表盘趋势图用 ECharts CDN，运行时需联网；其余功能离线可用。

