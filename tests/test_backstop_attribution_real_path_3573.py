"""#3573 真实路径回归：DRY_RUN=0 走生产脚本的 job 选择 / 跨 attempt 匹配 / 落定判定。

背景：本单 10-03 审查实测证伪了「dry-run 全绿 = 修复可用」——原有守卫文件全部
DRY_RUN=1，重跑结论由 `DRY_RUN_RERUN_CONCLUSION` 预置，真实路径的 jq 选择程序零覆盖；
三条独立缺陷中，前两项（快照字符串 id 与 API 数字 id 不匹配 / 重跑后同名 job 换新 id 致
旧 id 失效）导致空结论，第三项（conclusion=null 落 else mixed）导致提前错误分类。

做法（正文 §3，另见 §3.1）：
- `DRY_RUN=0` 执行**生产脚本**（不复制 jq 算法、不预置结论、不在测试里短路）；
- 临时 PATH 里放 `gh` 桩：按 scenario 返回真实 API 形态（数字 id、name、status、
  conclusion、run_attempt，快照/轮询分页），记录端点、顺序与轮询次数；
- 桩遇未知调用立即失败（exit 97）；latest 端点与「重跑后的旧 attempt」端点显式禁止，
  绝无触网路径；
- 临时 PATH 里放 `sleep` 桩：只记录参数、不真实等待；`WAIT_INTERVAL=0`（快速回归）
  或正值 30（轮询节奏断言，见 sleep 相关用例）；
- `WAIT_MAX` / `WAIT_MAX_SLOW` 用小值，无长等待。

`STP_BACKSTOP_SCRIPT` 可指向临时脚本副本，供 §3 三类变异与 §3.1 `_` 计数器变异自证使用；
共享 checkout 不变异。
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
# 变异自证（§3）用临时副本时经环境变量指向；默认就是生产脚本。
_SCRIPT = Path(
    os.environ.get("STP_BACKSTOP_SCRIPT") or (_ROOT / "scripts" / "ci" / "backstop-attribution.sh")
)

_RUN_ID = "12345"
_REPO = "owner/name"
# 真实形态的旧/新 job id（取自 run 35148237108 实测，#3573 §1）
_OLD_BACKEND_ID = 104969662010
_OLD_FRONTEND_ID = 104969662148
_NEW_BACKEND_ID = 104988897052
_NEW_FRONTEND_ID = 104988896806

_VITEST_LINE = (
    "2026-10-02T06:42:39.7508245Z  FAIL  src/utils/tmpRedTimingProbe.test.ts"
    " > tmp red timing probe > intentional red for #3573 timing verification\n"
)

# ── gh 桩：按 scenario 应答并记录调用 ─────────────────────────────────────
# 未知调用一律 exit 97——测试若见该退出码即说明生产脚本调了未约定的端点（或触网企图）。
_STUB = r'''
import json, os, sys

SCENARIO = json.load(open(os.environ["GH_STUB_SCENARIO"], encoding="utf-8"))
STATE_PATH = os.environ["GH_STUB_STATE"]
CALLS_PATH = os.environ["GH_STUB_CALLS"]

argv = sys.argv[1:]
with open(CALLS_PATH, "a", encoding="utf-8") as fh:
    fh.write(json.dumps({"argv": argv}, ensure_ascii=False) + "\n")

state = json.load(open(STATE_PATH, encoding="utf-8")) if os.path.exists(STATE_PATH) else {}

def save():
    with open(STATE_PATH, "w", encoding="utf-8") as fh:
        json.dump(state, fh)

def crash(msg):
    sys.stderr.write("gh stub: " + msg + "\n")
    sys.exit(97)

def http_fail(status, message):
    sys.stderr.write("gh: %s (HTTP %s)\n" % (message, status))
    sys.exit(1)

def take(seq, key):
    idx = state.get(key, 0)
    state[key] = idx + 1
    save()
    if not seq:
        return None
    return seq[min(idx, len(seq) - 1)]

run_id = str(SCENARIO.get("run_id", "12345"))
prefix = "repos/" + SCENARIO.get("repo", "owner/name")

# --- gh run rerun <id> --repo ... --failed ---
if argv[:2] == ["run", "rerun"]:
    if not SCENARIO.get("rerun_ok", True):
        sys.stderr.write("gh: failed to rerun workflow (HTTP 403)\n")
        sys.exit(1)
    state["rerun_seen"] = True
    save()
    sys.exit(0)

if argv[:1] != ["api"]:
    crash("unrecognized command: %r" % (argv,))

rest = [a for a in argv[1:] if a != "--allow-escape-sequences"]
if not rest:
    crash("missing endpoint")
endpoint = rest[0]
query = ""
if "?" in endpoint:
    endpoint, query = endpoint.split("?", 1)

# --- run 本体：run_attempt 序列 ---
if endpoint == "%s/actions/runs/%s" % (prefix, run_id):
    attempt = take(SCENARIO.get("run_attempt_sequence", [1]), "run_attempt")
    sys.stdout.write(json.dumps({
        "id": int(run_id), "run_attempt": attempt,
        "status": "completed", "conclusion": "failure",
    }))
    sys.exit(0)

# --- 分页 jobs：attempts/<n>/jobs?per_page=100&page=<p> ---
marker = "%s/actions/runs/%s/attempts/" % (prefix, run_id)
if endpoint.startswith(marker) and endpoint.endswith("/jobs"):
    attempt = int(endpoint[len(marker):-len("/jobs")])
    params = dict(p.split("=", 1) for p in query.split("&") if "=" in p)
    page = int(params.get("page", "1"))
    forbid_below = int(SCENARIO.get("forbid_old_attempt_after_rerun", 0) or 0)
    if state.get("rerun_seen") and attempt < forbid_below:
        crash("forbidden: old attempt %s jobs polled after rerun" % attempt)
    snapshots = SCENARIO.get("attempts", {}).get(str(attempt), [])
    if not snapshots:
        http_fail(404, "Not Found")
    if page == 1:
        idx = state.get("jobs:%s" % attempt, 0)
        state["jobs:%s" % attempt] = idx + 1
        state["jobs_snapshot:%s" % attempt] = idx
    else:
        idx = state.get("jobs_snapshot:%s" % attempt, 0)
    save()
    snap = snapshots[min(idx, len(snapshots) - 1)]
    if snap.get("error404"):
        http_fail(404, "Not Found")
    if "http_error" in snap:
        err = snap["http_error"]
        http_fail(err.get("status", 500), err.get("message", "Internal Server Error"))
    if "raw" in snap:
        sys.stdout.write(snap["raw"])
        sys.exit(0)
    pages = snap.get("pages", [])
    sys.stdout.write(json.dumps(pages[min(page - 1, len(pages) - 1)]))
    sys.exit(0)

# --- 旧 job 日志：actions/jobs/<id>/logs ---
log_marker = "%s/actions/jobs/" % prefix
if endpoint.startswith(log_marker) and endpoint.endswith("/logs"):
    jid = endpoint[len(log_marker):-len("/logs")]
    logs = SCENARIO.get("logs", {})
    if jid in logs:
        sys.stdout.write(logs[jid])
        sys.exit(0)
    http_fail(404, "Not Found")

# --- latest attempt 的 jobs 端点：固定 attempt 修复后不得再被调用 ---
if endpoint == "%s/actions/runs/%s/jobs" % (prefix, run_id):
    crash("forbidden: latest-attempt jobs endpoint polled")

crash("unrecognized endpoint: %s?%s" % (endpoint, query))
'''

# sleep 桩（#3573 §3.1）：只把参数追加到 $SLEEP_CALLS、不真实等待。
# 生产脚本只在 wait_conclusion 的「pending 且仍有下一轮」分支调用 sleep。
_SLEEP_STUB = """#!/usr/bin/env bash
if [ -n "${SLEEP_CALLS:-}" ]; then
  printf '%s\\n' "$*" >> "$SLEEP_CALLS"
fi
exit 0
"""


# ── scenario 构造 ─────────────────────────────────────────────────────────

def _job(jid: int, name: str, *, status: str = "completed", conclusion: str = "success",
         run_attempt: int = 1, steps: list[dict] | None = None) -> dict:
    """真实 job 对象的用到的子集：数字 id、name、status、conclusion、run_attempt。"""
    return {
        "id": jid,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": run_attempt,
        "steps": steps if steps is not None else [
            {"name": f"run {name}", "status": "completed", "conclusion": "success"}
        ],
    }


def _failed(name: str) -> dict:
    return {"name": f"run {name}", "status": "completed", "conclusion": "failure"}


def _page(jobs: list[dict], total_count: int | None = None) -> dict:
    return {"total_count": len(jobs) if total_count is None else total_count, "jobs": jobs}


def _snapshot(*pages: dict) -> dict:
    return {"pages": list(pages)}


def _scenario(*, attempt1: list[dict] | None = None, attempt2: list[dict] | None = None,
              run_attempt_sequence: list[int], rerun_ok: bool = True,
              logs: dict[str, str] | None = None) -> dict:
    attempts: dict[str, list[dict]] = {}
    if attempt1 is not None:
        attempts["1"] = attempt1
    if attempt2 is not None:
        attempts["2"] = attempt2
    return {
        "repo": _REPO,
        "run_id": _RUN_ID,
        "run_attempt_sequence": list(run_attempt_sequence),
        "rerun_ok": rerun_ok,
        "attempts": attempts,
        "logs": logs or {},
        "forbid_old_attempt_after_rerun": 2,
    }


def _backend_failure_page(*, run_attempt: int = 1, extra: list[dict] | None = None) -> dict:
    jobs = [
        _job(_OLD_BACKEND_ID, "backend-test", conclusion="failure",
             run_attempt=run_attempt, steps=[_failed("Run backend tests")]),
        _job(_OLD_FRONTEND_ID, "frontend-check", conclusion="success", run_attempt=run_attempt),
    ]
    jobs.extend(extra or [])
    return _page(jobs)


def _two_failure_targets_page(*, run_attempt: int = 1) -> dict:
    return _page([
        _job(_OLD_BACKEND_ID, "backend-test", conclusion="failure",
             run_attempt=run_attempt, steps=[_failed("Run backend tests")]),
        _job(_OLD_FRONTEND_ID, "frontend-check", conclusion="failure",
             run_attempt=run_attempt, steps=[_failed("Run backend tests")]),
    ])


def _new_backend(conclusion: str, *, status: str = "completed",
                 suffix_id: int = 0) -> dict:
    return _job(_NEW_BACKEND_ID + suffix_id, "backend-test", status=status,
                conclusion=conclusion, run_attempt=2)


# ── 执行与解析 ────────────────────────────────────────────────────────────

def _parse_outputs(path: Path) -> dict[str, str]:
    """解析 $GITHUB_OUTPUT（含 `name<<DELIM ... DELIM` 多行形态）。"""
    out: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<__STP_" in line:
            name, delim = line.split("<<", 1)
            i += 1
            buf = []
            while i < len(lines) and lines[i] != delim:
                buf.append(lines[i])
                i += 1
            out[name] = "\n".join(buf)
        elif "=" in line:
            name, value = line.split("=", 1)
            out[name] = value
        i += 1
    return out


class RealPathRun:
    def __init__(self, proc: subprocess.CompletedProcess, outputs: dict[str, str],
                 calls: list[list[str]], sleep_calls: list[str]):
        self.proc = proc
        self.outputs = outputs
        self.calls = calls
        self.sleep_calls = sleep_calls

    @property
    def endpoints(self) -> list[str]:
        eps = []
        for argv in self.calls:
            if argv[:1] == ["api"]:
                eps.extend(a for a in argv[1:] if not a.startswith("--"))
        return eps

    def jobs_calls(self, attempt: int) -> list[str]:
        return [e for e in self.endpoints if f"/attempts/{attempt}/jobs" in e]

    def latest_jobs_calls(self) -> list[str]:
        return [e for e in self.endpoints
                if re.search(rf"/actions/runs/{_RUN_ID}/jobs(?:\?|$)", e)]

    def rerun_calls(self) -> list[list[str]]:
        return [c for c in self.calls if c[:2] == ["run", "rerun"]]

    def call_index(self, pred) -> int:
        for i, argv in enumerate(self.calls):
            if pred(argv):
                return i
        raise AssertionError(f"调用不存在；实得 {self.calls!r}")

    def field(self, name: str) -> str:
        assert name in self.outputs, f"output 缺字段 {name}；实得 {sorted(self.outputs)}"
        return self.outputs[name]


def _run(tmp_path: Path, scenario: dict, *, env: dict[str, str] | None = None,
         wait_max: str = "120", wait_interval: str = "0") -> RealPathRun:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "gh"
    stub.write_text(f"#!{sys.executable}\n" + _STUB, encoding="utf-8")
    stub.chmod(0o755)
    # sleep 桩：记录每次调用的参数、不真实等待（#3573 §3.1 轮询节奏断言）。
    sleep_stub = bin_dir / "sleep"
    sleep_stub.write_text(_SLEEP_STUB, encoding="utf-8")
    sleep_stub.chmod(0o755)

    scenario_file = tmp_path / "scenario.json"
    scenario_file.write_text(json.dumps(scenario), encoding="utf-8")
    state_file = tmp_path / "stub_state.json"
    calls_file = tmp_path / "calls.jsonl"
    sleep_calls_file = tmp_path / "sleep_calls.txt"
    out_file = tmp_path / "gh_output"
    out_file.write_text("", encoding="utf-8")

    env_vars = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "REPO": _REPO,
        "CI_RUN_ID": _RUN_ID,
        "FIRST_CONCLUSION": "failure",
        "DRY_RUN": "0",
        "WAIT_INTERVAL": wait_interval,
        "WAIT_MAX": wait_max,
        "GITHUB_OUTPUT": str(out_file),
        "GH_STUB_SCENARIO": str(scenario_file),
        "GH_STUB_STATE": str(state_file),
        "GH_STUB_CALLS": str(calls_file),
        "SLEEP_CALLS": str(sleep_calls_file),
        "HOME": str(tmp_path),
    }
    env_vars.update(env or {})
    proc = subprocess.run(
        ["bash", str(_SCRIPT)], env=env_vars, capture_output=True, text=True,
        timeout=120, check=False,
    )
    calls = []
    if calls_file.exists():
        calls = [json.loads(line)["argv"] for line in calls_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    sleep_calls = []
    if sleep_calls_file.exists():
        sleep_calls = [line for line in sleep_calls_file.read_text(encoding="utf-8").splitlines() if line]
    return RealPathRun(proc, _parse_outputs(out_file), calls, sleep_calls)


# ── §3 覆盖1 跨 attempt：新 id + 相同 name ────────────────────────────────────

def test_new_attempt_new_ids_same_name_green_is_flake(tmp_path: Path):
    """旧/新 attempt 数字 id 不同、name 相同；新 attempt 成功 → flake。

    旧实现按快照里的旧 id 在新 attempt 里匹配，必然落空；按 name 才可能命中。
    """
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert run.field("rerun_conclusion") == "success"
    assert len(run.rerun_calls()) == 1
    assert run.latest_jobs_calls() == [], "轮询不得读 latest attempt"
    assert len(run.jobs_calls(2)) == 1
    # 旧 attempt 只在重跑前做快照（恰好一次），重跑后不得再读
    snapshot_call = run.call_index(lambda c: c[:1] == ["api"] and any(
        "/attempts/1/jobs" in a for a in c))
    rerun_call = run.call_index(lambda c: c[:2] == ["run", "rerun"])
    assert snapshot_call < rerun_call


def test_new_attempt_new_ids_same_name_red_is_deterministic(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("failure")]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert run.field("rerun_conclusion") == "failure"


# ── §3 覆盖2 新 attempt 暂时 404/空集 ─────────────────────────────────────────

def test_new_attempt_404_then_appears_polls_fixed_attempt_only(tmp_path: Path):
    """首次 404、随后空集，均继续等；桩禁止 latest 与重跑后的旧 attempt 轮询。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[
            {"error404": True},
            _snapshot(_page([], total_count=0)),
            _snapshot(_page([_new_backend("success")])),
        ],
        run_attempt_sequence=[1, 1, 2, 2, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert len(run.jobs_calls(2)) == 3, "404/空集后必须在预算内重试固定 attempt"
    assert run.latest_jobs_calls() == []
    # 重跑后不得回读旧 attempt（桩内也会 crash，此处再从调用面钉一次）
    rerun_call = run.call_index(lambda c: c[:2] == ["run", "rerun"])
    after = run.calls[rerun_call + 1:]
    assert not [c for c in after if any("/attempts/1/jobs" in a for a in c if a.startswith("repos/"))]


def test_attempt_changed_before_rerun_is_concurrent_unknown(tmp_path: Path):
    """重跑前再次确认 attempt 未变化；变化即并发重跑，不重跑、不归因。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "重跑前检测到 run attempt" in run.outputs["attribution_md"]
    assert run.rerun_calls() == []
    assert run.jobs_calls(2) == []


def test_new_attempt_not_created_yet_waits_then_settles(tmp_path: Path):
    """run_attempt 仍是 A：未定继续等；旧 attempt 的 failure 不得抢跑成 deterministic。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1, 1, 2],  # 第二次轮询新 attempt 才出现
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert len(run.jobs_calls(2)) == 1, "attempt 未出现时不该去查它的 jobs"


# ── §3 覆盖3 未完成 / null 落定 ───────────────────────────────────────────────

def test_success_plus_null_keeps_waiting_then_settles(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[
            _snapshot(_page([
                _new_backend("success"),
                _job(_NEW_FRONTEND_ID, "frontend-check", status="completed",
                     conclusion=None, run_attempt=2),
            ])),
            _snapshot(_page([
                _new_backend("success"),
                _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="success", run_attempt=2),
            ])),
        ],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert len(run.jobs_calls(2)) == 2, "completed+null 必须继续等，不得提前落定"


def test_failure_plus_null_keeps_waiting_then_settles(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[
            _snapshot(_page([
                _new_backend("failure"),
                _job(_NEW_FRONTEND_ID, "frontend-check", status="completed",
                     conclusion=None, run_attempt=2),
            ])),
            _snapshot(_page([
                _new_backend("failure"),
                _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="failure", run_attempt=2),
            ])),
        ],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert len(run.jobs_calls(2)) == 2


def test_queued_and_in_progress_keep_waiting_then_settle(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[
            _snapshot(_page([_new_backend("failure", status="queued")])),
            _snapshot(_page([_new_backend("failure", status="in_progress")])),
            _snapshot(_page([_new_backend("failure")])),
        ],
        run_attempt_sequence=[1, 1, 2, 2, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert len(run.jobs_calls(2)) == 3, "queued/in_progress 不得被当作落定"


# ── §3 覆盖4 部分目标 / 缺目标 / 预算耗尽 ─────────────────────────────────────

def test_partial_targets_wait_then_settle(tmp_path: Path):
    """初次只出现部分目标继续等；随后齐全才分类，子集不得判 success。"""
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[
            _snapshot(_page([_new_backend("success")])),
            _snapshot(_page([
                _new_backend("success"),
                _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="success", run_attempt=2),
            ])),
        ],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert len(run.jobs_calls(2)) == 2


def test_missing_target_exhausts_small_budget_unknown(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],  # 永远缺 frontend-check
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_max="2", env={"WAIT_MAX_SLOW": "2"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "未在预算内完成" in run.outputs["attribution_md"]
    assert len(run.jobs_calls(2)) == 2, "小预算耗尽即停，不得无限循环"
    assert run.field("classification") != "flake"


def test_completed_null_exhausts_small_budget_unknown(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([
            _job(_NEW_BACKEND_ID, "backend-test", status="completed",
                 conclusion=None, run_attempt=2),
        ]))],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_max="2", env={"WAIT_MAX_SLOW": "2"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "未在预算内完成" in run.outputs["attribution_md"]


def test_always_pending_exhausts_small_budget_unknown(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("failure", status="queued")]))],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_max="2", env={"WAIT_MAX_SLOW": "2"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "未在预算内完成" in run.outputs["attribution_md"]


def test_empty_new_attempt_set_is_not_success(tmp_path: Path):
    """空集不能经 jq 空集 all() 判成 success。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([]))],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_max="1", env={"WAIT_MAX_SLOW": "1"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"


# ── §3 覆盖5 三个分类出口（含真实 mixed）──────────────────────────────────────

def test_all_red_conclusions_is_deterministic(tmp_path: Path):
    """failure + timed_out 都属红结论集合 → deterministic。"""
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[_snapshot(_page([
            _new_backend("failure"),
            _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="timed_out", run_attempt=2),
        ]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert run.field("rerun_conclusion") == "failure"


def test_real_mixed_success_and_failure_is_deterministic_with_note(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_two_failure_targets_page())],
        attempt2=[_snapshot(_page([
            _new_backend("success"),
            _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="failure", run_attempt=2),
        ]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert run.field("rerun_conclusion") == "mixed"
    md = run.outputs["attribution_md"]
    assert "部分转绿、部分仍红" in md


# ── §3 覆盖6 无关兄弟 job 与预算分级 ─────────────────────────────────────────

def test_unrelated_pending_sibling_does_not_block_targets(tmp_path: Path):
    """兄弟 job 永久 queued 不阻塞目标落定。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([
            _new_backend("failure"),
            _job(555001, "docker-build", status="queued", conclusion=None, run_attempt=2),
        ]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert len(run.jobs_calls(2)) == 1, "兄弟 job 未落定不该拖住目标聚合"


def test_budget_grading_unchanged_in_real_path(tmp_path: Path):
    """快/慢/混合目标保持原预算（WAIT_INTERVAL=30 只为输出窗口值，首个轮询即落定不等待）。"""
    counter = {"i": 0}

    def budget(targets: list[dict], first_job: dict) -> str:
        case_dir = tmp_path / f"case{counter['i']}"
        counter["i"] += 1
        scenario = _scenario(
            attempt1=[_snapshot(_page(targets))],
            attempt2=[_snapshot(_page([first_job]))],
            run_attempt_sequence=[1, 1, 2],
        )
        return _run(case_dir, scenario, wait_interval="30").field("wait_budget")

    fast = _job(_OLD_FRONTEND_ID, "frontend-check", conclusion="failure", steps=[_failed("x")])
    slow = _job(_OLD_BACKEND_ID, "backend-test", conclusion="failure", steps=[_failed("x")])
    slow_new = _job(_NEW_BACKEND_ID, "backend-test", conclusion="success", run_attempt=2)
    fast_new = _job(_NEW_FRONTEND_ID, "frontend-check", conclusion="success", run_attempt=2)
    assert budget([fast], fast_new) == "3600"
    assert budget([slow], slow_new) == "7200"
    assert budget([fast, slow], fast_new) == "7200"


# ── §3 覆盖7 证据不足一律 unknown（原因准确、无无限循环）──────────────────────

def test_empty_snapshot_targets_unknown_without_rerun(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_page([
            _job(1, "frontend-check", conclusion="success"),
        ]))],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "没有 conclusion=failure 的目标 job" in run.outputs["attribution_md"]
    assert run.rerun_calls() == [], "空目标不得触发重跑"
    assert run.jobs_calls(2) == [], "空目标不得进入轮询"


def test_duplicate_target_names_at_snapshot_unknown_without_rerun(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_page([
            _job(11, "backend-test", conclusion="failure", steps=[_failed("x")]),
            _job(12, "backend-test", conclusion="failure", steps=[_failed("x")]),
        ]))],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "不唯一" in run.outputs["attribution_md"]
    assert run.rerun_calls() == []


def test_duplicate_target_in_new_attempt_unknown(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([
            _new_backend("success"),
            _new_backend("failure", suffix_id=1),
        ]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    md = run.outputs["attribution_md"]
    assert "不唯一" in md
    assert "预算内未完成" not in md, "结构异常不得伪装成预算耗尽"
    assert len(run.jobs_calls(2)) == 1


def test_non_404_api_error_unknown_with_real_reason(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[{"http_error": {"status": 500, "message": "Internal Server Error"}}],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    md = run.outputs["attribution_md"]
    assert "Internal Server Error" in md or "500" in md
    assert "预算内未完成" not in md
    assert len(run.jobs_calls(2)) == 1, "非 404 API 错误应立即 unknown，不烧预算"


def test_invalid_json_response_unknown(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[{"raw": "{not-json"}],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "解析失败" in run.outputs["attribution_md"]
    assert len(run.jobs_calls(2)) == 1


def test_attempt_jump_beyond_fixed_is_concurrent_unknown(tmp_path: Path):
    """轮询发现 run attempt 已超过 A+1：并发重跑证据不确定，不追随最新。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1, 3],
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "并发重跑" in run.outputs["attribution_md"]
    assert run.jobs_calls(2) == [], "发现并发后不得再读固定新 attempt"
    assert not [e for e in run.endpoints if "/attempts/3/" in e]


def test_completed_uncategorizable_conclusion_unknown(tmp_path: Path):
    """cancelled/skipped/neutral/action_required 等非空结论：unknown，不作确定性样本。"""
    for bad in ("cancelled", "skipped", "neutral", "action_required"):
        case_dir = tmp_path / bad
        case_dir.mkdir()
        scenario = _scenario(
            attempt1=[_snapshot(_backend_failure_page())],
            attempt2=[_snapshot(_page([_new_backend(bad)]))],
            run_attempt_sequence=[1, 1, 2],
        )
        run = _run(case_dir, scenario)
        assert run.proc.returncode == 0, run.proc.stderr
        assert run.field("classification") == "unknown", bad
        md = run.outputs["attribution_md"]
        assert bad in md, f"{bad} 原因须写进备注"
        assert "预算内未完成" not in md


# ── §3 覆盖8 分页：第二页才有目标 / 部分目标 ─────────────────────────────────

def test_pagination_collects_second_page_before_classifying(tmp_path: Path):
    """快照两页凑齐两个目标；轮询首轮第二页缺目标 → 等，收齐后才分类。"""
    attempt1 = [_snapshot(
        _page([_job(_OLD_BACKEND_ID, "backend-test", conclusion="failure",
                    steps=[_failed("x")])], total_count=2),
        _page([_job(_OLD_FRONTEND_ID, "frontend-check", conclusion="failure",
                    steps=[_failed("x")])], total_count=2),
    )]
    attempt2 = [
        _snapshot(
            _page([_new_backend("success")], total_count=2),
            _page([_job(777001, "docker-build", conclusion="success", run_attempt=2)], total_count=2),
        ),
        _snapshot(
            _page([_new_backend("success")], total_count=2),
            _page([_job(_NEW_FRONTEND_ID, "frontend-check", conclusion="success",
                        run_attempt=2)], total_count=2),
        ),
    ]
    scenario = _scenario(attempt1=attempt1, attempt2=attempt2,
                         run_attempt_sequence=[1, 1, 2, 2])
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert any("page=2" in e for e in run.jobs_calls(1)), "快照必须取第二页"
    assert len([e for e in run.jobs_calls(2) if "&page=1" in e]) == 2
    assert len([e for e in run.jobs_calls(2) if "&page=2" in e]) == 2
    md_snapshot = run.outputs["failed_jobs_md"]
    assert "backend-test" in md_snapshot and "frontend-check" in md_snapshot


# ── §3 覆盖9 顺序 / 单次重跑 / 回归 ───────────────────────────────────────────

def test_evidence_before_rerun_and_exactly_one_rerun(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("failure")]))],
        run_attempt_sequence=[1, 1, 2],
        logs={str(_OLD_BACKEND_ID): "2026-10-02T06:42:39Z FAILED backend/tests/test_x.py::test_y\n"},
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    rerun_call = run.call_index(lambda c: c[:2] == ["run", "rerun"])
    snapshot_jobs = run.call_index(lambda c: c[:1] == ["api"] and any(
        "/attempts/1/jobs" in a for a in c))
    log_call = run.call_index(lambda c: c[:1] == ["api"] and any(
        "/actions/jobs/" in a for a in c))
    assert snapshot_jobs < rerun_call, "红灯清单必须先于重跑"
    assert log_call < rerun_call, "失败日志证据必须先于重跑"
    assert len(run.rerun_calls()) == 1, "自动重跑恰好一次"
    # 最终轮询只读固定 A+1
    assert run.jobs_calls(1) and not [
        c for c in run.calls[rerun_call + 1:]
        if any("/attempts/1/jobs" in a for a in c if a.startswith("repos/"))
    ]


def test_rerun_request_failure_unknown_without_polling(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1],
        rerun_ok=False,
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "重跑请求失败" in run.outputs["attribution_md"]
    assert len(run.rerun_calls()) == 1
    assert run.jobs_calls(2) == []


def test_attempt_gt_1_no_rerun_regression(tmp_path: Path):
    """既有「attempt>1 不再自动重跑」分支保留：快照改读固定 attempt A=2。"""
    attempt2_snapshot = _snapshot(_page([
        _job(_NEW_BACKEND_ID, "backend-test", conclusion="failure", run_attempt=2,
             steps=[_failed("Run backend tests")]),
    ]))
    scenario = _scenario(attempt2=[attempt2_snapshot], run_attempt_sequence=[2])
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "deterministic"
    assert run.field("rerun_conclusion") == "failure"
    assert "不再自动重跑" in run.outputs["attribution_md"]
    assert run.rerun_calls() == []
    assert run.jobs_calls(2) and len(run.jobs_calls(2)) == 1


def test_output_contract_fields_and_summary_line(tmp_path: Path):
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("failure")]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path, scenario)
    assert set(run.outputs) >= {
        "classification", "rerun_conclusion", "wait_budget", "failed_jobs_md", "attribution_md",
    }
    assert run.outputs["classification"] in {"flake", "deterministic", "unknown"}
    assert "backstop_attribution attempt=1" in run.proc.stdout
    assert "classification=deterministic" in run.proc.stdout


def test_vitest_case_name_extracted_in_real_path(tmp_path: Path):
    """真实路径下 vitest 双空格形态仍能进归因块（判据 3 回归）。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1, 2],
        logs={str(_OLD_BACKEND_ID): _VITEST_LINE},
    )
    run = _run(tmp_path, scenario)
    assert run.proc.returncode == 0, run.proc.stderr
    assert "tmpRedTimingProbe.test.ts" in run.outputs["attribution_md"]


# ── §3.1 轮询节奏：sleep 次数 / 参数 / stderr（v1.1 返修新增验收）────────────
# 缺陷形态：wait_conclusion 曾用 Bash 特殊变量 `_` 当循环计数器，被
# `line="$(poll_once ...)"` 覆盖为空 → 整数比较报错并**跳过全部 sleep**，
# 待定目标以 API 调用速度烧完名义预算。以下用正 WAIT_INTERVAL + sleep 桩钉住节奏。

def test_pending_three_rounds_sleeps_exactly_twice_with_interval(tmp_path: Path):
    """连续 3 轮 pending → 预算耗尽：恰 2 次 sleep(30)，不提前退出、无整数比较错误。"""
    in_progress = _snapshot(_page([_new_backend("failure", status="in_progress")]))
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[in_progress],  # 复读最后一帧：三轮都 pending
        run_attempt_sequence=[1, 1, 2, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_interval="30", env={"WAIT_MAX_SLOW": "3"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "unknown"
    assert "未在预算内完成" in run.outputs["attribution_md"]
    assert len(run.jobs_calls(2)) == 3, "预算 3 轮必须跑满，不得提前退出"
    assert "integer expression expected" not in run.proc.stderr, run.proc.stderr
    assert run.sleep_calls == ["30", "30"], f"应恰两次 sleep(30)：{run.sleep_calls!r}"


def test_pending_then_success_sleeps_once_then_flake(tmp_path: Path):
    """首轮 pending → 次轮 success：恰 1 次 sleep(30)，随后 flake（落定后不补 sleep）。"""
    scenario = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[
            _snapshot(_page([_new_backend("failure", status="in_progress")])),
            _snapshot(_page([_new_backend("success")])),
        ],
        run_attempt_sequence=[1, 1, 2, 2],
    )
    run = _run(tmp_path, scenario, wait_interval="30", env={"WAIT_MAX_SLOW": "5"})
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.field("classification") == "flake"
    assert len(run.jobs_calls(2)) == 2
    assert "integer expression expected" not in run.proc.stderr, run.proc.stderr
    assert run.sleep_calls == ["30"], f"应恰一次 sleep(30)：{run.sleep_calls!r}"


def test_immediate_settle_and_fatal_unknown_do_not_sleep(tmp_path: Path):
    """即时 success 与致命 unknown（非 404 API 错误）均 0 次 sleep。"""
    immediate = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[_snapshot(_page([_new_backend("success")]))],
        run_attempt_sequence=[1, 1, 2],
    )
    run = _run(tmp_path / "ok", immediate, wait_interval="30")
    assert run.field("classification") == "flake"
    assert run.sleep_calls == [], f"落定即返回，不应 sleep：{run.sleep_calls!r}"

    fatal = _scenario(
        attempt1=[_snapshot(_backend_failure_page())],
        attempt2=[{"http_error": {"status": 500, "message": "Internal Server Error"}}],
        run_attempt_sequence=[1, 1, 2],
    )
    run2 = _run(tmp_path / "fatal", fatal, wait_interval="30")
    assert run2.field("classification") == "unknown"
    assert run2.sleep_calls == [], f"致命 unknown 不应 sleep：{run2.sleep_calls!r}"
    assert "integer expression expected" not in run2.proc.stderr, run2.proc.stderr
