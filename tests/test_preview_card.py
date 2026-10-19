"""Offline regressions for Feishu card preview and report-cache behavior."""
import importlib
import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ["ASSISTANT_DATA_DIR"] = tempfile.mkdtemp(prefix="garmin-assistant-")

# python-dotenv is a declared dependency, but keep these regressions runnable
# in a clean/offline interpreter without installing optional project packages.
dotenv_stub = types.ModuleType("dotenv")
dotenv_stub.load_dotenv = lambda *args, **kwargs: None
sys.modules.setdefault("dotenv", dotenv_stub)

preview_card = importlib.import_module("notify.preview_card")
push_feishu = importlib.import_module("notify.push_feishu")
snapshot = importlib.import_module("ai.snapshot")
ai_analyze = importlib.import_module("ai.ai_analyze")


class PreviewCardTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "overall_status": "状态平稳",
            "status_color": "blue",
            "advice": "今天适合轻松训练。注意补水。",
            "status_metrics": [
                {"name": "HRV", "value": "52 ms"},
                {"name": "睡眠分", "value": "81"},
            ],
            "training_plan": [
                {"type": "轻松跑", "duration": "30分钟", "zone": "Z2", "note": "保持放松"}
            ],
            "plan_date": "2026-10-18",
            "date": "2026-10-19",
        }

    def test_preview_module_imports_and_renders_current_status_metrics(self):
        html = preview_card.render_html(self.data, "2026-10-19")
        self.assertIn("HRV", html)
        self.assertIn("52 ms", html)
        self.assertIn("轻松跑", html)
        self.assertIn("<!doctype html>", html)

    def test_preview_command_writes_html_file(self):
        with tempfile.TemporaryDirectory() as temp:
            with (
                patch.object(preview_card, "OUTPUT_DIR", Path(temp)),
                patch.object(preview_card, "load_ai", return_value=self.data),
                patch.object(sys, "argv", ["preview_card.py", "--date", "2026-10-19"]),
            ):
                preview_card.main()
            generated = Path(temp) / "preview_card_2026-10-19.html"
            self.assertTrue(generated.exists())
            self.assertIn("52 ms", generated.read_text(encoding="utf-8"))

    def test_status_columns_schema_is_consumable_by_feishu_card_and_preview(self):
        columns = push_feishu.build_status_columns(self.data)
        self.assertEqual(columns["tag"], "column_set")
        self.assertEqual(len(columns["columns"]), 2)
        first = columns["columns"][0]
        self.assertEqual(first["elements"][0]["text"]["tag"], "lark_md")
        self.assertEqual(first["elements"][0]["text"]["content"], "**HRV**\n52 ms")
        self.assertIn("52 ms", preview_card.render_html(self.data, "2026-10-19"))

    def test_load_ai_accepts_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "ai_daily_2026-10-19.json"
            path.write_text(json.dumps(self.data, ensure_ascii=False), encoding="utf-8-sig")
            with patch.object(push_feishu, "AI_JSON", Path(temp) / "ai_daily_{date}.json"):
                self.assertEqual(push_feishu.load_ai("2026-10-19"), self.data)

    def test_snapshot_inputs_accept_utf8_bom(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            expected_health = {"2026-10-19": {"steps": 1234}}
            expected_activities = {"2026-10-19": []}
            (directory / "health_records.json").write_text(json.dumps(expected_health), encoding="utf-8-sig")
            (directory / "activities.json").write_text(json.dumps(expected_activities), encoding="utf-8-sig")
            self.assertEqual(snapshot.load_health_records(directory), expected_health)
            self.assertEqual(snapshot.load_activities(directory), expected_activities)

    def test_cached_empty_report_is_rebuilt_when_source_data_arrives(self):
        with tempfile.TemporaryDirectory() as temp:
            date = "2026-10-19"
            report = Path(temp) / f"ai_daily_{date}.json"
            markdown = Path(temp) / f"ai_daily_{date}.md"
            empty_report = {"metrics": [], "status_metrics": [], "sleep": {"score": None, "duration_h": None}}
            report.write_text(json.dumps(empty_report), encoding="utf-8-sig")
            snapshots = [
                {"source": "garmin", "metrics": {}, "sleep_stages": {}},
                {"source": "garmin", "metrics": {}, "activities": []},
            ]
            with (
                patch.object(ai_analyze, "AI_OUT_JSON", Path(temp) / "ai_daily_{date}.json"),
                patch.object(ai_analyze, "AI_OUT_MD", Path(temp) / "ai_daily_{date}.md"),
                patch.object(ai_analyze, "build_snapshot", side_effect=snapshots) as build,
                patch.object(ai_analyze, "save_snapshot"),
                patch.object(ai_analyze, "rule_based", return_value={"metrics": [], "advice": "可恢复训练。"}),
                patch.object(ai_analyze, "_status_metrics", return_value=[]),
                patch.object(ai_analyze, "render_md", return_value="# report"),
                patch.object(ai_analyze.coach_plan, "load_coach_plan", return_value={}),
            ):
                result = ai_analyze.analyze(date, no_ai=True)
            self.assertEqual(build.call_count, 2)
            self.assertEqual(result["date"], date)
            self.assertTrue(markdown.exists())
            self.assertNotEqual(json.loads(report.read_text(encoding="utf-8")), empty_report)


if __name__ == "__main__":
    unittest.main()
