from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
import yaml

from tools.site_config.bootstrap import (
    DEFAULT_DB_ROLE,
    BootstrapError,
    init_site,
    probe_data_disk,
)
from tools.site_config.ops import CommandResult
from tools.site_config.validation import load_site_config

REPO_ROOT = Path(__file__).resolve().parents[1]


class FakeOps:
    """探测替身：lsblk/blkid/timedatectl 走响应表，其余命令都成功返回空。"""

    def __init__(
        self,
        *,
        hostname: str = "city-b.synthetic.invalid",
        addresses: set[str] | None = None,
        responses: dict[str, tuple[int, str]] | None = None,
    ):
        self.calls: list[tuple[str, ...]] = []
        self._hostname = hostname
        self._addresses = set(addresses or {"127.0.0.1", "192.0.2.1"})
        self._responses = responses or {}

    def run(self, argv, *, env=None, input_text=None, cwd=None):
        argv = tuple(str(item) for item in argv)
        self.calls.append(argv)
        joined = " ".join(argv)
        for needle, (code, output) in self._responses.items():
            if needle in joined:
                return CommandResult(argv, code, output)
        return CommandResult(argv, 0, "")

    def hostname(self) -> str:
        return self._hostname

    def local_addresses(self) -> set[str]:
        return set(self._addresses)

    def route_address(self) -> str:
        return ""

    def os_release(self) -> dict[str, str]:
        return {"ID": "debian", "VERSION_ID": "13"}

    def machine(self) -> str:
        return "x86_64"

    def ensure_dir(self, path, mode: int, owner: str) -> None:
        Path(path).mkdir(parents=True, exist_ok=True)


LSBLK = (
    'NAME="sda" TYPE="disk" SIZE="1000204886016" MOUNTPOINT="" PKNAME="" RO="0"\n'
    'NAME="sda1" TYPE="part" SIZE="1000203091968" MOUNTPOINT="/mnt/stp-aee" PKNAME="sda" RO="0"\n'
    'NAME="sdb" TYPE="disk" SIZE="2000398934016" MOUNTPOINT="" PKNAME="" RO="0"\n'
)


def probe_ops(**overrides) -> FakeOps:
    # 根文件系统所在盘有分区（sda1 已挂载）——绝不能被当成「可用的空盘」
    defaults = dict(responses={
        "lsblk": (0, LSBLK),
        "blkid -s TYPE -o value /dev/sdb": (0, "ext4\n"),
    })
    defaults.update(overrides)
    return FakeOps(**defaults)


def test_data_disk_probe_skips_disks_that_hold_partitions():
    """带分区的盘（含生产 AEE 盘）绝不能被建议为可挂载整盘。"""
    assert probe_data_disk(probe_ops()) == ("/dev/sdb", 1863)


def test_data_disk_probe_skips_disks_without_a_filesystem():
    ops = probe_ops(responses={"lsblk": (0, LSBLK)})
    assert probe_data_disk(ops) == ("", 0)


def test_data_disk_probe_skips_read_only_and_partitioned_only():
    ops = probe_ops(responses={"lsblk": (0, LSBLK.replace('NAME="sdb" TYPE="disk" SIZE="2000398934016" MOUNTPOINT="" PKNAME="" RO="0"',
                                                              'NAME="sdb" TYPE="disk" SIZE="2000398934016" MOUNTPOINT="" PKNAME="" RO="1"'))})
    assert probe_data_disk(ops) == ("", 0)


def _answers(storage_mount: object = "/srv/stp-aee") -> dict[str, str]:
    return {
        "site_id": "city-b",
        "display_name": "B 市站点",
        "public_url": "http://192.0.2.1",
        "database": "stp_b",
        "storage_mount": str(storage_mount),
        "contact": "ops@example.invalid",
        "documentation_url": "https://docs.example.invalid/site-ops",
    }


def sandbox_answers(tmp_path: Path, monkeypatch) -> dict[str, str]:
    """把数据盘落点与存储路径都指到 tmp_path：测试不得写宿主机。"""
    from tools.site_config import bootstrap

    monkeypatch.setattr(bootstrap, "HOST_MOUNT", str(tmp_path / "srv/hdd"))
    return _answers(tmp_path / "srv/stp-aee")


def test_init_generates_a_config_that_validates(tmp_path):
    output = tmp_path / "site.yaml"
    bindings = tmp_path / "bindings"
    report = init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    assert report["status"] == "PASS"
    config = load_site_config(output)
    assert config.site.id == "city-b"
    assert config.agents == []
    assert config.control_plane.ssh_user is None
    assert config.storage.provisioning == "local_mount"
    assert config.storage.target is None
    assert config.dependencies.database_ref == "site_database"
    # --no-fix：宿主机写操作只报命令，不执行
    assert any(line.startswith("would run:") for line in report["actions"])
    assert not any(call[:1] == ("mount",) for call in probe_ops().calls)


def test_init_binds_the_declared_disk_subtree(tmp_path):
    disk_ops = probe_ops()
    report = init_site(
        output=tmp_path / "site.yaml", bindings_dir=tmp_path / "bindings",
        ops=disk_ops, interactive=False, fix=False, answers=_answers(),
    )
    joined = " | ".join(report["actions"])
    assert "/dev/sdb" in joined
    assert "city-b/aee_events" in joined
    # --no-fix 下连 mount 命令都不执行
    assert not any(call and call[0] == "mount" for call in disk_ops.calls)


def test_init_records_the_probe_provenance_in_the_file(tmp_path):
    output = tmp_path / "site.yaml"
    report = init_site(
        output=output, bindings_dir=tmp_path / "bindings", ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    text = output.read_text(encoding="utf-8")
    assert text.startswith("# 由 `python -m tools.site_config init` 生成")
    assert "探测：" in text
    assert "city-b.synthetic.invalid" in text
    assert report["provenance"]["site_id"] == "answer"


def test_init_derives_defaults_without_asking(tmp_path):
    """不给任何答案：站点标识、入口、库名、存储路径全部取探测值。"""
    output = tmp_path / "site.yaml"
    report = init_site(
        output=output, bindings_dir=tmp_path / "bindings", ops=probe_ops(), interactive=False,
        fix=False,
    )
    data = yaml.safe_load(output.read_text(encoding="utf-8").split("\n\n", 1)[1])
    assert data["site"]["id"] == "city-b"
    assert data["control_plane"]["target"] == "city-b.synthetic.invalid"
    assert data["control_plane"]["public_url"].startswith("http://192.0.2.1")
    assert data["storage"]["mount_path"] == "/srv/stp-aee"
    assert data["release"]["bundle"] == "/srv/stp-bundle"
    assert report["provenance"]["site_id"] == "default"


def test_init_writes_secrets_owner_only_and_never_prints_them(tmp_path):
    bindings = tmp_path / "bindings"
    report = init_site(
        output=tmp_path / "site.yaml", bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    assert stat.S_IMODE(os.lstat(bindings).st_mode) == 0o700
    expected = {"site_database", "site_redis", "site_admin", "site_ssh_encryption"}
    written = {path.name for path in bindings.iterdir()}
    assert written == expected
    for name in expected:
        assert stat.S_IMODE(os.lstat(bindings / name).st_mode) == 0o600
    admin = (bindings / "site_admin").read_text(encoding="utf-8")
    password = admin.split("PASSWORD=", 1)[1].strip()
    assert password
    serialized = json.dumps(report, ensure_ascii=False)
    assert password not in serialized
    assert password not in (tmp_path / "site.yaml").read_text(encoding="utf-8")
    # 口令只在绑定文件里出现一次：报告只给出路径
    assert report["admin_credentials"]["password_file"].endswith("site_admin")
    assert "PASSWORD" not in report["admin_credentials"]
    database = (bindings / "site_database").read_text(encoding="utf-8")
    assert database.startswith("DATABASE_URL=postgresql+psycopg://")
    assert "stp_b" in database
    redis = (bindings / "site_redis").read_text(encoding="utf-8")
    assert redis.strip().endswith("/1")


def test_init_dry_run_writes_nothing(tmp_path):
    output = tmp_path / "site.yaml"
    bindings = tmp_path / "bindings"
    ops = probe_ops()
    report = init_site(
        output=output, bindings_dir=bindings, ops=ops, interactive=False, fix=True,
        dry_run=True, answers=_answers(),
    )
    assert report["dry_run"] is True
    assert not output.exists()
    assert not bindings.exists()
    # 探测之外不得有任何写命令（venv 创建 / 建库 / 挂盘都要退化成报告）
    for call in ops.calls:
        assert not ({"venv", "createdb", "mount"} & set(call)), call


def test_init_database_actions_are_idempotent(tmp_path):
    ops = probe_ops(responses={
        "pg_roles": (0, ""),
        "pg_database": (0, ""),
    })
    actions, dsn = init_site(
        output=tmp_path / "site.yaml", bindings_dir=tmp_path / "bindings", ops=ops,
        interactive=False, fix=True, dry_run=False, answers=_answers(),
    )["actions"], None
    del dsn
    assert any("created role" in line or "would run" in line for line in actions)


def test_prepare_database_reports_failures_with_a_bootstrap_code(tmp_path):
    from tools.site_config.bootstrap import prepare_database

    ops = probe_ops(responses={"pg_roles": (1, "no connection")})
    with pytest.raises(BootstrapError) as caught:
        prepare_database(
            ops, database="stp_b", role=DEFAULT_DB_ROLE, password="secret",
            dry_run=False, fix=True,
        )
    assert caught.value.code == "bootstrap_database"


def test_prepare_storage_mounts_and_binds_with_fstab_entries(tmp_path, monkeypatch):
    """挂盘 + bind + fstab 是三条独立的写操作，缺一条重启后站点就找不到存储了。"""
    from tools.site_config import bootstrap

    fstab = tmp_path / "fstab"
    fstab.write_text("", encoding="utf-8")
    host_mount = tmp_path / "srv/hdd"
    monkeypatch.setattr(bootstrap, "HOST_MOUNT", str(host_mount))
    monkeypatch.setattr(bootstrap, "FSTAB", fstab)
    monkeypatch.setattr(bootstrap, "_mounted", lambda target: False)
    ops = probe_ops(responses={"blkid -s UUID": (0, "1234-abcd\n")})
    actions, mount_path = bootstrap.prepare_storage(
        ops, disk="/dev/sdb", mount_path=str(tmp_path / "srv/stp-aee"),
        subdir="city-b/aee_events", dry_run=False, fix=True,
    )
    assert mount_path == str(tmp_path / "srv/stp-aee")
    assert any("mounted /dev/sdb" in line for line in actions)
    assert any("bound" in line for line in actions)
    assert (host_mount / "city-b/aee_events").is_dir()
    text = fstab.read_text(encoding="utf-8")
    assert f"UUID=1234-abcd {host_mount} ext4 defaults,nofail 0 2" in text
    assert f"{host_mount}/city-b/aee_events {mount_path} none bind,nofail 0 0" in text


def test_prepare_storage_never_touches_the_host_in_report_mode(tmp_path, monkeypatch):
    from tools.site_config import bootstrap

    monkeypatch.setattr(bootstrap, "HOST_MOUNT", str(tmp_path / "srv/hdd"))
    ops = probe_ops()
    actions, _ = bootstrap.prepare_storage(
        ops, disk="/dev/sdb", mount_path=str(tmp_path / "srv/stp-aee"),
        subdir="city-b/aee_events", dry_run=False, fix=False,
    )
    assert all(line.startswith("would run:") for line in actions)
    assert not any(call and call[0] in {"mount", "chown"} for call in ops.calls)
    assert not (tmp_path / "srv").exists()


def test_init_report_lists_every_host_action_for_the_operator(tmp_path, monkeypatch):
    ops = probe_ops()
    report = init_site(
        output=tmp_path / "site.yaml", bindings_dir=tmp_path / "bindings", ops=ops,
        interactive=False, fix=True, answers=sandbox_answers(tmp_path, monkeypatch),
    )
    actions = report["actions"]
    assert any("tool venv" in line for line in actions)
    assert any("CREATE DATABASE" in line or "created empty database" in line for line in actions)


def test_init_without_data_disk_says_why_the_mount_must_exist(tmp_path):
    ops = probe_ops(responses={"lsblk": (0, "")})
    report = init_site(
        output=tmp_path / "site.yaml", bindings_dir=tmp_path / "bindings", ops=ops,
        interactive=False, fix=False, answers=_answers(),
    )
    assert any("no separate data disk" in line for line in report["actions"])
