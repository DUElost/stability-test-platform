from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .handover import run_handover
from .install import run_install
from .plan import plan_site_report
from .validation import validate_config_file
from .verify import verify_site


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
    install = commands.add_parser(
        "install",
        help="Run the local site install stages (S0–S4) on this declared target; never inherits installer-local defaults.",
    )
    install.add_argument("--config", type=Path, required=True, help="Explicit regular UTF-8 YAML input; no environment fallback.")
    install.add_argument("--bindings-dir", type=Path, required=True, help="Owner-only directory (0700) holding 0600 binding files.")
    install.add_argument("--state-dir", type=Path, required=True, help="Owner-only directory (0700) for the install state file.")
    install.add_argument("--confirm-site", required=True, help="Must equal site.id; guards against running for the wrong site.")
    install.add_argument("--confirm-target", required=True, help="Must equal control_plane.target; guards against the wrong host.")
    install.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    install.add_argument("--dry-run", action="store_true", help="Verify and plan only; no writes and no service changes.")
    install.add_argument(
        "--through-agents",
        action="store_true",
        help="After S4, onboard the declared Agents through this site's own API (S5); off by default.",
    )
    verify = commands.add_parser(
        "verify",
        help="S6 acceptance probes against the installed site (login/CSRF, hosts, devices, controlled noop chain).",
    )
    verify.add_argument("--config", type=Path, required=True, help="Explicit regular UTF-8 YAML input; no environment fallback.")
    verify.add_argument("--bindings-dir", type=Path, required=True, help="Owner-only directory (0700) holding 0600 binding files.")
    verify.add_argument(
        "--device-serial",
        default=None,
        help="Pin the authorized acceptance device; without it the first ONLINE device is used.",
    )
    verify.add_argument(
        "--run-timeout",
        type=float,
        default=900.0,
        help="Seconds to wait for the controlled run to reach a terminal state (default 900).",
    )
    verify.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    handover = commands.add_parser(
        "handover",
        help="Map P1 acceptance items (MS-01/02/04/05/06/10/13) to this site's install/verify evidence.",
    )
    handover.add_argument("--config", type=Path, required=True, help="Explicit regular UTF-8 YAML input; no environment fallback.")
    handover.add_argument("--state-dir", type=Path, required=True, help="Owner-only directory (0700) holding the install state file.")
    handover.add_argument(
        "--verify-report",
        type=Path,
        default=None,
        help="verify --json output; without it every verify-backed item stays BLOCKED.",
    )
    handover.add_argument("--dry-run", action="store_true", help="Report only; write nothing.")
    handover.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    arguments = parser.parse_args(argv)
    if arguments.command == "validate":
        report = validate_config_file(arguments.config)
    elif arguments.command == "plan":
        report = plan_site_report(arguments.config, save_dir=arguments.save_dir)
    elif arguments.command == "handover":
        report = run_handover(
            arguments.config,
            state_dir=arguments.state_dir,
            verify_report=arguments.verify_report,
            dry_run=arguments.dry_run,
        )
    elif arguments.command == "verify":
        report = verify_site(
            arguments.config,
            bindings_dir=arguments.bindings_dir,
            device_serial=arguments.device_serial,
            run_timeout=arguments.run_timeout,
        )
    else:
        report = run_install(
            arguments.config,
            bindings_dir=arguments.bindings_dir,
            state_dir=arguments.state_dir,
            confirm_site=arguments.confirm_site,
            confirm_target=arguments.confirm_target,
            dry_run=arguments.dry_run,
            through_agents=arguments.through_agents,
        )
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']} stage={report['stage']}: {report['summary']}")
        for check in report["checks"] + report["deferred_checks"]:
            print(f"{check['status']} {check['check_id']} role={check['role']} {check['location']} [{check['code']}]")
            print(f"  {check['message']} {check['remediation']}")
        if report.get("saved_file"):
            label = "Handover file" if report.get("stage") == "handover" else "Plan report"
            print(f"{label} written: {report['saved_file']}")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
