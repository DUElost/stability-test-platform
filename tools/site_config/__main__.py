from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .plan import plan_site_report
from .validation import validate_config_file


class RedactedParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments; use --help. Supplied argument values are not displayed.\n")


def main(argv: list[str] | None = None) -> int:
    parser = RedactedParser(prog="python -m tools.site_config", description="Offline site configuration checks only.")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate", help="Validate one explicit YAML file without resolving bindings or targets.")
    validate.add_argument("--config", type=Path, required=True, help="Explicit regular UTF-8 YAML input; no environment fallback.")
    validate.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    plan = commands.add_parser(
        "plan",
        help="Plan template rendering and release checks from one explicit YAML file and its declared local manifest.",
    )
    plan.add_argument("--config", type=Path, required=True, help="Explicit regular UTF-8 YAML input; no environment fallback.")
    plan.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    plan.add_argument(
        "--save-dir",
        type=Path,
        default=None,
        help="Existing directory you own; writes plan-report.json once with owner-only permissions.",
    )
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        report = validate_config_file(arguments.config)
    else:
        report = plan_site_report(arguments.config, save_dir=arguments.save_dir)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']} stage={report['stage']}: {report['summary']}")
        for check in report["checks"] + report["deferred_checks"]:
            print(f"{check['status']} {check['check_id']} role={check['role']} {check['location']} [{check['code']}]")
            print(f"  {check['message']} {check['remediation']}")
        if report.get("saved_file"):
            print(f"Plan report written: {report['saved_file']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
