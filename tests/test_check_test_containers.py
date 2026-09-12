"""#1482：残留 testcontainer 巡检工具（纯逻辑单测，不依赖 docker）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_spec = importlib.util.spec_from_file_location(
    "check_test_containers",
    REPO_ROOT / "tools" / "dev" / "check_test_containers.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
# dataclass 在 exec 期需要模块已在 sys.modules 中（否则解析 __module__ 失败）
sys.modules["check_test_containers"] = _mod
_spec.loader.exec_module(_mod)

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def _c(
    age_minutes: float, *, image: str = "postgres:16", name: str = "pg",
    labels: "dict | None" = None,
) -> _mod.Container:
    """默认带 testcontainers label（#1655 后 label 是权威判据）。"""
    if labels is None:
        labels = {"org.testcontainers": "true", "org.testcontainers.lang": "python"}
    return _mod.Container(
        cid=name[:12], name=name, image=image,
        created=NOW - timedelta(minutes=age_minutes), labels=labels,
    )


class TestParseDockerTime:
    def test_rfc3339_with_nanoseconds_is_truncated(self):
        dt = _mod.parse_docker_time("2026-09-11T11:14:02.123456789Z")
        assert dt.microsecond == 123456
        assert dt.tzinfo is not None

    def test_plain_seconds(self):
        assert _mod.parse_docker_time("2026-09-11T11:14:02Z").second == 2

    def test_empty_raises(self):
        try:
            _mod.parse_docker_time("")
        except ValueError:
            return
        raise AssertionError("空时间戳应抛 ValueError")


class TestTargetFilter:
    def test_label_is_authoritative(self):
        # #1655：testcontainers 注入的 label 是权威判据
        assert _c(10).is_target()
        assert _c(10, image="some-custom-pg:16").is_target()
        assert _c(10, image="testcontainers/ryuk:0.8.1").is_target()

    def test_bare_postgres_without_label_not_target(self):
        # 手工起、无 label 的 postgres 容器不得被当成目标（避免误删）
        assert not _c(10, labels={}).is_target()
        assert not _c(10, image="postgres:15", labels={}).is_target()
        assert not _c(10, image="stp-backend:latest", labels={}).is_target()


class TestPlanTargets:
    def test_splits_stale_and_recent_by_threshold(self):
        stale, recent = _mod.plan_targets(
            [_c(300), _c(121), _c(119), _c(5),
             _c(999, image="other:image", labels={})],
            min_age_minutes=120, now=NOW,
        )
        assert [c.age_minutes(now=NOW) for c in stale] == [300, 121]
        assert [c.age_minutes(now=NOW) for c in recent] == [119, 5]

    def test_threshold_boundary_is_stale(self):
        stale, recent = _mod.plan_targets([_c(120)], min_age_minutes=120, now=NOW)
        assert len(stale) == 1 and not recent


class TestMainFlow:
    def _runner(self, containers, *, rm_rc: int = 0):
        calls: list[list[str]] = []
        listing = "\n".join(
            f"{c.cid}\t{c.name}\t{c.image}\t{c.created.isoformat()}\t"
            f"{json.dumps(c.labels)}"
            for c in containers
        )

        def runner(args):
            calls.append(args)
            if args[0] == "ps":
                return 0, "\n".join(c.cid for c in containers)
            if args[0] == "rm":
                return rm_rc, ""
            return 0, listing

        return runner, calls

    def test_dry_run_reports_and_exits_zero(self, capsys):
        runner, calls = self._runner([_c(300, name="stale1")])
        rc = _mod.main([], runner=runner, now=NOW)
        out = capsys.readouterr().out
        assert rc == 0
        assert "疑似残留 1" in out and "docker rm -f" in out
        assert all(a[0] != "rm" for a in calls), "dry-run 不得执行删除"

    def test_strict_exits_one_when_stale(self):
        runner, _ = self._runner([_c(300)])
        assert _mod.main(["--strict"], runner=runner, now=NOW) == 1

    def test_recent_only_is_not_stale(self):
        runner, _ = self._runner([_c(10)])
        assert _mod.main(["--strict"], runner=runner, now=NOW) == 0

    def test_prune_without_yes_lists_only(self, capsys):
        # #1655：--prune 不带 --yes 只列出待删清单，不执行删除
        runner, calls = self._runner([_c(300, name="stale1")])
        rc = _mod.main(["--prune"], runner=runner, now=NOW)
        out = capsys.readouterr().out
        assert rc == 0
        assert all(a[0] != "rm" for a in calls), "--prune 无 --yes 不得删除"
        assert "需显式确认" in out

    def test_prune_with_yes_removes_only_stale(self):
        stale, recent = _c(300, name="stale1"), _c(5, name="recent1")
        removed: list[str] = []
        runner, calls = self._runner([stale, recent])

        def prune_runner(args):
            calls.append(args)
            if args[0] == "rm":
                removed.append(args[-1])
                return 0, ""
            return runner(args)

        rc = _mod.main(["--prune", "--yes"], runner=prune_runner, now=NOW)
        assert rc == 0
        assert removed == [stale.cid], "只清残留、不动近期容器"

    def test_prune_rm_failure_reported(self, capsys):
        runner, _ = self._runner([_c(300, name="stale1")], rm_rc=1)
        rc = _mod.main(["--prune", "--yes"], runner=runner, now=NOW)
        err = capsys.readouterr().err
        assert rc == 0
        assert "rm -f" in err and "失败" in err

    def test_no_containers_is_clean(self, capsys):
        def runner(args):
            return 0, ""

        assert _mod.main([], runner=runner, now=NOW) == 0
        assert "未发现 testcontainer" in capsys.readouterr().out

    def test_docker_missing_exits_two(self, capsys):
        def runner(args):
            raise FileNotFoundError("docker")

        assert _mod.main([], runner=runner, now=NOW) == 2
        assert "docker 不可用" in capsys.readouterr().err

    def test_docker_daemon_failure_is_not_green(self, capsys):
        # #1655：daemon 异常时 docker ps 非零退出——不得报「未发现」绿退
        def runner(args):
            return 125, ""

        assert _mod.main([], runner=runner, now=NOW) == 2
        assert "docker 巡检失败" in capsys.readouterr().err
        assert _mod.main(["--strict"], runner=runner, now=NOW) == 2
