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


class TestExitedContainerVisibility:
    """#1936：已停未删（Exited）的残留整类必须可见——列举必须带 ``-a``。

    回归语义：runner 模拟真实 docker 的过滤面——不带 ``-a`` 时只返回 running
    （本用例里为空），带 ``-a`` 才返回那个 Exited 容器。旧实现（``ps -q``）
    因此报「未发现」假绿，本用例在旧实现下必红。
    """

    def _exited_runner(self):
        stopped = _c(300, name="stopped1")
        listing = (
            f"{stopped.cid}\t{stopped.name}\t{stopped.image}\t"
            f"{stopped.created.isoformat()}\t{json.dumps(stopped.labels)}"
        )
        alive = {"v": True}
        calls: list[list[str]] = []

        def runner(args):
            calls.append(args)
            if args[0] == "rm":
                alive["v"] = False
                return 0, ""
            if args[0] == "ps":
                return 0, (stopped.cid if ("-a" in args and alive["v"]) else "")
            return 0, listing

        return runner, calls, stopped

    def test_exited_container_is_listed_and_flagged(self, capsys):
        runner, calls, stopped = self._exited_runner()
        rc = _mod.main(["--strict"], runner=runner, now=NOW)
        out = capsys.readouterr().out
        ps_args = next(a for a in calls if a[0] == "ps")
        assert "-a" in ps_args, "列举必须带 -a，否则 Exited 残留整类不可见"
        assert stopped.cid in out, "已停残留必须出现在报告里"
        assert rc == 1, "存在已停残留时 --strict 不得报绿"

    def test_exited_container_is_prunable(self, capsys):
        runner, calls, stopped = self._exited_runner()
        rc = _mod.main(["--prune", "--yes"], runner=runner, now=NOW)
        assert [a for a in calls if a[0] == "rm"] == [["rm", "-f", stopped.cid]]
        assert "仍有 0 个未清理" in capsys.readouterr().out
        assert rc == 0


class TestPruneStrictSemantics:
    """#1714：--prune --yes --strict 的退出码与复核报告语义。"""

    def _stateful_runner(self, *, rm_rc: int = 0, recheck_rc: int = 0):
        """容器集合随 rm 变化的 runner（区别于 _runner 的静态列表）。"""
        stale = _c(300, name="stale1")
        alive = {"v": True}
        ps_calls = {"n": 0}
        listing = (
            f"{stale.cid}\t{stale.name}\t{stale.image}\t"
            f"{stale.created.isoformat()}\t{json.dumps(stale.labels)}"
        )

        def runner(args):
            if args[0] == "rm":
                if rm_rc == 0:
                    alive["v"] = False
                return rm_rc, ""
            if args[0] == "ps":
                ps_calls["n"] += 1
                if recheck_rc != 0 and ps_calls["n"] > 1:
                    return recheck_rc, "daemon down"
                return 0, (stale.cid if alive["v"] else "")
            return 0, listing

        return runner, stale

    def test_clean_prune_with_strict_exits_zero(self, capsys):
        """清干净后 --strict 必须 0（此前用清理前的 stale 判定，恒返回 1）。"""
        runner, _ = self._stateful_runner()
        assert _mod.main(["--prune", "--yes", "--strict"], runner=runner, now=NOW) == 0
        out = capsys.readouterr().out
        assert "仍有 0 个未清理" in out

    def test_recheck_failure_is_reported_as_unknown_not_clean(self, capsys):
        """复核失败必须报「不可知」，不得谎报「仍有 0 个未清理」。"""
        runner, _ = self._stateful_runner(recheck_rc=1)
        rc = _mod.main(["--prune", "--yes", "--strict"], runner=runner, now=NOW)
        out = capsys.readouterr().out
        assert "复核失败" in out and "不可知" in out
        assert "仍有 0 个未清理" not in out, "不可知不得伪装成已清干净"
        assert rc == 1, "复核失败结果不可知，strict 保守返回 1"

    def test_prune_failure_keeps_strict_one(self, capsys):
        """rm 失败、容器仍在 → strict 仍 1。"""
        runner, _ = self._stateful_runner(rm_rc=1)
        assert _mod.main(["--prune", "--yes", "--strict"], runner=runner, now=NOW) == 1
        assert "仍有 1 个未清理" in capsys.readouterr().out
