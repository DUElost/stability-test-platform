"""I5：站点导航页（S7）与交接证据清单（handover）。

导航页与交接文件的共同约束：只发布获准信息（站点身份/入口/负责人/文档/发布），
不含凭据、秘密名、内部地址与路径；缺证据的 MS 条目如实 BLOCKED。
"""

from __future__ import annotations

import json
import re
import stat
from pathlib import Path

import yaml

from tools.site_config import stages
from tools.site_config import handover as handover_module
from tools.site_config.handover import ACCEPTANCE_ITEMS, run_handover
from tools.site_config.stages import (
    NAVIGATION_PLACEHOLDERS,
    render_navigation_page,
)
from tools.site_config.verify import check_navigation

PRIVATE_MARKER = "DO_NOT_ECHO_PRIVATE_INPUT_9374"
PUBLIC_URL = "https://site-i5.synthetic.invalid"
CONTACT = "site-owner <ops@example.invalid>"
NAV_TEMPLATE = Path(stages.__file__).resolve().parents[2] / stages.NAVIGATION_TEMPLATE
#: 工具源码里的 check_id 字面量（守卫测试用；见 TestAcceptanceMappingGuard）。
_CHECK_ID_LITERAL = re.compile(r"""["']((?:install|verify)\.[a-z0-9_.]+)["']""")


class StubConfig:
    """仅承载导航/交接需要的字段（避免整份 SiteConfig 夹具）。"""

    class _Site:
        id = "lab-i5"
        display_name = "合成站点 I5"

    class _Cp:
        public_url = PUBLIC_URL

    class _Nav:
        contact = CONTACT
        documentation_url = "https://docs.synthetic.invalid/ops"

    class _Release:
        expected_release = "i5-lab-2026.09.15"

    site = _Site()
    control_plane = _Cp()
    navigation = _Nav()
    release = _Release()


class StubCtx:
    def __init__(self, system_root: Path):
        self.config = StubConfig()
        self.system_root = system_root


def _site_yaml(tmp_path: Path) -> Path:
    data = {
        "schema_version": 1,
        "site": {"id": "lab-i5", "display_name": "合成站点 I5", "timezone": "Asia/Shanghai"},
        "platform": {"os_family": "linux", "cpu_arch": "x86_64", "service_manager": "systemd"},
        "network": {"dependency_mode": "controlled_mirror"},
        "control_plane": {
            "target": "control-i5.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "control_ssh",
            "deploy_root": str(tmp_path / "opt/stp-control"),
            "deploy_user": "stp",
            "public_url": PUBLIC_URL,
            "security_profile": "production",
            "tls_ref": "site_tls",
        },
        "storage": {
            "provisioning": "existing_share",
            "protocol": "nfs",
            "target": "storage-i5.synthetic.invalid",
            "os": None,
            "ssh_user": None,
            "ssh_credential_ref": None,
            "share": "/srv/stp-export",
            "credential_ref": None,
            "mount_path": str(tmp_path / "mnt/share"),
        },
        "agents": [{
            "key": "agent-01",
            "target": "agent-01.synthetic.invalid",
            "os": {"distribution": "debian", "version": "13"},
            "ssh_user": "bootstrap",
            "ssh_credential_ref": "agent_01_ssh",
            "install_root": str(tmp_path / "opt/stp-agent"),
            "local_aee_root": str(tmp_path / "var/stp-aee"),
        }],
        "dependencies": {"database_ref": "site_database", "redis_ref": "site_redis", "tools_profile": "synthetic"},
        "security": {
            "jwt_key_ref": "site_jwt",
            "agent_secret_ref": "site_agent_secret",
            "ssh_encryption_key_ref": "site_ssh_encryption",
            "initial_admin_ref": "site_admin",
        },
        "release": {
            "bundle": str(tmp_path / "bundle"),
            "manifest": str(tmp_path / "bundle/release-manifest.json"),
            "expected_release": "i5-lab-2026.09.15",
        },
        "navigation": {
            "contact": CONTACT,
            "documentation_url": "https://docs.synthetic.invalid/ops",
        },
    }
    path = tmp_path / "site.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def _slot_ids(slots: tuple[str | tuple[str, ...], ...]) -> set[str]:
    """展开证据槽位（#2404 起槽位可为「任一命中」候选集）。"""
    ids: set[str] = set()
    for slot in slots:
        ids.update((slot,) if isinstance(slot, str) else slot)
    return ids


def _stage_ids() -> set[str]:
    return {cid for item in ACCEPTANCE_ITEMS for cid in _slot_ids(item.stage_checks)}


def _verify_ids() -> set[str]:
    return {cid for item in ACCEPTANCE_ITEMS for cid in _slot_ids(item.verify_checks)}


def _state_dir(
    tmp_path: Path, *, runs: int = 2, site_id: str = "lab-i5", omit: tuple[str, ...] = (),
) -> Path:
    """安装记录夹具：把 ACCEPTANCE_ITEMS 需要的 stage check 全部记成 PASS。

    ``omit`` 用于精确用例：例如只留 S3 迁移候选中的一条（#2404 的双路径证据）。
    """
    required = sorted(_stage_ids() - set(omit))
    state_dir = tmp_path / "state"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    (state_dir / "install-state.json").write_text(json.dumps({
        "site_id": site_id,
        "target": "control-i5.synthetic.invalid",
        "release": "i5-lab-2026.09.15",
        "runs": runs,
        "stages": [{"stage": "S0", "status": "PASS", "checks": required}],
    }), encoding="utf-8")
    return state_dir


def _verify_report(tmp_path: Path, *, status: str = "PASS", failing: str | None = None) -> Path:
    check_ids = sorted(_verify_ids())
    checks = [
        {
            "check_id": cid, "role": "site", "status": "FAIL" if cid == failing else status,
            "location": "$", "code": "synthetic", "message": "synthetic", "remediation": "synthetic",
        }
        for cid in check_ids
    ]
    path = tmp_path / "verify.json"
    path.write_text(json.dumps({"stage": "verify", "status": "PASS", "checks": checks}), encoding="utf-8")
    return path


def _codes(report: dict) -> list[str]:
    return [check["code"] for check in report["checks"]]


def _status(report: dict, check_id: str) -> str:
    return next(check["status"] for check in report["checks"] if check["check_id"] == check_id)


class TestNavigationPage:
    def test_renders_only_approved_fields(self, tmp_path):
        ctx = StubCtx(tmp_path)
        page = render_navigation_page(NAV_TEMPLATE.read_text(encoding="utf-8"), ctx)

        assert page is not None
        assert "lab-i5" in page and "合成站点 I5" in page
        assert PUBLIC_URL in page and "https://docs.synthetic.invalid/ops" in page
        assert "handover.json" in page
        # 负责人里的尖括号必须被转义，否则会破坏页面结构
        assert "site-owner &lt;ops@example.invalid&gt;" in page
        for placeholder in NAVIGATION_PLACEHOLDERS:
            assert placeholder not in page

    def test_page_has_no_paths_or_secrets_slots(self, tmp_path):
        page = render_navigation_page(NAV_TEMPLATE.read_text(encoding="utf-8"), StubCtx(tmp_path))

        for forbidden in ("<deploy-root>", "/opt/", "/home/", "PRIVATE", "password", "SECRET"):
            assert forbidden not in page

    def test_leftover_placeholder_fails_closed(self, tmp_path):
        # 模板若出现契约外的 kebab-case 占位符，渲染必须拒绝出物
        template = NAV_TEMPLATE.read_text(encoding="utf-8").replace(
            "<site-id>", "<site-id><site-unknown-field>",
        )
        assert render_navigation_page(template, StubCtx(tmp_path)) is None


class TestHandover:
    def test_all_evidence_present(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path),
            state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path),
            system_root=tmp_path,
        )

        assert report["status"] == "PASS", report["checks"]
        assert report["saved_file"] == "handover.json"
        artifact = tmp_path / "var/www/stability-site/handover.json"
        payload = json.loads(artifact.read_text(encoding="utf-8"))
        assert payload["site_id"] == "lab-i5"
        assert payload["runs"] == 2
        assert [item["item"] for item in payload["acceptance"]] == [
            item.key for item in ACCEPTANCE_ITEMS
        ]
        assert all(item["status"] == "PASS" for item in payload["acceptance"])
        # 世界可读（nginx 以非特权用户提供该文件），但不是 0666
        assert stat.S_IMODE(artifact.stat().st_mode) == 0o644

    def test_pending_items_are_stated(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path), system_root=tmp_path,
        )
        artifact = json.loads(
            (tmp_path / "var/www/stability-site/handover.json").read_text(encoding="utf-8")
        )
        pending = " ".join(item["pending"] for item in artifact["acceptance"])
        assert "真机" in pending
        assert report["status"] == "PASS"

    def test_first_run_leaves_idempotency_item_blocked(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path, runs=1),
            verify_report=_verify_report(tmp_path), system_root=tmp_path,
        )

        assert _status(report, "handover.MS-04") == "BLOCKED"
        assert _codes(report).count("FAIL") == 0

    def test_blocked_verify_evidence_keeps_the_item_blocked(self, tmp_path):
        """#2283：BLOCKED 证据是**正常验收结果**——不判 FAIL，也不因此不写文件。

        此前凡非 PASS 即 failed → 整份 handover 判 FAIL 且不写文件，而 /site/ 导航页
        有指向 handover.json 的固定链接 → 404。
        """
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path, status="BLOCKED"), system_root=tmp_path,
        )

        assert report["status"] == "PASS", _codes(report)
        assert _status(report, "handover.MS-06") == "BLOCKED"
        assert "evidence_blocked" in _codes(report)
        assert report["saved_file"], "BLOCKED 不应阻断写文件"

    def test_missing_verify_report_leaves_verify_items_blocked(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path), system_root=tmp_path,
        )

        assert _status(report, "handover.MS-06") == "BLOCKED"
        assert _status(report, "handover.MS-13") == "BLOCKED"
        assert _status(report, "handover.MS-01") in {"PASS", "BLOCKED"}

    # ── #2404：MS-01 的 S3 证据在两条互斥路径上发出不同 ID ──────────────────

    def test_ms01_passes_on_schema_at_head_evidence(self, tmp_path):
        """已装站点幂等重跑：S3 发 `install.s3.db`(schema_at_head)、不发 migrate。

        238 现场即此形态——修前 MS-01 因固定要求 `install.s3.migrate` 而永远 BLOCKED。
        """
        report = run_handover(
            _site_yaml(tmp_path),
            state_dir=_state_dir(tmp_path, omit=("install.s3.migrate",)),
            verify_report=_verify_report(tmp_path),
            system_root=tmp_path,
        )

        assert _status(report, "handover.MS-01") == "PASS", report["checks"]
        message = next(
            str(check.get("message") or "")
            for check in report["checks"] if check["check_id"] == "handover.MS-01"
        )
        assert "install.s3.db(install:PASS)" in message, message

    def test_ms01_passes_on_migration_applied_evidence(self, tmp_path):
        """首装/带迁移的运行：S3 发 `install.s3.migrate`、不发 db 的 at_head 证据。"""
        report = run_handover(
            _site_yaml(tmp_path),
            state_dir=_state_dir(tmp_path, omit=("install.s3.db",)),
            verify_report=_verify_report(tmp_path),
            system_root=tmp_path,
        )

        assert _status(report, "handover.MS-01") == "PASS", report["checks"]

    def test_ms01_missing_both_migration_evidences_is_blocked(self, tmp_path):
        """两条都没有 → 如实 BLOCKED，且缺失文案给出 `A or B`（不只报一半）。"""
        report = run_handover(
            _site_yaml(tmp_path),
            state_dir=_state_dir(tmp_path, omit=("install.s3.db", "install.s3.migrate")),
            verify_report=_verify_report(tmp_path),
            system_root=tmp_path,
        )

        assert _status(report, "handover.MS-01") == "BLOCKED"
        message = next(
            str(check.get("message") or "")
            for check in report["checks"] if check["check_id"] == "handover.MS-01"
        )
        assert "install.s3.db or install.s3.migrate" in message, message

    def test_failing_mapped_check_fails_the_item(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path, failing="verify.s6.chain"),
            system_root=tmp_path,
        )

        assert report["status"] == "FAIL"
        assert _status(report, "handover.MS-06") == "FAIL"
        assert "evidence_failed" in _codes(report)
        # FAIL 时不出物
        assert report["saved_file"] is None
        assert not (tmp_path / "var/www/stability-site/handover.json").exists()

    def test_missing_state_is_fail_closed(self, tmp_path):
        report = run_handover(_site_yaml(tmp_path), state_dir=tmp_path / "state", system_root=tmp_path)

        assert report["status"] == "FAIL"
        assert "install_state" in _codes(report)

    def test_foreign_state_is_rejected(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path, site_id="other-site"),
            system_root=tmp_path,
        )

        assert report["status"] == "FAIL"
        assert "install_conflict" in _codes(report)

    def test_broken_verify_report_is_rejected(self, tmp_path):
        broken = tmp_path / "verify.json"
        broken.write_text("{not json", encoding="utf-8")
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path), verify_report=broken,
            system_root=tmp_path,
        )

        assert report["status"] == "FAIL"
        assert "verify_report" in _codes(report)

    def test_dry_run_writes_nothing(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path), dry_run=True, system_root=tmp_path,
        )

        assert report["status"] == "PASS"
        assert report["saved_file"] is None
        assert not (tmp_path / "var/www/stability-site/handover.json").exists()

    def test_redaction_guard_blocks_publication(self, tmp_path, monkeypatch):
        """交接文件命中禁止片段时拒绝出物（宁可没有物，也不泄密）。"""
        import tools.site_config.handover as handover_module

        monkeypatch.setattr(handover_module, "_FORBIDDEN_FRAGMENTS", ("PASS",))
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path), system_root=tmp_path,
        )

        assert report["status"] == "FAIL"
        assert "handover_redaction" in _codes(report)
        assert not (tmp_path / "var/www/stability-site/handover.json").exists()

    def test_report_never_contains_binding_values(self, tmp_path):
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path), system_root=tmp_path,
        )
        rendered = json.dumps(report, ensure_ascii=False) + (
            (tmp_path / "var/www/stability-site/handover.json").read_text(encoding="utf-8")
            if (tmp_path / "var/www/stability-site/handover.json").exists() else ""
        )

        assert PRIVATE_MARKER not in rendered

    def test_cli_writes_handover_file(self, tmp_path, capsys, monkeypatch):
        """CLI 接线：默认目标根是 /，测试把站点目录重定向到 tmp。"""
        import tools.site_config.__main__ as cli
        import tools.site_config.handover as handover_module

        monkeypatch.setattr(handover_module, "NAVIGATION_SITE_DIR", str(tmp_path / "site"))
        state = _state_dir(tmp_path)
        code = cli.main([
            "handover", "--config", str(_site_yaml(tmp_path)),
            "--state-dir", str(state), "--verify-report", str(_verify_report(tmp_path)),
        ])

        assert code == 0
        assert "handover.json" in capsys.readouterr().out
        assert (tmp_path / "site/handover.json").is_file()

    def test_unwritable_site_directory_fails_closed(self, tmp_path):
        """目录不可写时给检查失败（不出物），而不是抛栈。"""
        report = run_handover(
            _site_yaml(tmp_path), state_dir=_state_dir(tmp_path),
            verify_report=_verify_report(tmp_path), system_root=Path("/proc"),
        )

        assert report["status"] == "FAIL"
        assert "handover_write" in _codes(report)


class _NavApi:
    def __init__(self, *, status: int = 200, text: str = ""):
        self.status = status
        self.text = text

    def fetch_navigation(self):
        if self.status == 200 and not self.text:
            self.text = (
                f"<html><body>lab-i5 合成站点 I5 site-owner &lt;ops@example.invalid&gt; "
                f"{PUBLIC_URL} https://docs.synthetic.invalid/ops handover.json</body></html>"
            )
        return self.status, self.text


class TestVerifyNavigationCheck:
    def test_published_entry_passes(self, tmp_path):
        check = check_navigation(_NavApi(), StubConfig())

        assert check.status == "PASS"
        assert check.code == "navigation_published"

    def test_missing_entry_is_fail_closed(self):
        check = check_navigation(_NavApi(status=404), StubConfig())

        assert check.status == "FAIL"
        assert check.code == "navigation_missing"

    def test_entry_without_site_owner_is_rejected(self):
        check = check_navigation(_NavApi(text="<html>lab-i5 only</html>"), StubConfig())

        assert check.status == "FAIL"
        assert check.code == "navigation_missing"


class TestAcceptanceMappingGuard:
    """#2404 防复发：映射里的证据 ID 必须真的会被工具发出。

    提取 `tools/site_config/*.py`（**排除 handover.py 自身**，否则是自指的）里所有
    `"install.*"` / `"verify.*"` 字面量作为 emitter 集合，逐一核对 ACCEPTANCE_ITEMS
    的槽位与候选——将来改 ID 时映射不会再悄悄指向不存在的证据。
    """

    def test_every_mapped_id_is_emitted_somewhere(self):
        tool_root = Path(handover_module.__file__).resolve().parent
        emitted: set[str] = set()
        for path in tool_root.glob("*.py"):
            if path.name == "handover.py":
                continue
            emitted.update(_CHECK_ID_LITERAL.findall(path.read_text(encoding="utf-8")))

        mapped = _stage_ids() | _verify_ids()
        assert mapped <= emitted, f"映射了不会被发出的 ID：{sorted(mapped - emitted)}"
