"""`tools/dev/check-monitoring-assets.py` 的契约（#735 第三格：漂移要有人发现）。

背景：#2488 把告警规则补进安装清单后，仍没有东西回答「改了仓库、没重跑安装」——本机实测
一次就抓出 4 项漂移，其中 `/etc/default/prometheus-node-exporter` 装的是 **Debian 出厂默认**
（`--collector.nfsd` 从未生效，正是 #2197 要的那批指标）。所以本文件守的不是格式化，
而是「生产是否真的在跑仓库声称的东西」。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location(
    "check_monitoring_assets", REPO_ROOT / "tools" / "dev" / "check-monitoring-assets.py")
_mod = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
sys.modules["check_monitoring_assets"] = _mod
_spec.loader.exec_module(_mod)


@pytest.fixture
def fake_repo(tmp_path):
    """造一棵最小"仓库"：只放清单需要的源文件（内容含 <deploy-root> 标记）。"""
    root = tmp_path / "repo"
    from tools.site_config.stages import monitoring_artifacts
    for source_rel, _dest, _mode in monitoring_artifacts():
        target = root / source_rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# source {source_rel}\nroot=<deploy-root>\n", encoding="utf-8")
    return root


def _install(repo: Path, system_root: Path, *, mutate=None, skip=()):
    """按清单把"已装副本"铺到 system_root，可注入改动制造漂移。"""
    from tools.site_config.stages import monitoring_artifacts
    for source_rel, dest_rel, _mode in monitoring_artifacts():
        if source_rel in skip:
            continue
        dest = system_root / _mod.candidate_paths(dest_rel)[0]
        dest.parent.mkdir(parents=True, exist_ok=True)
        # 用被测模块自己的渲染函数造"已装"内容：一致才该判 match。此前这里手写替换
        # 只处理 <deploy-root>，于是可解析占位符（<prometheus-retention>）被测试自己
        # 造成不等，掩盖了它真正要断言的东西。
        text = _mod.expected_text(repo / source_rel, repo)
        if mutate:
            text = mutate(source_rel, text)
        dest.write_text(text, encoding="utf-8")


def test_all_assets_in_sync_exit_zero(tmp_path, fake_repo):
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root)
    results = _mod.inspect(system_root, fake_repo, repo_root=fake_repo)
    assert all(r["state"] == _mod.MATCH for r in results), results
    assert _mod.summarize(results)[0] == _mod.EXIT_OK


def test_one_byte_change_is_drift(tmp_path, fake_repo):
    system_root = tmp_path / "sys"

    def bump(source_rel, text):
        return text + "extra\n" if source_rel.endswith("stp-mem-top.timer") else text

    _install(fake_repo, system_root, mutate=bump)
    results = _mod.inspect(system_root, fake_repo, repo_root=fake_repo)
    states = {r["destination"]: r["state"] for r in results}
    assert states["etc/systemd/system/stp-mem-top.timer"] == _mod.DRIFT
    assert _mod.summarize(results)[0] == _mod.EXIT_DRIFT


def test_missing_asset_is_absent_not_drift(tmp_path, fake_repo):
    """本站可能压根没启用监控栈：缺席不能报成「漂移」，否则人人都是红的。"""
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root, skip=("deploy/control-plane/node-exporter/stp-mem-top.sh",))
    results = _mod.inspect(system_root, fake_repo, repo_root=fake_repo)
    states = {r["source"]: r["state"] for r in results}
    assert states["deploy/control-plane/node-exporter/stp-mem-top.sh"] == _mod.ABSENT
    assert _mod.summarize(results)[0] == _mod.EXIT_OK


def test_coverage_follows_the_manifest_not_a_hardcoded_list(tmp_path, fake_repo):
    """检测范围必须等于 `monitoring_artifacts()`——同一事实不留两套清单。

    #2488 之前规则文件就是因为「清单里没有」而从不被检查；本用例保证以后往清单里
    加资产（例如 Grafana 数据源）会自动进入检测，而不需要同步改这个测试。
    """
    from tools.site_config.stages import monitoring_artifacts
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root)
    results = _mod.inspect(system_root, fake_repo, repo_root=fake_repo)
    assert [r["source"] for r in results] == [a[0] for a in monitoring_artifacts()]
    assert len(results) >= 9  # 规则文件与守卫单元都已在清单里（#2488）


def test_resolvable_placeholder_is_checked_not_skipped(tmp_path, fake_repo):
    """回归：残余占位符必须在**替换之后**判。

    `<prometheus-retention>` 由 installer 常量确定；先判残余会把它误记 SKIP，
    检测面静默变小（实测踩到，本机 prometheus.default 一度被判不可确定）。
    """
    from tools.site_config.stages import PROMETHEUS_RETENTION
    rel = "deploy/prometheus/prometheus.default"
    src = fake_repo / rel
    src.write_text('ARGS="--storage.tsdb.retention.time=<prometheus-retention>"\n'
                   'root=<deploy-root>\n', encoding="utf-8")
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root)
    got = (system_root / "etc/default/prometheus").read_text(encoding="utf-8")
    assert PROMETHEUS_RETENTION in got
    states = {r["source"]: r["state"] for r in _mod.inspect(system_root, fake_repo, repo_root=fake_repo)}
    assert states[rel] == _mod.MATCH


def test_truly_unknown_placeholder_is_skipped_with_reason(tmp_path, fake_repo):
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root)
    src = fake_repo / "deploy/prometheus/prometheus.yml"
    src.write_text('site: "<site-id>"\nroot=<deploy-root>\n', encoding="utf-8")
    entry = next(r for r in _mod.inspect(system_root, fake_repo, repo_root=fake_repo)
                 if r["source"].endswith("prometheus.yml"))
    assert entry["state"] == _mod.SKIPPED and "<site-id>" in entry["detail"]


def test_legacy_distro_path_is_accepted(tmp_path, fake_repo):
    """本机这类 installer 之前的存量部署只有 /etc/prometheus：必须认得，否则全判 absent。

    #2643 方向 1 后站点装的是子集文件（`site-alerts.yml`），但**目标路径不变**——
    存量机升级时按同名覆盖，本判据正是钉这一点：老路径仍要被认出。
    """
    system_root = tmp_path / "sys"
    from tools.site_config.stages import monitoring_artifacts
    rel = "deploy/prometheus/site-alerts.yml"
    dest_rel = next(d for s, d, _ in monitoring_artifacts() if s == rel)
    legacy = _mod.LEGACY_FALLBACKS[dest_rel]
    target = system_root / legacy
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(_mod.expected_text(fake_repo / rel, fake_repo), encoding="utf-8")
    entry = next(r for r in _mod.inspect(system_root, fake_repo, repo_root=fake_repo) if r["source"] == rel)
    assert entry["state"] == _mod.MATCH
    assert entry["hit"] == legacy


def test_deploy_root_prefers_systemd_over_script_repo(tmp_path, monkeypatch):
    """回归：默认取脚本所在仓库根会让 worktree 里跑的检测**全线假漂移**。"""
    monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 0, stdout="WorkingDirectory=/srv/stp-production\n", stderr=""))
    root, reason = _mod.resolve_deploy_root(None)
    assert str(root) == "/srv/stp-production" and "systemd" in reason


def test_deploy_root_falls_back_when_unit_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(_mod.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
        a, 1, stdout="", stderr="Failed to get properties"))
    root, reason = _mod.resolve_deploy_root(None)
    assert root == _mod.REPO_ROOT and "回退" in reason


def test_explicit_deploy_root_wins(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("显式给了 --deploy-root 就不该再探测 systemd")
    monkeypatch.setattr(_mod.subprocess, "run", boom)
    root, reason = _mod.resolve_deploy_root("/srv/explicit")
    assert str(root) == "/srv/explicit" and "显式" in reason


def test_main_json_carries_exit_code_and_counts(tmp_path, fake_repo, capsys):
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root)
    rc = _mod.main(["--system-root", str(system_root), "--repo-root", str(fake_repo),
                    "--deploy-root", str(fake_repo), "--json"])
    assert rc == _mod.EXIT_OK
    payload = json.loads(capsys.readouterr().out)
    assert payload["counts"][_mod.MATCH] == len(payload["assets"])
    assert payload["deploy_root"] == str(fake_repo)


def test_detector_has_no_database_or_credential_dependency():
    """静态自证：部署前守卫**不能**要求数据库配置（它跑在 `alembic upgrade` 之前）。

    `check-deploy-source.sh` 与 systemd 的 `ExecStartPre=-` 都会调这条链，任何
    import 期解析 DATABASE_URL 的副作用都会让它在新站点上必然失败（#735 §1.3 同族）。
    """
    src = (REPO_ROOT / "tools" / "dev" / "check-monitoring-assets.py").read_text(encoding="utf-8")
    assert "from backend" not in src and "import backend" not in src
    assert "DATABASE_URL" not in src and "STP_ADMIN" not in src


# ---------------------------------------------------------------- 接线契约

SH = REPO_ROOT / "tools" / "dev" / "check-deploy-source.sh"


def test_detector_is_wired_into_the_deploy_source_guard():
    """漂移检测必须真的被部署守卫调用——否则又是一次「有工具、没人跑」（#735 的一贯教训）。

    执行者选 `check-deploy-source.sh` 而不是新开 timer：漂移只在"改了仓库、没重跑安装"时
    发生，与部署/重启时点天然重合；而它已经在 runbook 每一步与 systemd `ExecStartPre=-`
    上运行。
    """
    text = SH.read_text(encoding="utf-8")
    assert "check-monitoring-assets.py" in text


def test_wired_call_warns_and_does_not_propagate_exit_code():
    """接线必须是 WARN 语义：本脚本 `exit 1` 会被 runbook 读成「停止部署」。

    反向也要成立——不能写成 `|| true` 那种把输出一起吞掉的形态，否则 WARN 没人看得见。
    """
    text = SH.read_text(encoding="utf-8")
    at = text.index("check-monitoring-assets.py")
    segment = text[max(0, at - 400):at + 700]
    assert "WARN" in segment, "漂移只该告警，不该阻塞部署"
    assert ">&2" in segment, "WARN 必须写 stderr：stdout 留给部署报告的通过行"
    assert "|| true" not in segment, "不许用 || true 吞掉诊断输出"


def test_guard_still_passes_when_detector_reports_drift(tmp_path, fake_repo):
    """端到端：检测器 exit 1 时，用同一套 shell 结构跑一遍，脚本仍应给出 WARN 且整体成功。"""
    system_root = tmp_path / "sys"
    _install(fake_repo, system_root, mutate=lambda s, t: t + "drifted\n"
             if s.endswith("stp-mem-top.timer") else t)
    wrapper = tmp_path / "run.sh"
    wrapper.write_text(
        "#!/usr/bin/env bash\nset -u\n"
        'if drift_out="$("' + sys.executable + '" '
        '"' + str(REPO_ROOT / 'tools' / 'dev' / 'check-monitoring-assets.py') + '" '
        '--system-root ' + str(system_root) + ' --repo-root ' + str(fake_repo)
        + ' --deploy-root ' + str(fake_repo) + ' 2>&1)"; then\n'
        '  echo "OK line"\nelse\n'
        '  echo "check-deploy-source: WARN —— drift" >&2\n'
        '  printf \'%s\\n\' "$drift_out" | grep -E \'^[[:space:]]+\\[(DRIFT|SKIP |ABSENT)\' >&2\n'
        'fi\nexit 0\n', encoding="utf-8")
    proc = subprocess.run(["bash", str(wrapper)], capture_output=True, text=True,
                          env={**os.environ, "PYTHONPATH": str(REPO_ROOT)})
    assert proc.returncode == 0, proc.stderr
    assert "WARN" in proc.stderr, proc.stderr
    # 反向自证：子进程没起来时这些断言会一起空转，必须显式排除
    assert "No such file" not in proc.stderr, "子进程没起来 ⇒ 断言假绿"
    assert "usage:" not in proc.stderr, "参数没被接受 ⇒ 断言假绿"
    assert "DRIFT" in proc.stderr  # 漂移行确实被透出来了，不是静默吞掉


# ---------------------------------------------------------------- 事实源标注

MAIN_SHA = "f" * 40


def _fake_git(head_sha=MAIN_SHA, branch=None, has_origin=True):
    """按子命令分派的假 git——覆盖 describe_source_repo 用到的四条查询。"""
    def run(cmd, *a, **k):
        args = list(cmd)
        sub = " ".join(args[args.index("git") + 3:]) if "git" in args else " ".join(args)
        if "-f%h" in sub or "--format=%h" in sub:
            return subprocess.CompletedProcess(cmd, 0, stdout=head_sha[:7], stderr="")
        if "rev-parse HEAD" in sub:
            return subprocess.CompletedProcess(cmd, 0, stdout=head_sha, stderr="")
        if "symbolic-ref" in sub:
            if branch is None:
                return subprocess.CompletedProcess(cmd, 1, stdout="",
                                                   stderr="fatal: ref HEAD is not a symbolic ref")
            return subprocess.CompletedProcess(cmd, 0, stdout=branch, stderr="")
        if "rev-parse origin/main" in sub:
            if not has_origin:
                return subprocess.CompletedProcess(cmd, 128, stdout="",
                                                   stderr="fatal: unknown revision")
            return subprocess.CompletedProcess(cmd, 0, stdout=MAIN_SHA, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="unexpected: " + sub)

    return run


@pytest.mark.parametrize(
    "head_sha,branch,has_origin,expect_warn,expect_in",
    [
        # 在 main 且与 origin/main 同内容 ⇒ 可信
        (MAIN_SHA, "main", True, False, "内容 == origin/main"),
        # 特性分支但 HEAD 就是 origin/main（刚开分支未提交）⇒ 也不该警告：按名字判是错的
        (MAIN_SHA, "fix/some-branch", True, False, "内容 == origin/main"),
        # detached 指向 origin/main（本次做出正确判定的用法）⇒ 可信
        (MAIN_SHA, None, True, False, "内容 == origin/main"),
        # 在 main 但落后/未 fetch ⇒ 内容不是 main，必须提示（按分支名判会漏掉这种）
        ("a" * 40, "main", True, True, "可能假漂移"),
        # 特性分支且内容不同 ⇒ 提示，并给出两个 SHA 便于定位
        ("b" * 40, "fix/other", True, True, "可能假漂移"),
        # 无 origin/main 引用（浅克隆/离线）⇒ 不假装可信
        (MAIN_SHA, "main", False, True, "无法确认事实源"),
    ],
    ids=["main-ok", "branch-same-sha-ok", "detached-ok", "main-stale-warn",
         "branch-warn", "no-origin-warn"],
)
def test_source_repo_trust_is_decided_by_content_not_branch_name(
        monkeypatch, tmp_path, head_sha, branch, has_origin, expect_warn, expect_in):
    """回归：判据必须是「HEAD 是否等于 origin/main」，不是分支名。

    按名字判会同时犯两种错：`worktree add --detach origin/main`（本次正确判定用的就是它）
    与刚开的特性分支被误警告；而「在 main 上但没 fetch」内容已落后 main，却一片祥和。
    """
    monkeypatch.setattr(_mod.subprocess, "run", _fake_git(head_sha, branch, has_origin))
    line = _mod.describe_source_repo(tmp_path)
    assert ("⚠" in line) is expect_warn, line
    assert expect_in in line, line
    if not expect_warn:
        assert head_sha[:7] in line, line


def test_source_repo_handles_non_git_root(monkeypatch, tmp_path):
    """非 git 树（误传路径）必须明说，不能返回一个看起来正常的字符串。"""
    def nope(cmd, *a, **k):
        return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="not a git repo")

    monkeypatch.setattr(_mod.subprocess, "run", nope)
    line = _mod.describe_source_repo(tmp_path)
    assert "非 git 树" in line and "⚠" in line


