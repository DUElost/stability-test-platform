"""Regression tests for stages-based pipeline templates.

Validates that:
1. All pipeline templates load and parse correctly.
2. All referenced builtin actions exist in ACTION_REGISTRY.
3. Pipeline engine executes stages-format pipelines.
4. Legacy phases-format definitions are rejected.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

project_root = Path(__file__).parent.parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.agent.actions import ACTION_REGISTRY
from backend.agent.pipeline_engine import PipelineEngine

TEMPLATE_DIR = project_root / "backend" / "schemas" / "pipeline_templates"

EXPECTED_TEMPLATES = {
    "aimonkey": {"min_steps": 10},
    "monkey": {"min_steps": 2},
    "monkey_aee": {"min_steps": 2},
    "mtbf": {"min_steps": 3},
    "ddr": {"min_steps": 3},
    "gpu": {"min_steps": 3},
    "standby": {"min_steps": 2},
}

REQUIRED_STAGE_KEYS = {"prepare", "execute", "post_process"}


class TestTemplateValidity(unittest.TestCase):
    """Test that all pipeline templates are valid and self-consistent."""

    def test_all_expected_templates_exist(self):
        for name in EXPECTED_TEMPLATES:
            path = TEMPLATE_DIR / f"{name}.json"
            self.assertTrue(path.exists(), f"Template missing: {path}")

    def test_templates_are_valid_json(self):
        for f in TEMPLATE_DIR.glob("*.json"):
            with open(f, encoding="utf-8-sig") as fp:
                data = json.load(fp)
            self.assertIn("version", data, f"{f.name} missing 'version'")
            self.assertIn("stages", data, f"{f.name} missing 'stages'")
            self.assertIsInstance(data["stages"], dict, f"{f.name} 'stages' not an object")
            self.assertTrue(
                REQUIRED_STAGE_KEYS.issubset(set(data["stages"].keys())),
                f"{f.name} stages must contain {sorted(REQUIRED_STAGE_KEYS)}",
            )

    def test_templates_meet_minimum_structure(self):
        for name, reqs in EXPECTED_TEMPLATES.items():
            path = TEMPLATE_DIR / f"{name}.json"
            with open(path, encoding="utf-8-sig") as fp:
                data = json.load(fp)

            stages = data["stages"]
            total_steps = sum(len(stages.get(k) or []) for k in REQUIRED_STAGE_KEYS)
            non_empty_stage_count = sum(1 for k in REQUIRED_STAGE_KEYS if len(stages.get(k) or []) > 0)

            self.assertGreaterEqual(
                non_empty_stage_count,
                1,
                f"{name}: expected >= 1 non-empty stage",
            )
            self.assertGreaterEqual(
                total_steps,
                reqs["min_steps"],
                f"{name}: expected >= {reqs['min_steps']} steps, got {total_steps}",
            )


class TestActionCoverage(unittest.TestCase):
    """Test that all actions referenced in templates are registered."""

    def test_all_template_actions_exist_in_registry(self):
        missing = []
        for f in TEMPLATE_DIR.glob("*.json"):
            with open(f, encoding="utf-8-sig") as fp:
                data = json.load(fp)
            for stage_name, steps in (data.get("stages") or {}).items():
                for step in (steps or []):
                    action = step.get("action", "")
                    step_id = step.get("step_id", "unknown")
                    if action.startswith("builtin:"):
                        action_name = action[len("builtin:") :]
                        if action_name not in ACTION_REGISTRY:
                            missing.append(f"{f.name}/{stage_name}/{step_id}: {action_name}")
                    elif action.startswith("tool:"):
                        continue  # tool actions are resolved dynamically
                    else:
                        missing.append(f"{f.name}/{stage_name}/{step_id}: invalid action '{action}'")

        self.assertEqual(missing, [], f"Missing or invalid actions: {missing}")

    def test_action_registry_has_minimum_actions(self):
        expected_actions = [
            "check_device",
            "clean_env",
            "push_resources",
            "ensure_root",
            "fill_storage",
            "connect_wifi",
            "install_apk",
            "start_process",
            "monitor_process",
            "stop_process",
            "run_instrument",
            "adb_pull",
            "collect_bugreport",
            "scan_aee",
            "aee_extract",
            "log_scan",
            "run_tool_script",
        ]
        for action in expected_actions:
            self.assertIn(action, ACTION_REGISTRY, f"Action '{action}' not in registry")


class TestPipelineExecution(unittest.TestCase):
    """Test pipeline engine executes stages-format definitions."""

    def _mock_adb(self):
        adb = MagicMock()
        adb.adb_path = "adb"
        adb.shell.return_value = "test"
        adb.push.return_value = None
        adb.pull.return_value = None
        return adb

    def _simple_pipeline(self):
        return {
            "stages": {
                "prepare": [
                    {
                        "step_id": "check_device",
                        "action": "builtin:check_device",
                        "params": {},
                        "timeout_seconds": 5,
                        "retry": 0,
                    }
                ],
                "execute": [],
                "post_process": [],
            }
        }

    def test_engine_executes_simple_pipeline(self):
        adb = self._mock_adb()
        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(self._simple_pipeline())
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)

    def test_engine_rejects_legacy_phases(self):
        adb = self._mock_adb()
        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(
            {
                "phases": [
                    {
                        "name": "prepare",
                        "steps": [{"name": "check_device", "action": "builtin:check_device"}],
                    }
                ]
            }
        )
        self.assertFalse(result.success)
        self.assertIn("legacy phases format", result.error_message)

    def test_engine_stops_on_failed_step(self):
        adb = self._mock_adb()
        adb.shell.side_effect = Exception("device disconnected")

        pipeline = {
            "stages": {
                "prepare": [
                    {
                        "step_id": "first_check",
                        "action": "builtin:check_device",
                        "params": {},
                        "timeout_seconds": 5,
                        "retry": 0,
                    },
                    {
                        "step_id": "second_check",
                        "action": "builtin:check_device",
                        "params": {},
                        "timeout_seconds": 5,
                        "retry": 0,
                    },
                ],
                "execute": [],
                "post_process": [],
            }
        }

        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(pipeline)
        self.assertFalse(result.success)
        self.assertEqual(adb.shell.call_count, 2)

    def test_aimonkey_template_loads_and_validates(self):
        with open(TEMPLATE_DIR / "aimonkey.json", encoding="utf-8-sig") as fp:
            template = json.load(fp)

        self.assertEqual(template["version"], 1)
        self.assertIn("stages", template)
        self.assertTrue(REQUIRED_STAGE_KEYS.issubset(template["stages"].keys()))

        for stage_name, steps in template["stages"].items():
            for step in steps:
                action = step["action"]
                if action.startswith("builtin:"):
                    self.assertIn(action[8:], ACTION_REGISTRY, f"Missing: {stage_name}/{action}")


if __name__ == "__main__":
    unittest.main()
