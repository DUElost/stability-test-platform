"""#2402 —— dev 假 Agent 夹具的红线与纯逻辑自证（tools/dev/fake_agent.py）。

这里不连任何控制面：**红线本身**才是被测对象。三条红线各配正反两面，
其中「不会误打生产」用「HTTP 一次都没发出去」来证明，而不是只看代码写了什么。
"""
from __future__ import annotations

import ast
import importlib.util
import json
import pathlib
from types import SimpleNamespace

import pytest

TOOL = pathlib.Path(__file__).resolve().parents[1] / "tools" / "dev" / "fake_agent.py"


@pytest.fixture(scope="module")
def fa():
    spec = importlib.util.spec_from_file_location("dev_fake_agent", TOOL)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── 红线 1：只注册与回报，绝不执行脚本 ─────────────────────────────────────

def test_fixture_has_no_execution_surface(fa):
    """AST 级红线守卫：夹具**不得**引入执行面（防"顺手加一行就好"）。

    用 AST 而不是子串匹配：本文件的 docstring 里就要写「不 import subprocess」，
    子串判据会把说明本身判成违规（第一版就是这么红的）。
    """
    tree = ast.parse(TOOL.read_text(encoding="utf-8"))
    banned_modules = {"subprocess", "pty", "shutil", "asyncio"}
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned_modules), f"夹具引入了执行面模块：{sorted(imported & banned_modules)}"

    # 也不得出现 os.system / os.popen / eval / exec 这类调用
    bad_calls: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", None) or getattr(func, "id", None)
        if name in {"system", "popen", "exec", "eval", "spawn", "fork", "kill"}:
            bad_calls.append(name)
    assert bad_calls == [], f"夹具里出现了执行类调用：{bad_calls}"


def test_push_events_are_only_acked(fa):
    """服务端推来的执行类事件在夹具里没有实现——只有白名单回报事件可被 emit。"""
    assert "execute_job" in fa.PUSH_EVENTS and "run_job" in fa.PUSH_EVENTS
    assert set(fa.AGENT_EMIT_EVENTS) == {"step_log", "step_update", "job_status", "heartbeat"}


def test_verify_scripts_delegates_to_read_only_hasher(fa, monkeypatch):
    """派发门禁的应答复用生产实现（只读+sha256），夹具不另写一套哈希。"""
    calls: list = []

    def _fake(expected, *, host_id):
        calls.append((expected, host_id))
        return {"host_id": host_id, "results": []}

    monkeypatch.setitem(
        __import__("sys").modules, "backend.agent.script_verifier",
        SimpleNamespace(verify_scripts_payload=_fake),
    )
    out = fa.verify_scripts_ack([{"name": "gpu_check", "version": "1.0.0"}], host_id="192-0-2-11")
    assert out["host_id"] == "192-0-2-11"
    assert calls == [([{"name": "gpu_check", "version": "1.0.0"}], "192-0-2-11")]


# ── 红线 2：只打 dev 栈 ───────────────────────────────────────────────────

@pytest.mark.parametrize("base", [
    "http://127.0.0.1:8000",       # 本机生产控制面
    "http://localhost:8000",
    "http://control-plane.internal",  # 无端口 = 80/443，同样不是 dev
])
def test_non_dev_target_is_refused(fa, base):
    with pytest.raises(fa.FixtureRefused):
        fa.guard_target(base)


def test_dev_target_passes_and_default_is_dev_port(fa):
    assert fa.guard_target("http://127.0.0.1:18000") == "http://127.0.0.1:18000"
    assert fa.DEV_DEFAULT_BASE.endswith(":18000"), "默认必须落在 dev 映射端口"


def test_heartbeat_never_reaches_production(fa, monkeypatch, capsys):
    """越过红线时**一个请求都不发**：先判目标、后连网，顺序错了这条就会红。"""
    monkeypatch.setenv("AGENT_SECRET", "unit-test-secret")
    called: list = []
    monkeypatch.setattr(fa.urllib.request, "urlopen", lambda *a, **k: called.append(a))
    code = fa.main(["--base", "http://127.0.0.1:8000", "heartbeat", "--count", "3"])
    assert code == 2, "被拒必须以非零退出，而不是静默跳过"
    assert called == [], "拒绝之前不得发出任何 HTTP"
    err = capsys.readouterr().err
    assert "unit-test-secret" not in err, "报错文本不得带出凭据"


def test_allow_non_dev_target_is_explicit(fa):
    assert fa.guard_target("http://127.0.0.1:8000", allow_non_dev=True) == "http://127.0.0.1:8000"


# ── 红线 3：凭据与 fencing token 不外泄 ────────────────────────────────────

def test_missing_secret_refuses_without_leaking(fa, monkeypatch):
    monkeypatch.delenv("AGENT_SECRET", raising=False)
    with pytest.raises(fa.FixtureRefused) as exc:
        fa.require_secret()
    assert "AGENT_SECRET" in str(exc.value)


def test_step_command_does_not_print_fencing_token(fa, monkeypatch, capsys):
    token = "SUPERSECRETTOKEN-88f3"
    monkeypatch.setenv("AGENT_SECRET", "unit-test-secret")
    sent: list = []
    monkeypatch.setattr(fa, "fencing_token_for", lambda job: token)
    monkeypatch.setattr(
        fa, "post_json",
        lambda base, secret, path, body, **kw: sent.append((path, body)) or (200, {"ok": True}),
    )
    args = fa.parse_cli([
        "--base", "http://127.0.0.1:18000", "step",
        "--job", "12", "--step", "step_init_1", "--status", "RUNNING", "--log-file", "",
    ])
    assert fa.cmd_step(args) == 0
    # token 必须真的被带上（否则回报会被 403 拒），但**不得**出现在任何输出里
    assert sent and sent[0][1]["fencing_token"] == token
    out = capsys.readouterr().out
    assert token not in out
    assert "step_init_1" in out


def test_claim_reports_only_presence_of_token(fa, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_SECRET", "unit-test-secret")
    monkeypatch.setattr(
        fa, "post_json",
        lambda *a, **k: (200, {"data": [{"id": 1, "device_id": 2, "status": "RUNNING",
                                         "fencing_token": "TOKENVALUE-42"}]}),
    )
    args = fa.parse_cli([
        "--base", "http://127.0.0.1:18000", "claim", "--log-file", "",
    ])
    assert fa.cmd_claim(args) == 0
    out = capsys.readouterr().out
    assert "TOKENVALUE-42" not in out and "True" in out, "只报「有没有」，不报值"


# ── 夹具自身的可用性（那 4 个坑的处理）─────────────────────────────────────

def test_heartbeat_payload_shape(fa):
    payload = fa.build_heartbeat_payload(3, device_count=4, host_ip="192.0.2.11")
    assert payload["host_id"] == "0", "自动注册哨兵：按 IP 建/找主机"
    assert len(payload["devices"]) == 4
    # 平台交替（i 为奇数 → qualcomm）：便于验平台维度筛选与染色
    assert [d["platform"] for d in payload["devices"]] == ["qualcomm", "mtk", "qualcomm", "mtk"]
    third = payload["devices"][2]
    # 奇数轮把第 3 台置 offline：制造一次可观察的状态翻转，供实时面断言
    assert third["adb_connected"] is False and third["adb_state"] == "offline"
    even = fa.build_heartbeat_payload(2, device_count=3)["devices"][2]
    assert even["adb_state"] == "device"


def test_transport_preference_matches_dev_reality(fa):
    """auto 先试生产契约的 websocket（#1121），镜像缺 websocket-client 才退 polling。"""
    assert fa._transport_order("auto") == ["websocket", "polling"]
    assert fa._transport_order("polling") == ["polling"]
    assert fa._transport_order("websocket") == ["websocket"]


def test_inject_file_dedupes_by_mtime_and_rejects_unknown_events(fa, tmp_path, capsys):
    path = tmp_path / "cmd.json"
    path.write_text(json.dumps([
        {"event": "step_log", "data": {"job_id": 1, "line": "hello"}},
        {"event": "execute_job", "data": {"cmd": "rm -rf /"}},
    ]), encoding="utf-8")
    sent: list = []
    seen = fa._drain_inject_file(str(path), lambda e, d: sent.append((e, d)), None, None)
    assert seen and sent == [("step_log", {"job_id": 1, "line": "hello"})], (
        "白名单外的注入事件必须被拒——夹具不是任意事件的转发器"
    )
    again = fa._drain_inject_file(str(path), lambda e, d: sent.append((e, d)), seen, None)
    assert again == seen and len(sent) == 1, "同一 mtime 不得重复消费"
    out = capsys.readouterr().out
    assert "execute_job" in out and "rm -rf" not in out


def test_inject_command_rejects_non_agent_events(fa):
    args = fa.parse_cli([
        "--base", "http://127.0.0.1:18000", "inject", "--event", "execute_job",
        "--data", "{}", "--log-file", "",
    ])
    assert fa.cmd_inject(args) == 2, "事件白名单在连接建立之前就要挡下"
