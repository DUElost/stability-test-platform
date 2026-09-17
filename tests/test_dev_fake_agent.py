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
    """服务端推来的执行类事件在夹具里没有实现——只有白名单回报事件可被 emit。

    （``control`` 的回执在 #2518 起不再是「只回 ack」：abort 要回报终态，见下方用例。）"""
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

# ── 入站接线（#2470）：只回 ack，但必须真的在听 ────────────────────────────


class _FakeClient:
    """`socketio.Client` 的最小替身：记录 on/emit/call，不起任何网络。"""

    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str | None], object] = {}
        self.outbound: list[str] = []
        self.connected_to: str | None = None

    def connect(self, base, **kwargs):
        self.connected_to = base

    def on(self, event, handler, namespace=None):
        self.handlers[(event, namespace)] = handler

    def emit(self, event, data=None, namespace=None):
        self.outbound.append(event)

    def call(self, event, data=None, namespace=None, timeout=None):
        self.outbound.append(event)
        return {"ok": True}

    def disconnect(self):
        pass


def test_push_handlers_cover_every_push_event(fa):
    """PUSH_EVENTS 不再只是常量：每个事件都必须在 /agent 上挂到可调用 handler。

    #2470 的形态正是「常量齐全、无人消费」——`serve` 从不注册入站 handler，
    派发门禁的 `verify_scripts` RPC 等不到 ack，plan-run 停在队列里一行 job 都不
    产生，且日志里没有任何痕迹。
    """
    client = _FakeClient()
    registered = fa.register_push_handlers(client, host_id="192-0-2-11")
    assert set(registered) == set(fa.PUSH_EVENTS)
    assert set(client.handlers) == {(ev, fa.AGENT_NS) for ev in fa.PUSH_EVENTS}
    assert all(callable(h) for h in client.handlers.values())


def test_inbound_handlers_ack_and_never_execute(fa, monkeypatch):
    """verify_scripts 交给生产只读哈希器，其余只回 ok；且 handler 不得晚绑成同一事件。"""
    seen: list = []

    def _fake_ack(expected, *, host_id):
        seen.append((expected, host_id))
        return {"host_id": host_id, "results": [{"ok": True}]}

    monkeypatch.setattr(fa, "verify_scripts_ack", _fake_ack)
    client = _FakeClient()
    fa.register_push_handlers(client, host_id="h1")

    # PUSH_EVENTS 里 verify_scripts 不是最后一个：若闭包晚绑，这里拿到的会是
    # 最后一个事件的 handler，返回 {"ok": True} 而不是下面的应答体。
    ack = client.handlers[("verify_scripts", fa.AGENT_NS)]({"expected": [{"name": "gpu_setup"}]})
    assert ack == {"host_id": "h1", "results": [{"ok": True}]}
    assert seen == [([{"name": "gpu_setup"}], "h1")]

    for event in ("execute_job", "run_job", "dispatch", "job_command"):
        assert client.handlers[(event, fa.AGENT_NS)]({"do": "anything"}) == {"ok": True}
    # control 自 #2518 起带回报字段：无命令/非 abort 时仍是「什么都不做」的 ack
    assert client.handlers[("control", fa.AGENT_NS)]({"do": "anything"}) == {
        "ok": True, "command": "", "reported_aborted": [],
    }
    # 载荷缺 expected 键也要走同一委派路径（生产侧总是带键，夹具不因此崩）
    assert fa.push_ack("verify_scripts", None, host_id="h2") == {
        "host_id": "h2", "results": [{"ok": True}]}
    assert seen[-1] == ([], "h2")


def test_serve_wires_inbound_handlers_but_short_commands_do_not(fa, monkeypatch):
    """连接层按调用方决定挂不挂入站 handler：`serve` 必挂，短命令不挂。

    这一条钉的是「接线位置」——handler 注册必须发生在真正长期在线的子命令上，
    否则常量与注册各对一半，仍然是「看得见、答不出」。
    """
    clients: list[_FakeClient] = []
    monkeypatch.setitem(
        __import__("sys").modules, "socketio",
        SimpleNamespace(Client=lambda *a, **kw: clients.append(_FakeClient()) or clients[-1]),
    )
    monkeypatch.setenv("AGENT_SECRET", "not-a-real-secret")
    args = fa.parse_cli(["--log-file", "", "serve", "--lifetime", "0"])
    assert fa.cmd_serve(args) == 0
    assert clients, "serve 没建过连接"
    assert {(ev, ns) for (ev, ns) in clients[-1].handlers} == {
        (ev, fa.AGENT_NS) for ev in fa.PUSH_EVENTS}

    rc = fa._with_connection(args, lambda emit: None)
    assert rc == 0
    assert clients[-1].handlers == {}, "短命令路径不该挂入站 handler"


# ── 红线 2 的容器内寻址（#2470 第 3 项）───────────────────────────────────


def test_in_container_admits_loopback_only_with_fingerprint(fa):
    """容器内 loopback:8000 放行；宿主上同地址、无指纹、非 loopback 一律拒。"""
    admit = dict(in_container=True, container_probe=lambda: True)
    assert fa.guard_target("http://127.0.0.1:8000", **admit) == "http://127.0.0.1:8000"
    with pytest.raises(fa.FixtureRefused):
        fa.guard_target("http://127.0.0.1:8000", in_container=True,
                        container_probe=lambda: False)
    with pytest.raises(fa.FixtureRefused):
        fa.guard_target("http://127.0.0.1:8000")
    with pytest.raises(fa.FixtureRefused):
        fa.guard_target("http://10.0.2.99:8000", **admit)
    # 声明了容器内也不放宽 dev 端口那条路（18000 仍按常规判据通过）
    assert fa.guard_target("http://127.0.0.1:18000", in_container=True,
                           container_probe=lambda: False) == "http://127.0.0.1:18000"


def test_in_container_is_not_the_red_line_escape(fa):
    """两个开关语义不合并：--in-container 不放过非 dev 的第三方地址。"""
    with pytest.raises(fa.FixtureRefused):
        fa.guard_target("http://control.prod:8000", in_container=True,
                        container_probe=lambda: True)
    assert fa.guard_target("http://control.prod:8000", allow_non_dev=True)


def test_cli_defaults_and_docstring_show_the_runnable_form(fa):
    """默认不开容器模式；文档里必须给出容器内那条**实际可跑**的写法（#2470 的起因）。"""
    assert fa.parse_cli(["heartbeat"]).in_container is False
    assert fa.parse_cli(["--in-container", "heartbeat"]).in_container is True
    assert fa.parse_cli(["--in-container", "heartbeat"]).base == fa.DEV_DEFAULT_BASE
    doc = TOOL.read_text(encoding="utf-8")
    assert "--in-container" in doc and "http://127.0.0.1:8000" in doc


# ── #2518：control(abort) 回报终态（不再只回 ack）────────────────────────────

def test_control_abort_reports_terminal_state_for_each_job(fa, monkeypatch):
    """服务端扇出 abort 后，夹具必须像守约 Agent 一样回报 ABORTED。

    否则 abort_reaper 判 abort_ack_timeout → UNKNOWN → 再等 300s 宽限，
    dev 里测中止面必然 6 分钟起步（快路径永不进回归）。
    """
    monkeypatch.setenv("AGENT_SECRET", "unit-test-secret")
    monkeypatch.setattr(fa, "fencing_token_for", lambda job: f"TOKEN-{job}")
    sent: list = []
    monkeypatch.setattr(
        fa, "post_json",
        lambda base, secret, path, body, **kw: sent.append((path, body)) or (200, {"ok": True}),
    )

    ack = fa.control_ack(
        {"command": "abort", "payload": {"plan_run_id": 21, "job_ids": [14, 15], "reason": "op"}},
        host_id="192-0-2-11", base="http://127.0.0.1:18000",
    )

    assert ack["reported_aborted"] == [14, 15]
    assert [path for path, _ in sent] == [
        "/api/v1/agent/jobs/14/complete",
        "/api/v1/agent/jobs/15/complete",
    ]
    assert all(body["update"]["status"] == "ABORTED" for _, body in sent)


def test_control_non_abort_command_is_bare_ack(fa, monkeypatch):
    """其它 control 命令（如 reload_config）保持只回 ack——不越权做任何事。"""
    posted: list = []
    monkeypatch.setattr(fa, "post_json", lambda *a, **k: posted.append(a) or (200, {}))

    ack = fa.control_ack(
        {"command": "reload_config", "payload": {}},
        host_id="192-0-2-11", base="http://127.0.0.1:18000",
    )

    assert ack == {"ok": True, "command": "reload_config", "reported_aborted": []}
    assert posted == []


def test_push_ack_routes_control_through_control_ack(fa, monkeypatch):
    monkeypatch.setattr(
        fa, "control_ack",
        lambda payload, *, host_id, base: {"via": "control_ack", "base": base},
    )
    assert fa.push_ack("control", {"command": "abort"}, host_id="h1", base="http://dev") == {
        "via": "control_ack", "base": "http://dev",
    }


def test_complete_accepts_aborted_status(fa):
    """`complete --status ABORTED` 必须可用——中止链上唯一正确的终态值。"""
    args = fa.parse_cli([
        "--base", "http://127.0.0.1:18000", "complete", "--job", "17", "--status", "ABORTED",
    ])
    assert args.status == "ABORTED"


def test_complete_reports_selected_status(fa, monkeypatch):
    """CLI 的 --status 要真的发到 patch 体里（#2518 的 report_job_status 复用点）。"""
    monkeypatch.setenv("AGENT_SECRET", "unit-test-secret")
    monkeypatch.setattr(fa, "fencing_token_for", lambda job: None)
    sent: list = []
    monkeypatch.setattr(
        fa, "post_json",
        lambda base, secret, path, body, **kw: sent.append((path, body)) or (200, {"ok": True}),
    )

    args = fa.parse_cli([
        "--base", "http://127.0.0.1:18000", "complete", "--job", "17",
        "--status", "ABORTED", "--log-file", "",
    ])
    assert fa.cmd_complete(args) == 0
    assert sent[0][0] == "/api/v1/agent/jobs/17/complete"
    assert sent[0][1]["update"]["status"] == "ABORTED"
