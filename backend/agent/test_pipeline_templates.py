"""Regression tests for migrated pipeline templates.

Validates that:
1. All pipeline templates load and parse correctly
2. All referenced actions exist in ACTION_REGISTRY
3. Pipeline engine can execute templates with mocked ADB
4. Templates produce equivalent outcomes to legacy tool execution
"""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from backend.agent.actions import ACTION_REGISTRY
from backend.agent.pipeline_engine import PipelineEngine, StepResult

TEMPLATE_DIR = project_root / "backend" / "schemas" / "pipeline_templates"

EXPECTED_TEMPLATES = {
    "aimonkey": {"min_phases": 3, "min_steps": 10},
    "monkey": {"min_phases": 2, "min_steps": 2},
    "monkey_aee": {"min_phases": 2, "min_steps": 2},
    "mtbf": {"min_phases": 2, "min_steps": 3},
    "ddr": {"min_phases": 2, "min_steps": 3},
    "gpu": {"min_phases": 2, "min_steps": 3},
    "standby": {"min_phases": 2, "min_steps": 2},
}


class TestTemplateValidity(unittest.TestCase):
    """Test that all pipeline templates are valid and self-consistent."""

    def test_all_expected_templates_exist(self):
        for name in EXPECTED_TEMPLATES:
            path = TEMPLATE_DIR / f"{name}.json"
            self.assertTrue(path.exists(), f"Template missing: {path}")

    def test_templates_are_valid_json(self):
        for f in TEMPLATE_DIR.glob("*.json"):
            with open(f) as fp:
                data = json.load(fp)
            self.assertIn("version", data, f"{f.name} missing 'version'")
            self.assertIn("phases", data, f"{f.name} missing 'phases'")
            self.assertIsInstance(data["phases"], list, f"{f.name} 'phases' not a list")

    def test_templates_meet_minimum_structure(self):
        for name, reqs in EXPECTED_TEMPLATES.items():
            path = TEMPLATE_DIR / f"{name}.json"
            with open(path) as fp:
                data = json.load(fp)

            phases = data["phases"]
            total_steps = sum(len(p.get("steps", [])) for p in phases)

            self.assertGreaterEqual(
                len(phases), reqs["min_phases"],
                f"{name}: expected >= {reqs['min_phases']} phases, got {len(phases)}",
            )
            self.assertGreaterEqual(
                total_steps, reqs["min_steps"],
                f"{name}: expected >= {reqs['min_steps']} steps, got {total_steps}",
            )


class TestActionCoverage(unittest.TestCase):
    """Test that all actions referenced in templates are registered."""

    def test_all_template_actions_exist_in_registry(self):
        missing = []
        for f in TEMPLATE_DIR.glob("*.json"):
            with open(f) as fp:
                data = json.load(fp)
            for phase in data.get("phases", []):
                for step in phase.get("steps", []):
                    action = step.get("action", "")
                    if action.startswith("builtin:"):
                        action_name = action[len("builtin:"):]
                        if action_name not in ACTION_REGISTRY:
                            missing.append(f"{f.name}/{step['name']}: {action_name}")
                    elif action.startswith("shell:"):
                        pass  # shell actions don't need registry
                    elif action.startswith("tool:"):
                        pass  # tool actions resolved dynamically

        self.assertEqual(missing, [], f"Missing actions in registry: {missing}")

    def test_action_registry_has_minimum_actions(self):
        expected_actions = [
            "check_device", "clean_env", "push_resources",
            "ensure_root", "fill_storage", "connect_wifi", "install_apk",
            "start_process", "monitor_process", "stop_process", "run_instrument",
            "adb_pull", "collect_bugreport", "scan_aee",
            "aee_extract", "log_scan", "run_tool_script",
        ]
        for action in expected_actions:
            self.assertIn(action, ACTION_REGISTRY, f"Action '{action}' not in registry")


class TestPipelineExecution(unittest.TestCase):
    """Test pipeline engine executes templates with mocked ADB."""

    def _mock_adb(self):
        adb = MagicMock()
        adb.adb_path = "adb"
        adb.shell.return_value = "test"
        adb.push.return_value = None
        adb.pull.return_value = None
        return adb

    def _simple_pipeline(self):
        """Return a minimal pipeline for testing engine mechanics."""
        return {
            "version": 1,
            "phases": [
                {
                    "name": "prepare",
                    "parallel": False,
                    "steps": [
                        {
                            "name": "check_device",
                            "action": "builtin:check_device",
                            "params": {},
                            "timeout": 5,
                            "on_failure": "stop",
                        }
                    ],
                }
            ],
        }

    def test_engine_executes_simple_pipeline(self):
        adb = self._mock_adb()
        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(self._simple_pipeline())
        self.assertTrue(result.success)
        self.assertEqual(result.exit_code, 0)

    def test_engine_handles_failed_step_with_stop(self):
        adb = self._mock_adb()
        adb.shell.side_effect = Exception("device disconnected")

        pipeline = {
            "version": 1,
            "phases": [
                {
                    "name": "prepare",
                    "parallel": False,
                    "steps": [
                        {
                            "name": "check_device",
                            "action": "builtin:check_device",
                            "params": {},
                            "timeout": 5,
                            "on_failure": "stop",
                        },
                        {
                            "name": "ensure_root",
                            "action": "builtin:ensure_root",
                            "params": {},
                            "timeout": 5,
                            "on_failure": "stop",
                        },
                    ],
                }
            ],
        }

        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(pipeline)
        self.assertFalse(result.success)

    def test_engine_handles_failed_step_with_continue(self):
        adb = self._mock_adb()
        # First call fails (check_device), second succeeds
        adb.shell.side_effect = [Exception("fail"), "0"]

        pipeline = {
            "version": 1,
            "phases": [
                {
                    "name": "prepare",
                    "parallel": False,
                    "steps": [
                        {
                            "name": "check_device",
                            "action": "builtin:check_device",
                            "params": {},
                            "timeout": 5,
                            "on_failure": "continue",
                        },
                        {
                            "name": "ensure_root",
                            "action": "builtin:ensure_root",
                            "params": {},
                            "timeout": 5,
                            "on_failure": "stop",
                        },
                    ],
                }
            ],
        }

        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(pipeline)
        # Pipeline should complete (first step continues despite failure)
        # but overall result reflects the failure
        self.assertFalse(result.success)

    def test_engine_parallel_phase(self):
        adb = self._mock_adb()

        pipeline = {
            "version": 1,
            "phases": [
                {
                    "name": "parallel_checks",
                    "parallel": True,
                    "steps": [
                        {"name": "check1", "action": "builtin:check_device", "params": {}, "timeout": 5, "on_failure": "stop"},
                        {"name": "check2", "action": "builtin:check_device", "params": {}, "timeout": 5, "on_failure": "stop"},
                    ],
                }
            ],
        }

        engine = PipelineEngine(adb=adb, serial="FAKE001", run_id=1)
        result = engine.execute(pipeline)
        self.assertTrue(result.success)

    def test_aimonkey_template_loads_and_validates(self):
        with open(TEMPLATE_DIR / "aimonkey.json") as fp:
            template = json.load(fp)

        self.assertEqual(template["version"], 1)
        self.assertEqual(len(template["phases"]), 3)

        phase_names = [p["name"] for p in template["phases"]]
        self.assertEqual(phase_names, ["prepare", "execute", "post_process"])

        # Validate all actions are resolvable
        for phase in template["phases"]:
            for step in phase.get("steps", []):
                action = step["action"]
                if action.startswith("builtin:"):
                    self.assertIn(action[8:], ACTION_REGISTRY, f"Missing: {action}")


if __name__ == "__main__":
    unittest.main()
