"""Real frozen 1.0.8, package verification and Engine/pump; only device I/O is fake."""
from __future__ import annotations

import importlib.util
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import requests

from backend.agent import api_client, pipeline_engine
from backend.agent.contracts.device_return import ONBOARDED
from backend.agent.operation_scheduler import OperationScheduler
from backend.agent.registry.local_db import LocalDB
from backend.agent.registry.script_registry import ScriptEntry
from backend.agent.script_packages import resolve_script_path

PACKAGE = "98cc16f82fa6c56fe24f37151958f4ad3efd2f5983b1bba36f9340d95cd4a557"
ROOT = Path(__file__).resolve().parents[3]


class FrozenReturnDevice:
    def __init__(self, monkeypatch, tmp_path, *, kind="clear", trusted=True, supported=True):
        self.kind, self.trusted = kind, trusted
        self.alive = kind == "footprint"
        self.writes: list[str] = []
        self.calls: list[str] = []
        self.modes: list[str] = []
        self.reports: list[dict] = []
        self.report_statuses: list[int | Exception] = []
        self.interrupt: int | None = None
        self.clock = 0.0
        self.missing = False
        self.lease_status = 200
        self.scheduler = OperationScheduler(max_concurrent=1)
        self.combination = ONBOARDED["power-cycle"]["verified_device_classes"][0]
        self.model = self.combination["model"] if trusted else "UNVERIFIED_TEST_MODEL"
        self.xml = ('<map><boolean name="running" value="' + ("true" if kind == "footprint" else "false")
                    + '"/><boolean name="auto_resume" value="' + ("true" if kind == "footprint" else "false") + '"/></map>')
        spec = importlib.util.spec_from_file_location("u4_frozen_packer", ROOT / "tools/dev/package_tool_asset.py")
        packer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(packer)
        self.packages = tmp_path / "packages"
        self.tarball = self.packages / "powercycle_disarm/1.0.8.tar.gz"
        self.tarball.parent.mkdir(parents=True)
        packed = packer.build_deterministic_tar_gz(ROOT / "backend/agent/scripts/powercycle_disarm", self.tarball)
        assert packed["package_sha256"] == PACKAGE
        self.cache = tmp_path / "cache"
        monkeypatch.setenv("STP_PACKAGES_ROOT", str(self.packages))
        monkeypatch.setenv("STP_TOOLS_CACHE_ROOT", str(self.cache))
        monkeypatch.setenv("STP_DEVICE_SERIAL", "u4-test-device")
        self.entry = ScriptEntry(1, "powercycle_disarm", "1.0.8", "python", "powercycle_disarm.py", "", PACKAGE)
        resolved = resolve_script_path(self.entry)
        assert Path(resolved.path).is_file()
        lib_spec = importlib.util.spec_from_file_location("u4_frozen_lib", Path(resolved.cwd) / "_lib.py")
        lib = importlib.util.module_from_spec(lib_spec)
        lib_spec.loader.exec_module(lib)
        monkeypatch.setitem(sys.modules, "_lib", lib)
        mod_spec = importlib.util.spec_from_file_location("u4_frozen_script", resolved.path)
        self.script = importlib.util.module_from_spec(mod_spec)
        mod_spec.loader.exec_module(self.script)
        self.lib = lib
        monkeypatch.setattr(lib, "adb", self.adb)
        monkeypatch.setattr(lib, "push_prefs_xml", self.write_prefs)
        clock = SimpleNamespace(sleep=self.advance, monotonic=lambda: self.clock, time=lambda: self.clock)
        monkeypatch.setattr(lib, "time", clock)
        monkeypatch.setattr(self.script, "time", clock)
        self.registry = SimpleNamespace(resolve=self.resolve, version="test-only", initialize=lambda: None)
        self.local_db = LocalDB()
        self.local_db.initialize(str(tmp_path / "agent-state.db"))
        self.local_db.set_state("return_init_check_available", "1" if supported else "0")
        monkeypatch.setattr(pipeline_engine, "discover_devices", lambda *_: [{"serial": "u4-test-device", "model": self.model}])
        monkeypatch.setattr(pipeline_engine, "detect_device_platform", lambda *_: "MTK")
        monkeypatch.setattr(pipeline_engine.subprocess, "Popen", self.popen)
        monkeypatch.setattr(pipeline_engine, "_remember_process_group", lambda *_: None)
        monkeypatch.setattr(pipeline_engine, "_popen_isolation_kwargs", lambda: {})
        monkeypatch.setattr(pipeline_engine, "_IS_WINDOWS", True)  # finite StringIO, real pump
        monkeypatch.setattr(pipeline_engine, "_terminate_process_tree", self.forbid_signals)
        monkeypatch.setattr(os, "kill", self.forbid_signals)
        monkeypatch.setattr(os, "killpg", self.forbid_signals)
        monkeypatch.setattr(requests, "post", self.post)
        monkeypatch.setattr(api_client, "time", SimpleNamespace(sleep=self.advance))
        self.mq = MagicMock()
        self.mq.connected = True

    def forbid_signals(self, *args, **kwargs):
        raise AssertionError("U4 test cannot signal the host")

    def advance(self, seconds):
        self.clock += seconds
        assert self.clock <= 20000, "finite virtual clock exhausted"

    def resolve(self, name, version):
        assert (name, version) == ("powercycle_disarm", "1.0.8")
        if self.missing:
            raise LookupError("test registration unavailable")
        return self.entry

    def write_prefs(self, xml):
        assert self.scheduler.held == 1 and 1 in self.scheduler._held_devices
        self.writes.append(xml)
        self.xml, self.kind = xml, "clear"

    def adb(self, *args, **kwargs):
        assert self.scheduler.held == 1 and 1 in self.scheduler._held_devices
        command = " ".join(args)
        self.calls.append(command)
        self.lib.exec_progress_tick()
        if command == "get-state":
            return 0, "device", ""
        if command == "shell id -u":
            return 0, "0", ""
        if "getprop sys.boot_completed" in command:
            return 0, "1", ""
        if "getprop ro.soc.manufacturer" in command:
            return 0, "mediatek", ""
        if "getprop ro.product.model" in command:
            return 0, self.model.replace("_", "-"), ""
        if "pm path " in command:
            return (1, "", "") if self.kind == "not_installed" else (0, "package:/data/app/base.apk", "")
        if "sha256sum " in command:
            return 0, self.combination["autotesttool_apk_sha256"] + "  /data/app/base.apk", ""
        if "dumpsys package " in command:
            return 0, "userId=1000", ""
        if "dumpsys activity services " in command:
            return 0, "PowerCycleService" if self.alive else "", ""
        if "am startservice " in command or "am force-stop " in command:
            self.alive = False
            return 0, "", ""
        if "if [ ! -f " in command:
            if self.kind in ("absent", "not_installed"):
                return 0, "__STP_PREFS_ABSENT__", ""
            if self.kind == "denied":
                return 1, "", "Permission denied"
            return 0, self.xml, ""
        if any(term in command for term in ("chown ", "chmod ", "mkdir ")):
            return 0, "", ""
        raise AssertionError("Unexpected device I/O: " + command)

    def popen(self, args, *, env, **kwargs):
        assert self.scheduler.held == 1 and 1 in self.scheduler._held_devices
        assert Path(args[1]).read_bytes() == (self.cache / "powercycle_disarm/1.0.8/powercycle_disarm.py").read_bytes()
        params = json.loads(env["STP_STEP_PARAMS"])
        self.modes.append(params.get("mode", "disarm"))
        captured = io.StringIO()
        self.lib.sys = SimpleNamespace(stderr=captured)
        value = self.script._run(params)  # frozen script and frozen stop/readback logic
        code = self.interrupt or (0 if value["success"] else 1)
        return SimpleNamespace(pid=99999999, returncode=code,
            stdout=io.StringIO(json.dumps(value)), stderr=io.StringIO(captured.getvalue()),
            poll=lambda: code, wait=lambda **_: code)

    def post(self, url, *, json, **kwargs):
        response = requests.Response()
        response.url = url
        response.status_code = 200
        response._content = b'{"data":{}}'
        if url.endswith("/extend_lock"):
            response.status_code = self.lease_status
        elif url.endswith(("/return-check", "/return-discharge")):
            self.reports.append(deepcopy(json))
            status = self.report_statuses.pop(0) if self.report_statuses else 200
            if isinstance(status, Exception):
                raise status
            response.status_code = status
            if status == 422:
                response._content = b'{"detail":{"code":"UNVERIFIED_PROBE_RESULT","violations":[{"family":"power-cycle"}]}}'
        else:
            raise AssertionError("Unexpected HTTP: " + url)
        return response

    def engine(self):
        return pipeline_engine.PipelineEngine(SimpleNamespace(adb_path="test-only"), "u4-test-device", 1,
            script_registry=self.registry, local_db=self.local_db, api_url="http://u4-test.invalid",
            fencing_token="test-token", operation_scheduler=self.scheduler, device_id=1,
            mq_producer=self.mq, nfs_root="")

    def execute(self):
        return self.engine().execute({"lifecycle": {"init": [], "teardown": []}})

    def close(self):
        self.local_db.close()
