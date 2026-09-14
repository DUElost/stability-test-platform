from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    arguments = parser.parse_args(argv)
    report = validate_config_file(arguments.config)
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']} stage=validate: {report['summary']}")
        for check in report["checks"] + report["deferred_checks"]:
            print(f"{check['status']} {check['check_id']} role={check['role']} {check['location']} [{check['code']}]")
            print(f"  {check['message']} {check['remediation']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
