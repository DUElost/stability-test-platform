import sys
import unittest
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

try:
    from backend.api.routes.runs import _report_to_markdown
    from backend.services.report_service import (
        build_jira_draft as _build_jira_draft,
        build_risk_alerts as _build_risk_alerts,
        parse_run_log_summary as _parse_run_log_summary,
    )
    from backend.api.schemas import (
        RunReportOut,
        RunOut,
        TaskOut,
        RiskAlertOut,
    )
except ModuleNotFoundError:
    from api.routes.runs import _report_to_markdown
    from services.report_service import (
        build_jira_draft as _build_jira_draft,
        build_risk_alerts as _build_risk_alerts,
        parse_run_log_summary as _parse_run_log_summary,
    )
    from api.schemas import (
        RunReportOut,
        RunOut,
        TaskOut,
        RiskAlertOut,
    )


class TestRunReportHelpers(unittest.TestCase):
    def _sample_report(self) -> RunReportOut:
        return RunReportOut(
            generated_at=datetime.utcnow(),
            run=RunOut(
                id=101,
                task_id=11,
                host_id=3,
                device_id=9,
                status="FAILED",
                started_at=None,
                finished_at=None,
                exit_code=1,
                error_code="AIMONKEY_RISK",
                error_message="high risk",
                log_summary="monitor=runtime completed; risk=HIGH; events=3; restarts=2; aee_entries=1",
                artifacts=[],
                risk_summary={"risk_level": "HIGH", "counts": {"events_total": 3, "by_type": {"ANR": 1}}},
            ),
            task=TaskOut(
                id=11,
                name="AIMONKEY regression",
                type="AIMONKEY",
                template_id=None,
                params={},
                target_device_id=9,
                status="FAILED",
                priority=0,
                created_at=datetime.utcnow(),
            ),
            host=None,
            device=None,
            summary_metrics={"restarts": 2, "events": 3},
            risk_summary={"risk_level": "HIGH", "counts": {"events_total": 3, "by_type": {"ANR": 1}}},
            alerts=[RiskAlertOut(code="ANR_DETECTED", severity="HIGH", message="ANR found")],
        )

    def test_report_to_markdown_contains_sections(self):
        report = self._sample_report()
        text = _report_to_markdown(report)
        self.assertIn("# Run Report - 101", text)
        self.assertIn("## Risk Summary", text)
        self.assertIn("## Alerts", text)

    def test_parse_run_log_summary(self):
        metrics = _parse_run_log_summary(
            "monitor=runtime completed; risk=HIGH; events=3; restarts=2; aee_entries=1"
        )
        self.assertEqual(metrics.get("monitor"), "runtime completed")
        self.assertEqual(metrics.get("risk"), "HIGH")
        self.assertEqual(metrics.get("events"), 3)
        self.assertEqual(metrics.get("restarts"), 2)

    def test_build_risk_alerts_with_high_risk(self):
        risk_summary = {
            "risk_level": "HIGH",
            "counts": {
                "restart_count": 3,
                "by_type": {
                    "ANR": 2,
                    "CRASH": 1,
                },
            },
        }
        alerts = _build_risk_alerts(risk_summary, {"restarts": 3})
        codes = {item.code for item in alerts}
        self.assertIn("RISK_LEVEL_HIGH", codes)
        self.assertIn("ANR_DETECTED", codes)
        self.assertIn("CRASH_DETECTED", codes)
        self.assertIn("RESTART_FREQUENT", codes)

    def test_build_jira_draft_from_report(self):
        report = self._sample_report()
        draft = _build_jira_draft(report)
        self.assertEqual(draft.run_id, 101)
        self.assertEqual(draft.priority, "Critical")
        self.assertIn("AIMONKEY", draft.summary)
        self.assertIn("h2. Alerts", draft.description)
        self.assertEqual(draft.project_key, "STABILITY")

    def test_build_jira_draft_template_mapping(self):
        report = self._sample_report()
        template = """
        {
          "default": {
            "project_key": "QA",
            "component": "Stability-Core",
            "labels": ["auto-jira"],
            "custom_fields": {"cf_team": "stability"}
          },
          "task_type": {
            "AIMONKEY": {
              "component": "AIMONKEY",
              "fix_version": "AIMONKEY-V1",
              "labels": ["aimonkey"]
            }
          },
          "risk_level": {
            "HIGH": {
              "assignee": "qa.owner",
              "labels": ["risk-high"]
            }
          }
        }
        """.strip()
        module_name = _build_jira_draft.__module__
        with patch(f"{module_name}.REPORT_JIRA_TEMPLATE_JSON", template):
            draft = _build_jira_draft(report)
        self.assertEqual(draft.project_key, "QA")
        self.assertEqual(draft.component, "AIMONKEY")
        self.assertEqual(draft.fix_version, "AIMONKEY-V1")
        self.assertEqual(draft.assignee, "qa.owner")
        self.assertIn("auto-jira", draft.labels)
        self.assertIn("aimonkey", draft.labels)
        self.assertIn("risk-high", draft.labels)
        self.assertEqual(draft.custom_fields.get("cf_team"), "stability")


if __name__ == "__main__":
    unittest.main()
