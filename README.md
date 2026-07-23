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
- ✅ M1 数据层 + AI 分析层（规则兜底可离线运行）
- ✅ M2 飞书富卡片 + 本地预览
- ⏳ M3 个人微信（WxPusher）推送
- ⏳ M4 Web 仪表盘 + 每日编排自动化
