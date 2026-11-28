"""stp-agent-priv `usbnet-*` 受限提权通道契约与全量边界反例测试（#4212 / ADR-0037 D10 / ADR-0066 D6）。

完整覆盖批次 B13 #4212 §4.2 U3 六大反例与全部强控制项：
1. 反例 ①：root 属主 `/etc/stp-usbnet.conf` 未授权（`usbnet_enabled != true`）或缺失/symlink/权限过宽/
   属主不符/超 64 KiB/JSON 非法时，全部六个 `usbnet-*` 子命令（含 `usbnet-status`）一律退出码 2
   fail-closed（`STP_USBNET_DISABLED` / `STP_AGENT_PRIV_ERROR`），不修改任何系统状态；非 root 退出码 2；
2. 反例 ②：自洽但未在 `allowed_usb_ports`、非 `{rndis_host, cdc_ncm}` 驱动、位于 `/sys/devices/virtual/`、
   为宿主机默认路由上联网卡/`lo`/`docker0`/`br-*`、或缺少合法 Ed25519 `usbnet_grant`（签名篡改 / 过期 /
   未来 `iat > now+30` / TTL 不在 300..1800s / `host_id` 不符 / `egress` 未授权）均被 `usbnet-attach` 拒绝；
   同时使用 `cryptography` 真实生成的 Ed25519 密钥对与 RFC 8032 测试向量交叉验证纯 Python Ed25519 验签；
3. 反例 ③：代次比较域成对验收——同一设备会话释放 / `usbnet-revoke` / 换代后未过期旧 `grant` 被
   `usbnet-attach` 拒绝（退出码 3 + `STP_USBNET_GRANT_REVOKED`），同时**同一物理 USB 口换入低代次新设备**
   后新设备合法 `usbnet_grant` 仍可正常挂桥（不按 `usb_devpath` 跨设备比较代次）；且旧授权过期或撤销后，
   `usbnet-detach` 仍可基于台账安全摘除该旧绑定；`usbnet-revoke` 不删除 `active_bindings`；
4. 反例 ④：`usbnet-detach` 遇到接口名被另一活跃会话复用（`ifindex`/`usb_devpath`/`mac` 不符）或挂在非本受控
   他桥上时，绝不触碰当前同名新接口，仅清理旧台账、写撤销并返回 `already_gone_or_reused`；`--expect-iface` /
   `--expect-devpath` 不符返回 exit 2 + `STP_USBNET_BINDING_MISMATCH`；
5. 反例 ⑤：`usbnet-teardown` 有活跃绑定默认 exit 3（`STP_USBNET_ACTIVE_BINDINGS`）拒绝；空绑定关闭与
   `--force` 关闭均先无条件推进 `min_allowed_iat_by_egress` 并将活跃绑定写入 `revoked_sessions` 原子落盘，
   再清理并执行零残留复核（失败保留撤销屏障并返回 exit 3 + `STP_USBNET_TEARDOWN_INCOMPLETE`）；
   `usbnet-status` 暴露 `revocation_state` 摘要（含 `min_allowed_iat_by_egress` 与
   `min_allowed_generation_by_serial`，缺席键默认 `0`）；成对验证冷却期满后按真实 `iat > barrier` 新签
   授权可正常挂桥，而 teardown 前签发（含已挂桥及「已签发、尚未挂桥」）旧授权持续被 `STP_USBNET_GRANT_REVOKED` 拒绝；
   验证 `attaching` 两种故障收敛（预写后挂桥前中断由 `usbnet-detach` 返回 `detached_unbridged`；满额时同一
   `attaching` 会话重入 `usbnet-attach` 不自阻塞且核心身份冲突 exit 2）；验证隔离前置与中途失败不转发
   （严格 `down → master → isolated on → 回读 brport/isolated == "1" → up`，隔离失败或回滚未完成时端口
   保持 `DOWN` 不转发）；
6. 反例 ⑥：`usbnet-ensure-bridge` 安装 `INPUT`/`FORWARD`/`DOCKER-USER`/`NAT` 专属规则链阻断宿主本机端口、
   RFC1918/管理网段/Docker 网桥与同桥互访，且 `dnsmasq` 强制 `--port=0 --bind-interfaces --interface=<bridge>`；
   外部命令固定绝对路径、清空环境、<= 30s 超时、不经 shell；Ansible 模板默认禁用且零 sudoers 改动。
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
WRAPPER = ROOT / "backend/agent/stp_agent_priv.py"
UPDATE_PLAYBOOK = ROOT / "tools/ansible/playbooks/update_agent.yml"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_grant(priv_key: Ed25519PrivateKey, payload: dict) -> str:
    payload_seg = _b64url(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    sig = priv_key.sign(payload_seg.encode("ascii"))
    return f"{payload_seg}.{_b64url(sig)}"


def _pubkey_pem(priv_key: Ed25519PrivateKey) -> str:
    return priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")


def _pubkey_raw_b64url(priv_key: Ed25519PrivateKey) -> str:
    return _b64url(priv_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    ))


@pytest.fixture
def wrapper(monkeypatch):
    spec = importlib.util.spec_from_file_location("stp_agent_priv_usbnet", WRAPPER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_require_root", lambda: None)
    monkeypatch.setattr(module, "USBNET_TRUSTED_UID", os.getuid())
    monkeypatch.setattr(module, "USBNET_TRUSTED_GID", os.getgid())
    monkeypatch.setattr(module, "_load_conf", lambda _p: {"INSTALL_DIR": "/opt/stability-test-agent"})
    return module


class _FakeNetHost:
    """可编程假主机：真实 sysfs 符号链接树、/etc/stp-usbnet.conf、/run/stp-agent-priv 与命令模拟器。"""

    def __init__(self, wrapper, monkeypatch, tmp_path: Path):
        self.wrapper = wrapper
        self.monkeypatch = monkeypatch
        self.tmp_path = tmp_path
        self.priv_key = Ed25519PrivateKey.generate()
        self.now = 1_800_000_000
        self.monkeypatch.setattr(wrapper.time, "time", lambda: float(self.now))
        self.monkeypatch.setattr(wrapper.time, "sleep", lambda _s: None)

        # /etc/stp-usbnet.conf
        self.etc_dir = tmp_path / "etc"
        self.etc_dir.mkdir(mode=0o755)
        self.conf_path = self.etc_dir / "stp-usbnet.conf"

        # /run/stp-agent-priv (parent /run is root-owned real dir)
        self.run_parent = tmp_path / "run"
        self.run_parent.mkdir(mode=0o755)
        self.run_dir = self.run_parent / "stp-agent-priv"

        # sysfs roots
        self.devices_root = tmp_path / "sys_devices"
        self.devices_root.mkdir()
        (self.devices_root / "virtual" / "net").mkdir(parents=True)
        self.net_sysfs = tmp_path / "sys_class_net"
        self.net_sysfs.mkdir()
        self.usb_drivers = tmp_path / "sys_bus_usb_drivers"
        self.usb_drivers.mkdir()
        for drv in ("rndis_host", "cdc_ncm", "cdc_ether", "asix"):
            (self.usb_drivers / drv).mkdir()

        # /proc fakes
        self.proc_root = tmp_path / "proc"
        self.proc_root.mkdir()
        self.proc_route = self.proc_root / "net_route"
        self.proc_route.write_text(
            "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"
            "eth0\t00000000\t0100A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n",
            encoding="utf-8",
        )
        self.ip_forward = self.proc_root / "ip_forward"
        self.ip_forward.write_text("0\n", encoding="utf-8")

        # trusted fake binaries
        self.bin_dir = tmp_path / "sbin"
        self.bin_dir.mkdir(mode=0o755)
        self.bins = {}
        for name in ("ip", "bridge", "iptables", "dnsmasq", "nmcli"):
            b = self.bin_dir / name
            b.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            b.chmod(0o755)
            self.bins[name] = b

        monkeypatch.setattr(wrapper, "USBNET_CONF_PATH", str(self.conf_path))
        monkeypatch.setattr(wrapper, "USBNET_RUN_DIR", str(self.run_dir))
        monkeypatch.setattr(wrapper, "_SYSFS_DEVICES_ROOT", str(self.devices_root))
        monkeypatch.setattr(wrapper, "NET_SYSFS_BASE", str(self.net_sysfs))
        monkeypatch.setattr(wrapper, "USB_DRIVERS_SYSFS_BASE", str(self.usb_drivers))
        monkeypatch.setattr(wrapper, "USBNET_PROC_NET_ROUTE", str(self.proc_route))
        monkeypatch.setattr(wrapper, "USBNET_PROC_ROOT", str(self.proc_root))
        monkeypatch.setattr(wrapper, "USBNET_IP_FORWARD_PATH", str(self.ip_forward))
        monkeypatch.setattr(wrapper, "IP_BIN", str(self.bins["ip"]))
        monkeypatch.setattr(wrapper, "BRIDGE_BIN", str(self.bins["bridge"]))
        monkeypatch.setattr(wrapper, "IPTABLES_BIN", str(self.bins["iptables"]))
        monkeypatch.setattr(wrapper, "DNSMASQ_BIN", str(self.bins["dnsmasq"]))
        monkeypatch.setattr(wrapper, "NMCLI_BIN", str(self.bins["nmcli"]))

        # Simulated kernel/network state
        self.commands: list[tuple[list[str], dict]] = []
        self.bridges: set[str] = set()
        self.chains: dict[tuple[str, str], list[list[str]]] = {
            ("filter", "INPUT"): [],
            ("filter", "FORWARD"): [],
            ("filter", "DOCKER-USER"): [],
            ("nat", "POSTROUTING"): [],
        }
        self.iface_state: dict[str, dict] = {}
        self.dnsmasq_pid = 42420
        self.fail_hook = None  # optional callable(argv) -> (rc, out, err) | None

        monkeypatch.setattr(wrapper.subprocess, "run", self._fake_subprocess_run)

        self.write_conf()

    def write_conf(self, **overrides):
        doc = {
            "usbnet_enabled": True,
            "host_id": "host-pilot-01",
            "control_plane_verify_pubkey": _pubkey_pem(self.priv_key),
            "allowed_usb_ports": ["1-1.2", "1-1.3", "1-1.4"],
            "allowed_egresses": ["direct"],
            "usbnet_max_devices": 8,
            "egress_profiles": {
                "direct": {
                    "bridge": "br-stpusb-dir",
                    "subnet_cidr": "10.200.0.0/22",
                    "upstream_dns": ["223.5.5.5", "114.114.114.114"],
                    "uplink_iface": None,
                    "local_dns_forward": False,
                    "vpn_gateway_ips": [],
                },
            },
        }
        doc.update(overrides)
        self.conf_path.write_text(json.dumps(doc), encoding="utf-8")
        self.conf_path.chmod(0o640)
        return doc

    def add_usb_gadget(
        self,
        *,
        iface: str = "usb0",
        devpath: str = "1-1.2",
        serial: str = "MTKDEV001",
        ifindex: int = 11,
        mac: str = "02:11:22:33:44:55",
        driver: str = "rndis_host",
        vendor: str = "0e8d",
        master: str | None = None,
        isolated: str = "0",
    ) -> Path:
        # 清理旧同名接口
        net_link = self.net_sysfs / iface
        if net_link.exists() or net_link.is_symlink():
            net_link.unlink()

        usb_dev = self.devices_root / "pci0000:00" / "0000:00:14.0" / "usb1" / devpath
        usb_intf = usb_dev / f"{devpath}:1.0"
        net_real = usb_intf / "net" / iface
        net_real.mkdir(parents=True, exist_ok=True)
        (usb_dev / "serial").write_text(f"{serial}\n", encoding="utf-8")
        (usb_dev / "idVendor").write_text(f"{vendor}\n", encoding="utf-8")
        (net_real / "ifindex").write_text(f"{ifindex}\n", encoding="utf-8")
        (net_real / "address").write_text(f"{mac}\n", encoding="utf-8")

        dev_link = net_real / "device"
        if dev_link.exists() or dev_link.is_symlink():
            dev_link.unlink()
        dev_link.symlink_to(usb_intf)

        drv_link = usb_intf / "driver"
        if drv_link.exists() or drv_link.is_symlink():
            drv_link.unlink()
        if driver is not None:
            drv_link.symlink_to(self.usb_drivers / driver)

        net_link.symlink_to(net_real)
        self.iface_state[iface] = {
            "net_real": net_real,
            "oper": "down",
            "master": None,
            "isolated": isolated,
        }
        if master is not None:
            self._set_master(iface, master, isolated=isolated)
        return net_real

    def _set_master(self, iface: str, bridge: str | None, isolated: str = "0"):
        st = self.iface_state[iface]
        net_real: Path = st["net_real"]
        master_link = net_real / "master"
        brport_dir = net_real / "brport"
        if master_link.exists() or master_link.is_symlink():
            master_link.unlink()
        if bridge is None:
            if brport_dir.exists():
                for child in list(brport_dir.iterdir()):
                    child.unlink()
                brport_dir.rmdir()
            st["master"] = None
            st["isolated"] = "0"
        else:
            br_dir = self.devices_root / "virtual" / "net" / bridge
            br_dir.mkdir(parents=True, exist_ok=True)
            master_link.symlink_to(br_dir)
            brport_dir.mkdir(exist_ok=True)
            (brport_dir / "isolated").write_text(f"{isolated}\n", encoding="utf-8")
            st["master"] = bridge
            st["isolated"] = isolated

    def create_bridge_sysfs(self, bridge: str = "br-stpusb-dir"):
        self.bridges.add(bridge)
        br_real = self.devices_root / "virtual" / "net" / bridge
        br_real.mkdir(parents=True, exist_ok=True)
        (br_real / "bridge").mkdir(exist_ok=True)
        link = self.net_sysfs / bridge
        if not link.exists() and not link.is_symlink():
            link.symlink_to(br_real)

    def remove_bridge_sysfs(self, bridge: str):
        self.bridges.discard(bridge)
        link = self.net_sysfs / bridge
        if link.exists() or link.is_symlink():
            link.unlink()

    def make_grant(self, **overrides) -> str:
        payload = {
            "session_id": "sess-001",
            "generation": 1,
            "host_id": "host-pilot-01",
            "device_serial": "MTKDEV001",
            "usb_devpath": "1-1.2",
            "egress": "direct",
            "iat": self.now,
            "exp": self.now + 900,
        }
        payload.update(overrides)
        return _sign_grant(self.priv_key, payload)

    def _fake_subprocess_run(self, argv, **kwargs):
        self.commands.append((list(argv), dict(kwargs)))
        if self.fail_hook is not None:
            hook_res = self.fail_hook(argv)
            if hook_res is not None:
                rc, out, err = hook_res
                return subprocess.CompletedProcess(argv, rc, out.encode(), err.encode())

        exe = Path(argv[0]).name
        if exe == "ip":
            return self._sim_ip(argv)
        if exe == "bridge":
            return self._sim_bridge(argv)
        if exe == "iptables":
            return self._sim_iptables(argv)
        if exe == "dnsmasq":
            return self._sim_dnsmasq(argv)
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def _sim_ip(self, argv):
        if argv[1:4] == ["link", "show", "dev"]:
            dev = argv[4]
            rc = 0 if (dev in self.bridges or dev in self.iface_state) else 1
            return subprocess.CompletedProcess(argv, rc, b"", b"")
        if argv[1:4] == ["link", "add", "name"] and argv[5:7] == ["type", "bridge"]:
            self.create_bridge_sysfs(argv[4])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[1:4] == ["link", "delete", "dev"] and argv[5:7] == ["type", "bridge"]:
            self.remove_bridge_sysfs(argv[4])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if argv[1:4] == ["link", "set", "dev"]:
            dev = argv[4]
            rest = argv[5:]
            if dev in self.iface_state:
                if rest == ["down"]:
                    self.iface_state[dev]["oper"] = "down"
                elif rest == ["up"]:
                    self.iface_state[dev]["oper"] = "up"
                elif len(rest) == 2 and rest[0] == "master":
                    if rest[1] not in self.bridges:
                        return subprocess.CompletedProcess(argv, 1, b"", b"bridge missing")
                    self._set_master(dev, rest[1], isolated="0")
                elif rest == ["nomaster"]:
                    self._set_master(dev, None)
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def _sim_bridge(self, argv):
        if argv[1:4] == ["link", "set", "dev"] and argv[5:7] == ["isolated", "on"]:
            dev = argv[4]
            if dev in self.iface_state and self.iface_state[dev]["master"] is not None:
                net_real: Path = self.iface_state[dev]["net_real"]
                (net_real / "brport" / "isolated").write_text("1\n", encoding="utf-8")
                self.iface_state[dev]["isolated"] = "1"
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"not a bridge port")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def _sim_iptables(self, argv):
        # iptables -w -t <table> <op> <chain> ...
        table = argv[3]
        op = argv[4]
        chain = argv[5]
        key = (table, chain)
        if op == "-L":
            rc = 0 if key in self.chains else 1
            return subprocess.CompletedProcess(argv, rc, b"", b"")
        if op == "-N":
            if key in self.chains:
                return subprocess.CompletedProcess(argv, 1, b"", b"Chain already exists")
            self.chains[key] = []
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if op == "-F":
            if key not in self.chains:
                return subprocess.CompletedProcess(argv, 1, b"", b"No chain")
            self.chains[key] = []
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if op == "-X":
            if key not in self.chains:
                return subprocess.CompletedProcess(argv, 1, b"", b"No chain")
            del self.chains[key]
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if op == "-A":
            self.chains.setdefault(key, []).append(argv[6:])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if op == "-I":
            # argv[6] is rulenum
            self.chains.setdefault(key, []).insert(0, argv[7:])
            return subprocess.CompletedProcess(argv, 0, b"", b"")
        if op == "-C":
            rc = 0 if argv[6:] in self.chains.get(key, []) else 1
            return subprocess.CompletedProcess(argv, rc, b"", b"")
        if op == "-D":
            rules = self.chains.get(key, [])
            if argv[6:] in rules:
                rules.remove(argv[6:])
                return subprocess.CompletedProcess(argv, 0, b"", b"")
            return subprocess.CompletedProcess(argv, 1, b"", b"No rule")
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    def _sim_dnsmasq(self, argv):
        pid_arg = next(a.split("=", 1)[1] for a in argv if a.startswith("--pid-file="))
        lease_arg = next(a.split("=", 1)[1] for a in argv if a.startswith("--dhcp-leasefile="))
        Path(pid_arg).write_text(f"{self.dnsmasq_pid}\n", encoding="utf-8")
        Path(lease_arg).write_text("", encoding="utf-8")
        pdir = self.proc_root / str(self.dnsmasq_pid)
        pdir.mkdir(exist_ok=True)
        (pdir / "cmdline").write_bytes(b"\x00".join(a.encode() for a in argv) + b"\x00")

        # 拦截 os.kill 模拟停掉假 dnsmasq 进程
        real_kill = os.kill

        def fake_kill(pid, sig):
            if pid == self.dnsmasq_pid:
                cmd_file = self.proc_root / str(pid) / "cmdline"
                if cmd_file.exists():
                    cmd_file.unlink()
                return
            return real_kill(pid, sig)

        self.monkeypatch.setattr(self.wrapper.os, "kill", fake_kill)
        return subprocess.CompletedProcess(argv, 0, b"", b"")


@pytest.fixture
def host(wrapper, monkeypatch, tmp_path):
    return _FakeNetHost(wrapper, monkeypatch, tmp_path)


# ── 纯 Python Ed25519 验签与 RFC 8032 交叉验证 ────────────────────────────────


def test_pure_python_ed25519_matches_rfc8032_and_cryptography(wrapper):
    # RFC 8032 §7.1 Test Vector 1 (empty message)
    pk1 = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    sig1 = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e06522490155"
        "5fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert wrapper.ed25519_verify(pk1, b"", sig1) is True
    assert wrapper.ed25519_verify(pk1, b"x", sig1) is False

    # 与 cryptography Ed25519 实签互验（PEM 与 raw base64url 公钥均支持）
    priv = Ed25519PrivateKey.generate()
    msg = b"stp-usbnet-grant-canonical-payload"
    sig = priv.sign(msg)
    pem_bytes = wrapper.parse_ed25519_pubkey(_pubkey_pem(priv))
    raw_bytes = wrapper.parse_ed25519_pubkey(_pubkey_raw_b64url(priv))
    assert pem_bytes == raw_bytes
    assert wrapper.ed25519_verify(pem_bytes, msg, sig) is True
    # 篡改 1 bit 必失败
    bad_sig = bytes([sig[0] ^ 0x01]) + sig[1:]
    assert wrapper.ed25519_verify(pem_bytes, msg, bad_sig) is False


# ── 反例 ①：未授权/禁用态/配置异常/非 root 全量 fail-closed (exit 2) ──────────


@pytest.mark.parametrize("argv", [
    ["usbnet-status"],
    ["usbnet-ensure-bridge", "--egress", "direct"],
    ["usbnet-attach", "--grant", "a.b", "--iface", "usb0", "--expect-devpath", "1-1.2"],
    ["usbnet-detach", "--session-id", "s1", "--generation", "1"],
    ["usbnet-revoke", "--session-id", "s1", "--generation", "1", "--serial", "MTKDEV001"],
    ["usbnet-teardown", "--egress", "direct"],
])
def test_anti_case_1_disabled_conf_fails_closed_for_all_subcommands(wrapper, host, argv, capsys):
    """反例 ①：usbnet_enabled=false 时全部六个子命令（含 usbnet-status）一律 exit 2 + STP_USBNET_DISABLED。"""
    host.write_conf(usbnet_enabled=False, control_plane_verify_pubkey="", allowed_usb_ports=[])
    rc = wrapper.main(argv)
    assert rc == 2
    err = capsys.readouterr().err
    assert wrapper.USBNET_MARKER_DISABLED in err
    assert host.commands == []
    assert not host.run_dir.exists()


def test_anti_case_1_missing_symlink_writable_oversize_and_nonroot_rejected(
    wrapper, host, tmp_path, capsys, monkeypatch,
):
    """反例 ①：配置缺失 / symlink / 权限过宽 / 父目录可写 / 超 64KiB / 非 root 一律 exit 2 + STP_AGENT_PRIV_ERROR。"""
    # 缺失
    host.conf_path.unlink()
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err

    # symlink
    real_conf = tmp_path / "other.conf"
    real_conf.write_text(json.dumps({"usbnet_enabled": True}), encoding="utf-8")
    host.conf_path.symlink_to(real_conf)
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err
    host.conf_path.unlink()

    # group/other writable
    host.write_conf()
    host.conf_path.chmod(0o664)
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err

    # 超 64 KiB
    host.conf_path.chmod(0o640)
    raw = host.conf_path.read_text(encoding="utf-8")
    host.conf_path.write_text(raw + " " * (wrapper.USBNET_MAX_CONF_BYTES + 1), encoding="utf-8")
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err

    # 父目录可写
    host.write_conf()
    host.etc_dir.chmod(0o777)
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err
    host.etc_dir.chmod(0o755)

    # 非 root
    monkeypatch.setattr(wrapper, "_require_root", lambda: wrapper._fail("must run as root"))
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err


def test_corrupted_or_symlink_ledger_fails_closed_even_for_status(wrapper, host, tmp_path, capsys):
    """D10.3：台账目录或文件损坏/symlink/权限异常一律 exit 2 fail-closed。"""
    host.run_dir.mkdir(mode=0o700)
    ledger = host.run_dir / wrapper.USBNET_LEDGER_FILENAME

    # 坏权限（0644 而非 0600）
    ledger.write_text("{}", encoding="utf-8")
    ledger.chmod(0o644)
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err

    # 损坏 JSON
    ledger.chmod(0o600)
    ledger.write_text("{corrupt", encoding="utf-8")
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err

    # 台账文件是 symlink
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    ledger.unlink()
    ledger.symlink_to(target)
    assert wrapper.main(["usbnet-status"]) == 2
    assert "STP_AGENT_PRIV_ERROR" in capsys.readouterr().err


# ── 反例 ②：目标白名单、sysfs 判据与 Ed25519 grant 校验 ──────────────────────


def test_anti_case_2_untrusted_port_driver_virtual_uplink_and_bad_grants_rejected(wrapper, host, capsys):
    """反例 ②：非白名单 USB 口 / 非法驱动 / virtual 网卡 / 默认路由上联网卡 / 非法或过期 grant 均被拒绝。"""
    host.create_bridge_sysfs("br-stpusb-dir")
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="MTKDEV001", driver="rndis_host")

    # 1. 非白名单 USB 口（即使 grant 签名自洽）
    host.add_usb_gadget(iface="usb9", devpath="2-9.9", serial="MTKDEV009", driver="rndis_host")
    grant_unlisted = host.make_grant(usb_devpath="2-9.9", device_serial="MTKDEV009")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_unlisted, "--iface", "usb9", "--expect-devpath", "2-9.9",
    ]) == 2
    assert "allowed_usb_ports" in capsys.readouterr().err

    # 2. 非 {rndis_host, cdc_ncm} 驱动（如 cdc_ether）
    host.add_usb_gadget(iface="usb1", devpath="1-1.3", serial="MTKDEV002", driver="cdc_ether")
    grant_bad_drv = host.make_grant(usb_devpath="1-1.3", device_serial="MTKDEV002")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_bad_drv, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 2
    assert "driver" in capsys.readouterr().err

    # 3. /sys/devices/virtual/ 下的虚拟网卡
    veth_real = host.devices_root / "virtual" / "net" / "veth0"
    veth_real.mkdir(parents=True)
    (host.net_sysfs / "veth0").symlink_to(veth_real)
    grant_ok = host.make_grant()
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_ok, "--iface", "veth0", "--expect-devpath", "1-1.2",
    ]) == 2
    assert "non-virtual" in capsys.readouterr().err

    # 4. 宿主默认路由上联网卡（eth0）与保留接口（lo / docker0 / br-stpusb-dir）
    for forbidden_iface in ("eth0", "lo", "docker0", "br-stpusb-dir"):
        assert wrapper.main([
            "usbnet-attach", "--grant", grant_ok, "--iface", forbidden_iface, "--expect-devpath", "1-1.2",
        ]) == 2
        capsys.readouterr()

    # 5. 非法签名（用另一私钥签发）
    evil_key = Ed25519PrivateKey.generate()
    evil_grant = _sign_grant(evil_key, {
        "session_id": "sess-evil",
        "generation": 1,
        "host_id": "host-pilot-01",
        "device_serial": "MTKDEV001",
        "usb_devpath": "1-1.2",
        "egress": "direct",
        "iat": host.now,
        "exp": host.now + 900,
    })
    assert wrapper.main([
        "usbnet-attach", "--grant", evil_grant, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 2
    assert "signature" in capsys.readouterr().err

    # 6. TTL 不在 300..1800s（过短 299s / 过长 1801s）、未来 iat (> now+30)、已过期、host_id 不符、egress 未授权
    bad_grants = [
        host.make_grant(iat=host.now, exp=host.now + 299),
        host.make_grant(iat=host.now, exp=host.now + 1801),
        host.make_grant(iat=host.now + 31, exp=host.now + 931),
        host.make_grant(iat=host.now - 900, exp=host.now),
        host.make_grant(host_id="other-host"),
        host.make_grant(egress="vpn"),
        host.make_grant(device_serial="WRONG_SERIAL"),
    ]
    for g in bad_grants:
        assert wrapper.main([
            "usbnet-attach", "--grant", g, "--iface", "usb0", "--expect-devpath", "1-1.2",
        ]) == 2
        capsys.readouterr()

    assert host.commands == []


# ── 反例 ③：代次比较域成对验收 + revoke 不删 active_bindings + 过期后安全摘桥 ──


def test_anti_case_3_serial_scoped_watermark_and_same_port_lower_gen_new_device(wrapper, host, capsys):
    """反例 ③：同设备旧授权被 STP_USBNET_GRANT_REVOKED 拒绝；同物理口换入低代次新设备合法生效；
    usbnet-revoke 不删 active_bindings；旧授权过期/撤销后 usbnet-detach 仍可安全摘除。"""
    host.create_bridge_sysfs("br-stpusb-dir")
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_A", ifindex=21, mac="02:aa:bb:cc:dd:01")

    # 1. DEV_A (gen=5) 签发两条未过期授权：先用 gen=5 成功挂桥，同时预签旧 gen=4（未过期）
    grant_a_old_gen4 = host.make_grant(session_id="sess-a4", generation=4, device_serial="DEV_A", usb_devpath="1-1.2")
    grant_a_gen5 = host.make_grant(session_id="sess-a5", generation=5, device_serial="DEV_A", usb_devpath="1-1.2")

    assert wrapper.main([
        "usbnet-attach", "--grant", grant_a_gen5, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    capsys.readouterr()
    assert host.iface_state["usb0"]["oper"] == "up"
    assert host.iface_state["usb0"]["master"] == "br-stpusb-dir"
    assert host.iface_state["usb0"]["isolated"] == "1"

    # 2. 对 (sess-a5, gen=5) 调用 usbnet-revoke：必须写入撤销且不得删除 active_bindings！
    assert wrapper.main([
        "usbnet-revoke", "--session-id", "sess-a5", "--generation", "5", "--serial", "DEV_A",
    ]) == 0
    capsys.readouterr()

    assert wrapper.main(["usbnet-status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert len(st["active_bindings"]) == 1
    assert st["active_bindings"][0]["session_id"] == "sess-a5"
    assert st["revocation_state"]["min_allowed_generation_by_serial"]["DEV_A"] == 5

    # 3. 即使 grant_a_gen5 尚未过期，但已被 revoke -> usbnet-attach 拒绝 (exit 3 + STP_USBNET_GRANT_REVOKED)
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_a_gen5, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 3
    assert wrapper.USBNET_MARKER_GRANT_REVOKED in capsys.readouterr().err

    # 4. 推进时间让 grant_a_gen5 彻底过期（+2000s）：usbnet-detach 不依赖 grant TTL，仍基于台账安全摘桥
    host.now += 2000
    assert wrapper.main([
        "usbnet-detach", "--session-id", "sess-a5", "--generation", "5",
        "--expect-iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "detached"
    assert host.iface_state["usb0"]["oper"] == "down"
    assert host.iface_state["usb0"]["master"] is None

    # 5. DEV_A 用未过期的低代次 grant (gen=4 < watermark 5) 尝试挂桥 -> STP_USBNET_GRANT_REVOKED
    grant_a_low_fresh = host.make_grant(session_id="sess-a4-fresh", generation=4, device_serial="DEV_A", usb_devpath="1-1.2")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_a_low_fresh, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 3
    assert wrapper.USBNET_MARKER_GRANT_REVOKED in capsys.readouterr().err
    # 同时确认最初的 grant_a_old_gen4 已过期被 exit 2 拒绝
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_a_old_gen4, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 2
    capsys.readouterr()

    # 6. 成对验证：同一物理 USB 口 1-1.2 换入新设备 DEV_B（自身代次 generation=1 < DEV_A 的 5），
    #    因水位仅按 device_serial 索引，DEV_B 合法 grant 必须可以正常挂桥！
    host.add_usb_gadget(
        iface="usb0", devpath="1-1.2", serial="DEV_B", ifindex=22,
        mac="02:bb:cc:dd:ee:02", driver="cdc_ncm",
    )
    grant_b_gen1 = host.make_grant(session_id="sess-b1", generation=1, device_serial="DEV_B", usb_devpath="1-1.2")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_b_gen1, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["result"] == "attached"
    assert res["device_serial"] == "DEV_B"
    assert res["generation"] == 1


# ── 反例 ④：detach 防接口名复用、防他桥误伤与 --expect-* 校验 ──────────────────


def test_anti_case_4_detach_never_touches_reused_iface_or_foreign_bridge(wrapper, host, capsys):
    """反例 ④：接口名 usb0 被新会话/新设备复用（ifindex/devpath/mac 不符）或挂在非受控他桥上时，
    usbnet-detach 绝不操作当前接口，仅清理旧台账并写撤销返回 already_gone_or_reused。"""
    host.create_bridge_sysfs("br-stpusb-dir")
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_OLD", ifindex=31, mac="02:00:00:00:00:31")
    grant_old = host.make_grant(session_id="sess-old", generation=2, device_serial="DEV_OLD", usb_devpath="1-1.2")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_old, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    capsys.readouterr()

    # --expect-iface / --expect-devpath 不符 -> exit 2 + STP_USBNET_BINDING_MISMATCH，不动接口
    host.commands.clear()
    assert wrapper.main([
        "usbnet-detach", "--session-id", "sess-old", "--generation", "2", "--expect-iface", "usb9",
    ]) == 2
    assert wrapper.USBNET_MARKER_BINDING_MISMATCH in capsys.readouterr().err
    assert host.commands == []

    assert wrapper.main([
        "usbnet-detach", "--session-id", "sess-old", "--generation", "2", "--expect-devpath", "1-1.3",
    ]) == 2
    assert wrapper.USBNET_MARKER_BINDING_MISMATCH in capsys.readouterr().err
    assert host.commands == []

    # 模拟原设备拔出、新设备插到另一口并复用接口名 usb0（新 ifindex=99、新 mac、已 UP 在网桥上）
    host.add_usb_gadget(
        iface="usb0", devpath="1-1.3", serial="DEV_NEW", ifindex=99,
        mac="02:00:00:00:00:99", master="br-stpusb-dir", isolated="1",
    )
    host.iface_state["usb0"]["oper"] = "up"
    host.commands.clear()

    # 旧会话 detach：绝不执行任何 ip 命令，仅清旧台账并返回 already_gone_or_reused
    assert wrapper.main([
        "usbnet-detach", "--session-id", "sess-old", "--generation", "2",
    ]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already_gone_or_reused"
    assert host.commands == []
    assert host.iface_state["usb0"]["oper"] == "up"
    assert host.iface_state["usb0"]["master"] == "br-stpusb-dir"

    # 再测挂在非本受控他桥（如 br-foreign）上：即使 ifindex/devpath/mac 全匹配也绝不触碰他桥成员
    host.add_usb_gadget(iface="usb1", devpath="1-1.4", serial="DEV_F", ifindex=41, mac="02:00:00:00:00:41")
    grant_f = host.make_grant(session_id="sess-f", generation=1, device_serial="DEV_F", usb_devpath="1-1.4")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_f, "--iface", "usb1", "--expect-devpath", "1-1.4",
    ]) == 0
    capsys.readouterr()
    host._set_master("usb1", "br-foreign", isolated="0")
    host.commands.clear()

    assert wrapper.main(["usbnet-detach", "--session-id", "sess-f", "--generation", "1"]) == 0
    out_f = json.loads(capsys.readouterr().out)
    assert out_f["result"] == "already_gone_or_reused"
    assert host.commands == []
    assert host.iface_state["usb1"]["master"] == "br-foreign"


# ── 反例 ⑤：teardown 屏障/零残留、attaching 收敛/满额重入、隔离前置不转发 ───


def test_anti_case_5_teardown_barrier_preissued_unattached_grant_and_post_cooldown_recovery(
    wrapper, host, capsys,
):
    """反例 ⑤a：teardown 活跃绑定默认拒绝；空绑定与 --force 均先持久化 min_allowed_iat_by_egress，
    阻断 teardown 前已签发但尚未挂桥的旧 grant；冷却期满后按真实 iat > barrier 新签可正常挂桥。"""
    assert wrapper.main(["usbnet-ensure-bridge", "--egress", "direct"]) == 0
    capsys.readouterr()

    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_1", ifindex=51, mac="02:10:00:00:00:51")
    host.add_usb_gadget(iface="usb1", devpath="1-1.3", serial="DEV_2", ifindex=52, mac="02:10:00:00:00:52")

    # 签发两条 grant：DEV_1 挂桥，DEV_2「已签发、尚未挂桥」
    grant_attached = host.make_grant(session_id="s-att", generation=1, device_serial="DEV_1", usb_devpath="1-1.2")
    grant_preissued_unattached = host.make_grant(
        session_id="s-unatt", generation=1, device_serial="DEV_2", usb_devpath="1-1.3",
    )
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_attached, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    capsys.readouterr()

    # 有活跃绑定且未传 --force -> exit 3 + STP_USBNET_ACTIVE_BINDINGS
    assert wrapper.main(["usbnet-teardown", "--egress", "direct"]) == 3
    assert wrapper.USBNET_MARKER_ACTIVE_BINDINGS in capsys.readouterr().err
    assert "br-stpusb-dir" in host.bridges

    # 带 --force 关闭 -> 零残留成功并推进 min_allowed_iat_by_egress = now + 30
    teardown_now = host.now
    assert wrapper.main(["usbnet-teardown", "--egress", "direct", "--force"]) == 0
    td_out = json.loads(capsys.readouterr().out)
    assert td_out["result"] == "teardown_ok"
    expected_barrier = teardown_now + 30
    assert td_out["min_allowed_iat"] == expected_barrier
    assert "br-stpusb-dir" not in host.bridges
    assert ("filter", "STP_USBNET_IN_DIRECT") not in host.chains
    assert ("filter", "STP_USBNET_FWD_DIRECT") not in host.chains
    assert ("nat", "STP_USBNET_NAT_DIRECT") not in host.chains

    # usbnet-status 暴露 revocation_state 摘要（min_allowed_iat_by_egress + min_allowed_generation_by_serial）
    assert wrapper.main(["usbnet-status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["revocation_state"]["min_allowed_iat_by_egress"]["direct"] == expected_barrier
    assert st["revocation_state"]["min_allowed_generation_by_serial"]["DEV_1"] == 1
    assert st["active_bindings"] == []
    assert st["dnsmasq"]["direct"]["running"] is False

    # 重建网桥，验证：
    assert wrapper.main(["usbnet-ensure-bridge", "--egress", "direct"]) == 0
    capsys.readouterr()

    # (a) teardown 前已签发但尚未挂桥的 grant_preissued_unattached 即使从未挂桥且未过期 -> STP_USBNET_GRANT_REVOKED
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_preissued_unattached, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 3
    assert wrapper.USBNET_MARKER_GRANT_REVOKED in capsys.readouterr().err

    # (b) 冷却期满后（now = expected_barrier + 1），按真实 iat > barrier 新签授权可以正常挂桥
    host.now = expected_barrier + 1
    grant_post_cooldown = host.make_grant(
        session_id="s-new", generation=2, device_serial="DEV_2", usb_devpath="1-1.3",
    )
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_post_cooldown, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "attached"

    # (c) 同一时刻故意用 teardown 前旧 iat 的 grant 仍然被 STP_USBNET_GRANT_REVOKED 拒绝
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_preissued_unattached, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 3
    assert wrapper.USBNET_MARKER_GRANT_REVOKED in capsys.readouterr().err

    # (d) 空绑定 teardown 也必须无条件推进 min_allowed_iat_by_egress
    assert wrapper.main(["usbnet-detach", "--session-id", "s-new", "--generation", "2"]) == 0
    capsys.readouterr()
    host.now += 100
    empty_td_now = host.now
    assert wrapper.main(["usbnet-teardown", "--egress", "direct"]) == 0
    out_empty = json.loads(capsys.readouterr().out)
    assert out_empty["min_allowed_iat"] == empty_td_now + 30


def test_anti_case_5_teardown_incomplete_keeps_revocation_barrier(wrapper, host, capsys):
    """反例 ⑤b：teardown 清理步骤失败或残留时返回 exit 3 + STP_USBNET_TEARDOWN_INCOMPLETE，
    且已落盘的 min_allowed_iat_by_egress 撤销屏障必须保留。"""
    assert wrapper.main(["usbnet-ensure-bridge", "--egress", "direct"]) == 0
    capsys.readouterr()

    # 注入删桥失败
    host.fail_hook = lambda argv: (1, "", "busy") if argv[1:4] == ["link", "delete", "dev"] else None
    t0 = host.now
    assert wrapper.main(["usbnet-teardown", "--egress", "direct", "--force"]) == 3
    assert wrapper.USBNET_MARKER_TEARDOWN_INCOMPLETE in capsys.readouterr().err

    # 撤销屏障已落盘并由 usbnet-status 暴露
    host.fail_hook = None
    assert wrapper.main(["usbnet-status"]) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["revocation_state"]["min_allowed_iat_by_egress"]["direct"] == t0 + 30

    # 幂等重跑 teardown --force 收敛成功
    assert wrapper.main(["usbnet-teardown", "--egress", "direct", "--force"]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "teardown_ok"


def test_anti_case_5_attaching_unbridged_detach_and_full_capacity_reentry(wrapper, host, capsys):
    """反例 ⑤c：预写 attaching 后挂桥前中断由 usbnet-detach 置 down 并返回 detached_unbridged；
    满额（active_bindings == usbnet_max_devices）时同一 attaching 会话重入 usbnet-attach 不自阻塞；
    核心身份冲突时 exit 2 拒绝。"""
    host.write_conf(usbnet_max_devices=1)
    host.create_bridge_sysfs("br-stpusb-dir")
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_CAP", ifindex=61, mac="02:20:00:00:00:61")
    host.iface_state["usb0"]["oper"] = "up"

    # 构造「预写 attaching 后、挂桥前中断」的台账态（容量已达 1/1 满额）
    host.run_dir.mkdir(mode=0o700, exist_ok=True)
    ledger_path = host.run_dir / wrapper.USBNET_LEDGER_FILENAME
    ledger_doc = {
        "schema": 1,
        "active_bindings": {
            "sess-cap#1": {
                "session_id": "sess-cap",
                "generation": 1,
                "state": "attaching",
                "egress": "direct",
                "device_serial": "DEV_CAP",
                "iface": "usb0",
                "ifindex": 61,
                "usb_devpath": "1-1.2",
                "mac": "02:20:00:00:00:61",
                "attached_at": host.now,
            },
        },
        "revocation_state": {
            "revoked_sessions": [],
            "min_allowed_generation_by_serial": {},
            "min_allowed_iat_by_egress": {},
        },
    }
    ledger_path.write_text(json.dumps(ledger_doc), encoding="utf-8")
    ledger_path.chmod(0o600)

    # 1. 满额时同一 attaching 会话重入 usbnet-attach：不被自身占位阻塞，成功走完隔离前置并收敛为 attached
    grant_same = host.make_grant(session_id="sess-cap", generation=1, device_serial="DEV_CAP", usb_devpath="1-1.2")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_same, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    assert json.loads(capsys.readouterr().out)["result"] == "attached"

    # 2. 核心身份冲突（同 key 但 device_serial 不同）-> exit 2 fail-closed
    host.add_usb_gadget(iface="usb1", devpath="1-1.3", serial="DEV_OTHER", ifindex=62, mac="02:20:00:00:00:62")
    grant_conflict = host.make_grant(session_id="sess-cap", generation=1, device_serial="DEV_OTHER", usb_devpath="1-1.3")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_conflict, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 2
    assert "conflict" in capsys.readouterr().err

    # 3. 另一会话在满额时被拒 (exit 2)
    grant_other = host.make_grant(session_id="sess-other", generation=1, device_serial="DEV_OTHER", usb_devpath="1-1.3")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_other, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 2
    assert "usbnet_max_devices" in capsys.readouterr().err

    # 4. 恢复为未入桥 attaching 状态，调用 usbnet-detach -> 仅执行 down（不执行 nomaster），返回 detached_unbridged
    host._set_master("usb0", None)
    host.iface_state["usb0"]["oper"] = "up"
    ledger_path.write_text(json.dumps(ledger_doc), encoding="utf-8")
    ledger_path.chmod(0o600)
    host.commands.clear()

    assert wrapper.main(["usbnet-detach", "--session-id", "sess-cap", "--generation", "1"]) == 0
    det_out = json.loads(capsys.readouterr().out)
    assert det_out["result"] == "detached_unbridged"
    assert host.iface_state["usb0"]["oper"] == "down"
    ip_cmds = [argv[1:] for argv, _ in host.commands if Path(argv[0]).name == "ip"]
    assert ip_cmds == [["link", "set", "dev", "usb0", "down"]]


def test_anti_case_5_isolation_before_up_and_incomplete_rollback_stays_down(wrapper, host, capsys):
    """反例 ⑤d：严格按 down → master → isolated on → 回读 brport/isolated == '1' → up 执行；
    隔离失败且 nomaster 回滚失败时绝不执行 up、端口保持 DOWN 且保留 attaching 条目供收敛。"""
    host.create_bridge_sysfs("br-stpusb-dir")
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_ISO", ifindex=71, mac="02:30:00:00:00:71")
    grant = host.make_grant(session_id="sess-iso", generation=1, device_serial="DEV_ISO", usb_devpath="1-1.2")

    # 1. 正常挂桥：严格验证命令顺序 down -> master -> isolated on -> up
    host.commands.clear()
    assert wrapper.main([
        "usbnet-attach", "--grant", grant, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 0
    capsys.readouterr()
    seq = [(Path(argv[0]).name, argv[1:]) for argv, _ in host.commands]
    assert seq == [
        ("ip", ["link", "set", "dev", "usb0", "down"]),
        ("ip", ["link", "set", "dev", "usb0", "master", "br-stpusb-dir"]),
        ("bridge", ["link", "set", "dev", "usb0", "isolated", "on"]),
        ("ip", ["link", "set", "dev", "usb0", "up"]),
    ]

    # 2. 隔离命令成功但内核 brport/isolated 回读仍为 "0"，且回滚 nomaster 失败 ->
    #    绝不执行 up、先强制 down、保留 attaching 条目、返回 exit 3 + STP_USBNET_ATTACH_ROLLBACK
    host.add_usb_gadget(iface="usb1", devpath="1-1.3", serial="DEV_FAIL", ifindex=72, mac="02:30:00:00:00:72")
    grant_fail = host.make_grant(session_id="sess-fail", generation=1, device_serial="DEV_FAIL", usb_devpath="1-1.3")

    def hook_isolated_stuck_and_nomaster_fail(argv):
        exe = Path(argv[0]).name
        if exe == "bridge" and argv[5:7] == ["isolated", "on"]:
            # 模拟命令返回 0 但 brport/isolated 仍为 "0"
            return (0, "", "")
        if exe == "ip" and argv[5:] == ["nomaster"]:
            return (1, "", "EBUSY")
        return None

    host.fail_hook = hook_isolated_stuck_and_nomaster_fail
    host.commands.clear()
    assert wrapper.main([
        "usbnet-attach", "--grant", grant_fail, "--iface", "usb1", "--expect-devpath", "1-1.3",
    ]) == 3
    err = capsys.readouterr().err
    assert wrapper.USBNET_MARKER_ATTACH_ROLLBACK in err
    assert "rollback=incomplete_port_down" in err

    # 绝不能出现 ip link set dev usb1 up，且端口保持 DOWN
    assert not any(argv[1:] == ["link", "set", "dev", "usb1", "up"] for argv, _ in host.commands)
    assert host.iface_state["usb1"]["oper"] == "down"

    # attaching 条目必须保留在台账中以便后续 usbnet-detach 或重入收敛
    host.fail_hook = None
    assert wrapper.main(["usbnet-status"]) == 0
    st = json.loads(capsys.readouterr().out)
    fail_entries = [b for b in st["active_bindings"] if b["session_id"] == "sess-fail"]
    assert len(fail_entries) == 1
    assert fail_entries[0]["state"] == "attaching"


# ── 反例 ⑥：三层默认拒绝隔离规则、dnsmasq 边界、子进程收口与 Ansible 契约 ────


def test_anti_case_6_three_layer_firewall_dnsmasq_and_clean_subprocess_env(wrapper, host, capsys, monkeypatch):
    """反例 ⑥：INPUT/FORWARD/DOCKER-USER/NAT 三层默认拒绝规则、dnsmasq --port=0 --bind-interfaces、
    固定绝对路径/清空环境/<=30s 超时/不经 shell。"""
    monkeypatch.setenv("LEAKED_SECRET", "top-secret")
    assert wrapper.main(["usbnet-ensure-bridge", "--egress", "direct"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["bridge"] == "br-stpusb-dir"
    assert out["gateway_ip"] == "10.200.0.1"
    assert host.ip_forward.read_text(encoding="utf-8").strip() == "1"

    # 所有外部命令必须满足：绝对路径、清空环境、<= 30s 超时、不经 shell
    for argv, kwargs in host.commands:
        assert argv[0].startswith("/")
        assert kwargs["env"] == wrapper.USBNET_CMD_ENV
        assert "LEAKED_SECRET" not in kwargs["env"]
        assert kwargs["timeout"] <= 30
        assert kwargs.get("shell") is False

    # ① INPUT 专属链：放行 DHCP + ICMP echo-request，末尾默认 DROP（阻断 SSH/HTTP/PG/Redis 等本机端口）
    in_rules = host.chains[("filter", "STP_USBNET_IN_DIRECT")]
    assert ["-i", "br-stpusb-dir", "-j", "STP_USBNET_IN_DIRECT"] in host.chains[("filter", "INPUT")]
    assert ["-p", "udp", "--sport", "68", "--dport", "67", "-j", "ACCEPT"] in in_rules
    assert ["-p", "icmp", "--icmp-type", "echo-request", "-j", "ACCEPT"] in in_rules
    assert in_rules[-1] == ["-j", "DROP"]
    assert not any("--dport" in r and "22" in r for r in in_rules)
    assert not any("--dport" in r and "53" in r for r in in_rules)  # local_dns_forward=False

    # ② FORWARD & DOCKER-USER 专属链：先白名单上游 DNS 53，再阻断全部 RFC1918/link-local/CGNAT/docker0/同桥互访
    fwd_rules = host.chains[("filter", "STP_USBNET_FWD_DIRECT")]
    assert ["-i", "br-stpusb-dir", "-j", "STP_USBNET_FWD_DIRECT"] in host.chains[("filter", "FORWARD")]
    assert ["-i", "br-stpusb-dir", "-j", "STP_USBNET_FWD_DIRECT"] in host.chains[("filter", "DOCKER-USER")]
    assert ["-i", "br-stpusb-dir", "-o", "br-stpusb-dir", "-j", "DROP"] in fwd_rules
    assert ["-i", "br-stpusb-dir", "-d", "223.5.5.5/32", "-p", "udp", "--dport", "53", "-j", "ACCEPT"] in fwd_rules
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16"):
        assert ["-i", "br-stpusb-dir", "-d", cidr, "-j", "DROP"] in fwd_rules
    assert ["-i", "br-stpusb-dir", "-o", "docker0", "-j", "DROP"] in fwd_rules
    assert fwd_rules[-1] == ["-j", "DROP"]

    # ③ NAT 专属链
    nat_rules = host.chains[("nat", "STP_USBNET_NAT_DIRECT")]
    assert ["-s", "10.200.0.0/22", "!", "-o", "br-stpusb-dir", "-j", "MASQUERADE"] in nat_rules

    # ④ dnsmasq 强制 --port=0 --bind-interfaces --interface=br-stpusb-dir，PID/leases 落在 /run/stp-agent-priv/
    dm_calls = [argv for argv, _ in host.commands if Path(argv[0]).name == "dnsmasq"]
    assert len(dm_calls) == 1
    dm_argv = dm_calls[0]
    assert "--port=0" in dm_argv
    assert "--bind-interfaces" in dm_argv
    assert "--interface=br-stpusb-dir" in dm_argv
    assert f"--pid-file={host.run_dir}/dnsmasq-direct.pid" in dm_argv
    assert f"--dhcp-leasefile={host.run_dir}/dnsmasq-direct.leases" in dm_argv


def test_detach_not_in_ledger_is_idempotent_and_records_revocation(wrapper, host, capsys):
    """D10.5：未命中台账时补记 revoked_sessions 并返回 exit 0，不触碰任何网卡。"""
    host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_X", ifindex=81, mac="02:40:00:00:00:81")
    host.commands.clear()
    assert wrapper.main(["usbnet-detach", "--session-id", "sess-unknown", "--generation", "3"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["result"] == "already_gone_or_reused"
    assert host.commands == []

    # 随后同一 (sess-unknown, 3) 的有效 grant 被撤销屏障拒绝
    host.create_bridge_sysfs("br-stpusb-dir")
    grant = host.make_grant(session_id="sess-unknown", generation=3, device_serial="DEV_X", usb_devpath="1-1.2")
    assert wrapper.main([
        "usbnet-attach", "--grant", grant, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 3
    assert wrapper.USBNET_MARKER_GRANT_REVOKED in capsys.readouterr().err


def test_attach_complete_rollback_and_brport_symlink_and_ifindex_change(wrapper, host, tmp_path, capsys):
    """D10.4：隔离失败且 nomaster 成功时删除 attaching 并返回 rollback=complete；
    brport/isolated 为 symlink 或 UP 前 ifindex 变化均拒绝置 UP。"""
    host.create_bridge_sysfs("br-stpusb-dir")
    net_real = host.add_usb_gadget(iface="usb0", devpath="1-1.2", serial="DEV_RB", ifindex=82, mac="02:41:00:00:00:82")
    grant = host.make_grant(session_id="sess-rb", generation=1, device_serial="DEV_RB", usb_devpath="1-1.2")

    # 1. bridge isolated on 后 brport/isolated 被替换为 symlink -> O_NOFOLLOW 拒绝，执行 down + nomaster，删除 attaching
    evil_iso = tmp_path / "evil_isolated"
    evil_iso.write_text("1\n", encoding="utf-8")

    def hook_symlink_iso(argv):
        if Path(argv[0]).name == "bridge" and argv[5:7] == ["isolated", "on"]:
            iso_file = net_real / "brport" / "isolated"
            if iso_file.exists() or iso_file.is_symlink():
                iso_file.unlink()
            iso_file.symlink_to(evil_iso)
            return (0, "", "")
        return None

    host.fail_hook = hook_symlink_iso
    host.commands.clear()
    assert wrapper.main([
        "usbnet-attach", "--grant", grant, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 3
    err = capsys.readouterr().err
    assert wrapper.USBNET_MARKER_ATTACH_ROLLBACK in err
    assert "rollback=complete" in err
    assert not any(argv[1:] == ["link", "set", "dev", "usb0", "up"] for argv, _ in host.commands)

    # attaching 已被清理
    host.fail_hook = None
    assert wrapper.main(["usbnet-status"]) == 0
    assert json.loads(capsys.readouterr().out)["active_bindings"] == []

    # 2. UP 前 ifindex 变化（模拟设备中途重建）-> 拒绝置 UP 并完整回滚
    def hook_ifindex_mutated(argv):
        if Path(argv[0]).name == "bridge" and argv[5:7] == ["isolated", "on"]:
            (net_real / "brport" / "isolated").write_text("1\n", encoding="utf-8")
            (net_real / "ifindex").write_text("999\n", encoding="utf-8")
            return (0, "", "")
        return None

    host.fail_hook = hook_ifindex_mutated
    host.commands.clear()
    assert wrapper.main([
        "usbnet-attach", "--grant", grant, "--iface", "usb0", "--expect-devpath", "1-1.2",
    ]) == 3
    err2 = capsys.readouterr().err
    assert wrapper.USBNET_MARKER_ATTACH_ROLLBACK in err2
    assert not any(argv[1:] == ["link", "set", "dev", "usb0", "up"] for argv, _ in host.commands)


def test_revoked_sessions_bounded_gc_policy(wrapper):
    """D10.3：仅当 gen < min_gen[serial] 或 now - revoked_at > 3600 才裁剪 revoked_sessions。"""
    now = 1_800_005_000
    ledger = wrapper._empty_usbnet_ledger()
    ledger["revocation_state"]["min_allowed_generation_by_serial"] = {"DEV_A": 5}
    ledger["revocation_state"]["revoked_sessions"] = [
        {"session_id": "keep-fresh", "generation": 5, "device_serial": "DEV_A", "revoked_at": now - 3600},
        {"session_id": "drop-below-gen", "generation": 4, "device_serial": "DEV_A", "revoked_at": now - 10},
        {"session_id": "drop-expired", "generation": 6, "device_serial": "DEV_A", "revoked_at": now - 3601},
        {"session_id": "keep-other-serial", "generation": 1, "device_serial": "DEV_B", "revoked_at": now - 100},
    ]
    wrapper._gc_revoked_sessions(ledger, now)
    kept_ids = [item["session_id"] for item in ledger["revocation_state"]["revoked_sessions"]]
    assert kept_ids == ["keep-fresh", "keep-other-serial"]


def test_ansible_update_playbook_deploys_default_disabled_usbnet_conf_without_sudoers(wrapper):
    """D10.2/D10.6/D10.7：update_agent.yml 下发默认禁用 /etc/stp-usbnet.conf（root:root 非 g/o 可写）
    与 NetworkManager 防接管配置，且绝不新增或修改 sudoers 规则。"""
    text = UPDATE_PLAYBOOK.read_text(encoding="utf-8")
    plays = yaml.safe_load(text)
    tasks = {t["name"]: t for play in plays for t in play.get("tasks", [])}

    conf_task = tasks["Deploy root-owned /etc/stp-usbnet.conf template — default disabled (#4212)"]
    copy_spec = conf_task["ansible.builtin.copy"]
    assert copy_spec["dest"] == wrapper.USBNET_CONF_PATH
    assert (copy_spec["owner"], copy_spec["group"]) == ("root", "root")
    assert int(copy_spec["mode"], 8) & 0o022 == 0
    assert "agent_usbnet_enabled | default(false)" in copy_spec["content"]
    assert "agent_configure_usbnet | default(false)" in str(conf_task["when"])

    nm_task = tasks[
        "Prevent NetworkManager from auto-managing STP USB-net bridges and RNDIS/CDC-NCM ifaces (#4212)"
    ]
    nm_copy = nm_task["ansible.builtin.copy"]
    assert nm_copy["dest"] == "/etc/NetworkManager/conf.d/99-stp-usbnet-unmanaged.conf"
    assert "interface-name:br-stpusb-*" in nm_copy["content"]
    assert "driver:rndis_host" in nm_copy["content"]
    assert "driver:cdc_ncm" in nm_copy["content"]

    # D10.7：update_agent.yml 全部 task 中不得新增或修改 sudoers 规则
    assert not any("sudoers" in json.dumps(task, ensure_ascii=False).lower() for task in tasks.values())
