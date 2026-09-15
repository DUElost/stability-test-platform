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

    def ensure_plain_dir(self, path) -> None:
        # 记录调用：测试要证明存储准备**不**使用会递归 chown 的 ensure_dir
        self.calls.append(("ensure_plain_dir", str(path)))
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
    # #2197：监控栈默认装——装了存储却没有监控栈的站点，/storage 页永远是空的
    assert data["monitoring"] == {"enabled": True, "prometheus_port": 9091}
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


def test_prepare_storage_never_chowns_or_remounts_an_existing_mount(tmp_path, monkeypatch):
    """重跑时 /srv/hdd 已是挂载点：既不重复 mount，也绝不递归改属主。

    `ensure_dir` 末尾是 `chown -R root:root`——用在挂载点上会把整盘既有数据
    （238 上是 71.5G 的 aee_events，属主 uid 1000）静默改成 root。
    """
    from tools.site_config import bootstrap

    fstab = tmp_path / "fstab"
    fstab.write_text("", encoding="utf-8")
    host_mount = tmp_path / "srv/hdd"
    host_mount.mkdir(parents=True)
    (host_mount / "existing-data").mkdir()
    monkeypatch.setattr(bootstrap, "HOST_MOUNT", str(host_mount))
    monkeypatch.setattr(bootstrap, "FSTAB", fstab)
    monkeypatch.setattr(bootstrap, "_mounted", lambda target: True)

    ops = probe_ops(responses={"blkid -s UUID": (0, "1234-abcd\n")})
    actions, _ = bootstrap.prepare_storage(
        ops, disk="/dev/sdb", mount_path=str(tmp_path / "srv/stp-aee"),
        subdir="city-b/aee_events", dry_run=False, fix=True,
    )

    invoked = [call[0] for call in ops.calls if call]
    assert "chown" not in invoked
    assert "mount" not in invoked, "已是挂载点：不该重复挂载"
    assert ("ensure_plain_dir", str(host_mount)) in ops.calls
    # 既有数据目录原样保留（没有被复制、移动或删除）
    assert (host_mount / "existing-data").is_dir()
    assert any("bound" in line or "already" in line or "fstab" in line for line in actions) or actions == []


def test_local_ops_plain_dir_keeps_ownership_and_mode(tmp_path):
    """真实实现：只创建目录，不 chmod/chown（属主保持调用者）。"""
    from tools.site_config.ops import LocalOps

    target = tmp_path / "nested/keep-me"
    LocalOps().ensure_plain_dir(target)
    assert target.is_dir()
    assert os.stat(target).st_uid == os.getuid()
    # 既存目录不被改动
    existing = tmp_path / "existing"
    existing.mkdir(mode=0o750)
    before = os.stat(existing)
    LocalOps().ensure_plain_dir(existing)
    after = os.stat(existing)
    assert (before.st_uid, before.st_gid, stat.S_IMODE(before.st_mode)) == (
        after.st_uid, after.st_gid, stat.S_IMODE(after.st_mode),
    )


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


def _role_ops(role_present: bool = True):
    responses = {"pg_roles": (0, "1\n" if role_present else ""), "pg_database": (0, "1\n")}
    return probe_ops(responses=responses)


def test_existing_role_with_another_password_fails_closed():
    """既有角色密码与本次生成不一致：绝不静默改既有角色，也不写坏绑定。"""
    from tools.site_config import bootstrap

    with pytest.raises(BootstrapError) as caught:
        bootstrap.prepare_database(
            _role_ops(), database="stp_b", role="stp", password="generated-1",
            dry_run=False, fix=True, role_probe=lambda ops, dsn, password: False,
        )
    assert caught.value.code == "bootstrap_database_role"
    assert "different password" in caught.value.detail


def test_existing_role_is_reset_only_when_explicitly_allowed():
    from tools.site_config import bootstrap

    ops = _role_ops()
    actions, _ = bootstrap.prepare_database(
        ops, database="stp_b", role="stp", password="generated-1",
        dry_run=False, fix=True, reset_password=True,
    )
    assert any("reset password for existing role: stp" in line for line in actions)
    joined = " | ".join(" ".join(call) for call in ops.calls if call)
    assert "ALTER ROLE stp LOGIN PASSWORD" in joined


def test_existing_role_matching_the_binding_needs_no_change():
    from tools.site_config import bootstrap

    ops = _role_ops()
    actions, _ = bootstrap.prepare_database(
        ops, database="stp_b", role="stp", password="generated-1",
        dry_run=False, fix=True, role_probe=lambda ops, dsn, password: True,
    )
    assert any("role already exists: stp" in line for line in actions)
    assert not any("ALTER ROLE" in " ".join(call) for call in ops.calls if call)


def test_rerun_keeps_existing_bindings_instead_of_rotating_them(tmp_path):
    """重跑 init 不得轮换站点口令/Fernet/DSN——S2 已把首次值渲染进站点 env。"""
    output = tmp_path / "site.yaml"
    bindings = tmp_path / "bindings"
    first = init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    before = {name: (bindings / name).read_text(encoding="utf-8") for name in sorted(os.listdir(bindings))}
    fresh = init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    after = {name: (bindings / name).read_text(encoding="utf-8") for name in sorted(os.listdir(bindings))}
    assert before == after
    assert any("kept existing bindings (not rotated)" in line for line in fresh["actions"])
    assert any("kept existing bindings (not rotated)" in line for line in first["actions"]) is False


def test_rerun_keeps_the_existing_values_even_when_parameters_differ(tmp_path):
    """站点已在运行：既有绑定（口令/索引/DSN）优先于本次命令行参数。"""
    output = tmp_path / "site.yaml"
    bindings = tmp_path / "bindings"
    init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(), redis_index=2, admin_username="ops",
    )
    before = {name: (bindings / name).read_text(encoding="utf-8") for name in sorted(os.listdir(bindings))}

    fresh = init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(), redis_index=1, admin_username="admin",
    )

    after = {name: (bindings / name).read_text(encoding="utf-8") for name in sorted(os.listdir(bindings))}
    assert before == after
    assert before["site_redis"].strip().endswith("/2")
    assert "USERNAME=ops" in before["site_admin"]
    assert any("kept existing bindings (not rotated)" in line for line in fresh["actions"])


def test_rerun_probes_the_database_with_the_existing_password(tmp_path, monkeypatch):
    """重跑必须拿既有绑定的密码去核对，而不是新生成的——否则会白白 ALTER 角色。"""
    from tools.site_config import bootstrap

    output = tmp_path / "site.yaml"
    bindings = tmp_path / "bindings"
    init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    existing_dsn = bootstrap._read_binding(bindings, "site_database")["DATABASE_URL"]
    existing_password = bootstrap._dsn_password(existing_dsn)

    captured: dict = {}
    real = bootstrap.prepare_database

    def spy(ops, **kwargs):
        captured.update(kwargs)
        return real(ops, **kwargs)

    monkeypatch.setattr(bootstrap, "prepare_database", spy)
    init_site(
        output=output, bindings_dir=bindings, ops=probe_ops(), interactive=False,
        fix=False, answers=_answers(),
    )
    assert captured["password"] == existing_password
    assert captured["dsn"].endswith("/stp_b")
