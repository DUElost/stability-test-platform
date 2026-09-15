from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# `preflight` 必须在裸机（还没装安装器依赖）上也能跑：只有它是模块级导入，
# 其余子命令在各自分支里延迟导入，避免把 pydantic/PyYAML 拉进 preflight 路径。
from .preflight import run_preflight


class RedactedParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, "Invalid arguments; use --help. Supplied argument values are not displayed.\n")


def main(argv: list[str] | None = None) -> int:
    parser = RedactedParser(prog="python -m tools.site_config", description="Offline site configuration checks only.")
    commands = parser.add_subparsers(dest="command", required=True)
    preflight = commands.add_parser(
        "preflight",
        help="Read-only host readiness probe; nothing is written and no configuration file is needed.",
    )
    preflight.add_argument("--bindings-dir", type=Path, default=None, help="Existing bindings directory to inspect (optional).")
    preflight.add_argument("--db-url", default=None, help="Empty database DSN to probe; without it that item stays BLOCKED.")
    preflight.add_argument("--redis-url", default=None, help="Redis URL (dedicated db index) to probe.")
    preflight.add_argument("--bundle", type=Path, default=None, help="Release bundle directory to check (optional).")
    preflight.add_argument("--deploy-root", type=Path, default=None, help="Declared deploy root; reports whether it is resumable.")
    preflight.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
    init = commands.add_parser(
        "init",
        help="Probe the host and write site.yaml + bindings from four answers; --fix also prepares venv/db/storage.",
    )
    init.add_argument("--output", type=Path, required=True, help="site.yaml to write (created with owner-only permissions).")
    init.add_argument("--bindings-dir", type=Path, required=True, help="Owner-only directory (0700) to hold the generated 0600 bindings.")
    init.add_argument("--site-id", default=None, help="Answer: site identifier (default city-b).")
    init.add_argument("--display-name", default=None, help="Answer: human-readable site name.")
    init.add_argument("--public-url", default=None, help="Answer: platform entry URL (default http://<primary address>).")
    init.add_argument("--database", default=None, help="Answer: database name on the local PostgreSQL instance.")
    init.add_argument("--redis-index", type=int, default=1, help="Redis db index dedicated to this site (default 1).")
    init.add_argument("--storage-mount", default=None, help="Answer: central storage mount path (default /srv/stp-aee).")
    init.add_argument("--data-disk", default=None, help="Whole data disk to mount and bind; default is the largest unmounted disk.")
    init.add_argument("--bundle", default=None, help="Release bundle path for this site (default /srv/stp-bundle).")
    init.add_argument("--admin-username", default="admin", help="Initial administrator name (default admin).")
    init.add_argument("--non-interactive", action="store_true", help="Take every default instead of prompting.")
    init.add_argument(
        "--reset-db-password",
        action="store_true",
        help="Allow ALTER ROLE when the role already exists with a different password (off by default).",
    )
    init.add_argument("--no-fix", dest="fix", action="store_false", help="Report the exact host commands instead of running them.")
    init.add_argument("--dry-run", action="store_true", help="Write nothing at all; report only.")
    init.add_argument("--json", action="store_true", help="Emit a redacted, machine-readable stage report.")
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
    install.add_argument(
        "--agents-inventory",
        type=Path,
        default=None,
        help="Ansible-style inventory whose hosts join the declared Agents before S5 (default ~/hosts.ini when --through-agents is used).",
    )
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
    if arguments.command == "preflight":
        report = run_preflight(
            bindings_dir=arguments.bindings_dir,
            db_url=arguments.db_url,
            redis_url=arguments.redis_url,
            bundle=arguments.bundle,
            deploy_root=arguments.deploy_root,
        )
    elif arguments.command == "init":
        from .bootstrap import init_site

        report = init_site(
            output=arguments.output,
            bindings_dir=arguments.bindings_dir,
            site_id=arguments.site_id,
            display_name=arguments.display_name,
            public_url=arguments.public_url,
            database=arguments.database,
            redis_index=arguments.redis_index,
            storage_mount=arguments.storage_mount,
            data_disk=arguments.data_disk,
            bundle=arguments.bundle,
            admin_username=arguments.admin_username,
            reset_db_password=arguments.reset_db_password,
            interactive=False if arguments.non_interactive else None,
            fix=arguments.fix,
            dry_run=arguments.dry_run,
        )
    elif arguments.command == "validate":
        from .validation import validate_config_file

        report = validate_config_file(arguments.config)
    elif arguments.command == "plan":
        from .plan import plan_site_report

        report = plan_site_report(arguments.config, save_dir=arguments.save_dir)
    elif arguments.command == "handover":
        from .handover import run_handover

        report = run_handover(
            arguments.config,
            state_dir=arguments.state_dir,
            verify_report=arguments.verify_report,
            dry_run=arguments.dry_run,
        )
    elif arguments.command == "verify":
        from .verify import verify_site

        report = verify_site(
            arguments.config,
            bindings_dir=arguments.bindings_dir,
            device_serial=arguments.device_serial,
            run_timeout=arguments.run_timeout,
        )
    else:
        from .install import run_install

        report = run_install(
            arguments.config,
            bindings_dir=arguments.bindings_dir,
            state_dir=arguments.state_dir,
            confirm_site=arguments.confirm_site,
            confirm_target=arguments.confirm_target,
            dry_run=arguments.dry_run,
            through_agents=arguments.through_agents,
            agents_inventory=arguments.agents_inventory,
        )
    if arguments.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"{report['status']} stage={report['stage']}: {report['summary']}")
        for check in report["checks"] + report["deferred_checks"]:
            print(f"{check['status']} {check['check_id']} role={check['role']} {check['location']} [{check['code']}]")
            print(f"  {check['message']} {check['remediation']}")
        for line in report.get("actions", []):
            print(f"  action: {line}")
        if report.get("materialized_bindings"):
            print(f"  bindings written from inventory: {', '.join(report['materialized_bindings'])}")
        if report.get("stage") == "preflight" and report.get("suggested_public_url"):
            print(f"  suggested entry: {report['suggested_public_url']} (override with --public-url on init)")
        if report.get("saved_file"):
            label = "Handover file" if report.get("stage") == "handover" else "Plan report"
            print(f"{label} written: {report['saved_file']}")
        if report.get("stage") == "init" and report.get("status") == "PASS":
            credentials = report["admin_credentials"]
            print(f"Site inputs written: {report['output']}")
            print(f"Bindings written: {report['bindings_dir']}")
            print(f"Initial administrator '{credentials['username']}' password is in {credentials['password_file']} (0600).")
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
