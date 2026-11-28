#!/usr/bin/python3
# -*- coding: utf-8 -*-
"""stp-agent-priv —— Agent 主机提权边界 wrapper（#1250 / ADR-0037）。

背景：install_agent.sh 此前给 Agent 用户落无参数限制的免密
``rsync/cp/chmod/chown/ln`` —— 拿到 Agent 用户执行能力即可对任意 root
文件提权写。本脚本是唯一被 NOPASSWD sudoers 授权的入口（root:root、位于
``/usr/local/sbin``，不在 Agent 可写的安装目录内），把热更新与安装链所需的
root 操作收敛为**固定子命令 + 路径/属主/内容校验**：

    selftest         探活与自检（wrapper **自洽**：属主/权限 + parser↔契约表一致）
    capabilities     打印支持的子命令（每行一个）——交付链按集合比对，见 #2319
    bootstrap        写 /etc/stp-agent-priv.conf 与 sudoers（仅 root 安装期）
    apply-code       把 Agent 暂存代码树同步进 $INSTALL_DIR/agent/
    install-schema   安装 Pipeline schema（校验 JSON 与调用者属主）
    write-version    写 agent/VERSION（校验短 SHA）
    write-digest     写 agent/ARTIFACT_DIGEST（ADR-0040，校验 sha256:<hex>）
    sync-env         改 .env：AGENT_SECRET 或受控 overrides（保持原哨兵输出）
    deps-marker      写依赖刷新标记
    fix-ownership    安装目录属主回收（symlink 安全：chown -h）
    restart          重启 Agent systemd 服务
    ensure-udev-rule 写固定 udev 规则（MTK ttyACM → 0660+dialout；调用者非组成员
                    才 0666）并 reload（#2133/D5；#2284；#2353）
    usb-authorized   切换 MTK 设备 sysfs authorized（port 正则 + vendor 校验）
    read-kernel-log  只读搬运内核日志（journalctl -k，固定形态 + 两个整数参数，
                    #2957：给 Agent 一条可读内核 USB 日志的最小提权面）
    usb-power        按 serial 查 root 属主槽位映射，固定 argv 调 uhubctl 切电
                    （#3892 / ADR-0063 D2 / ADR-0037 D8：调用方不能指定 Hub 或端口）
    xhci-rebind      无目标参数枚举 /sys/bus/pci/drivers/xhci_hcd 下已绑定 BDF 并执行
                    unbind→等待→bind 与重绑校验（#2972 / ADR-0037 D9）
    usbnet-*         USB 反向供网受限通道（ensure-bridge / attach / detach / revoke /
                    teardown / status；#4212 / ADR-0037 D10 / ADR-0066 D6）

不变量：
- 所有写路径固定或经前缀校验，永不接受任意目标路径；
- 目录逐级以 O_NOFOLLOW 打开，写入与属主变更不重新解析可变父路径；
- apply-code 使用 ``--safe-links``，且 rsync 以配置中的非 root Agent 身份运行；
- 运行需要 root（sudo）；``bootstrap`` 校验用户名/服务名的字符集后写
  sudoers，避免注入。

Python 3.6+（主机系统 python3，不使用第三方依赖）。exit code：0 成功、
1 selftest 失败、2 拒绝（STP_AGENT_PRIV_ERROR / STP_USBNET_DISABLED）、
3 read-kernel-log 读取失败（journalctl 非零退出 / 超时 / 输出截断；stderr 带
``STP_READ_KERNEL_LOG_*`` 标记）、usb-power 执行失败（uhubctl 非零退出 / 超时；
stderr 带 ``STP_USB_POWER_*`` 标记）、xhci-rebind 执行失败（无有效 BDF / 写入失败 /
部分成功 / 超时 / probe 失败；stderr 带 ``STP_XHCI_REBIND_*`` 标记）或 usbnet-*
运行态失败（撤销命中 / 挂桥回滚 / 活跃绑定阻断 / 关闭不完整；stderr 带
``STP_USBNET_*`` 标记）。
"""

import argparse
import base64
from contextlib import contextmanager, redirect_stderr
import errno
import fcntl
import grp
import hashlib
import io
import ipaddress
import json
import os
import pwd
import re
import select
import shutil
import signal
import stat
import subprocess
import sys
import time
import uuid

WRAPPER_PATH = "/usr/local/sbin/stp-agent-priv"
DEFAULT_CONF_PATH = "/etc/stp-agent-priv.conf"
SUDOERS_DIR = "/etc/sudoers.d"
RSYNC_BIN = "/usr/bin/rsync"
SYSTEMCTL_BIN = "/usr/bin/systemctl"
UDEVADM_BIN = "/usr/bin/udevadm"
MAX_SCHEMA_BYTES = 2 * 1024 * 1024
MAX_ENV_PAYLOAD_BYTES = 64 * 1024

# ── read-kernel-log 窄面（#2957 / ADR-0037 D7）──────────────────────────────
# 只读子命令：Agent 以 android 运行，内核日志在机队上恒不可读
# （dmesg_restrict=1、/dev/kmsg EPERM、非特权 journalctl 输出与「内核干净」同形），
# 导致 #2900 的两条内核证据类告警恒绿。本子命令把读权限收敛为
# **固定 argv + 两个整数参数**，不开放任何 journalctl 选项面（--file/-D/-M/-u/
# --grep 等一律不可达），也不动 sudo 组权限（A 方案）。
JOURNALCTL_BIN = "/usr/bin/journalctl"
#: stdout 上限：超过即视为**截断样本**（非零退出 + stderr 标记），调用方不得当完整结果用。
#: 依据：#2957 实测一台主机 `journalctl -k --boot` 有 373,600 行（远超 8 MiB），
#: 截断后计数偏小却与「干净」不可区分——比读不到更危险。
MAX_KERNEL_LOG_BYTES = 8 * 1024 * 1024
KERNEL_LOG_TIMEOUT_SECONDS = 30
MAX_KERNEL_LOG_LINES = 5000
#: 子进程环境：清空后只留这两项（不设 SYSTEMD_PAGER；--no-pager 双保险）。
KERNEL_LOG_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}

# ── usb-power 窄面（#3892 / ADR-0063 D1–D2 / ADR-0037 D8）─────────────────────
# sudoers 不约束 wrapper 参数（D6），所以切电目标**只能**来自 root 属主的槽位映射：
# 调用方给 serial，wrapper 查表得到 (hub_location, port)；Agent 用户无法断开
# 映射之外的任何 Hub 口（例如正在跑测试的邻机）。
UHUBCTL_BIN = "/usr/sbin/uhubctl"
USB_POWER_SLOTS_PATH = "/etc/stp-agent/usb-power-slots.json"
#: 映射文件与 uhubctl 本体必须属于该 uid（测试里 monkeypatch 为当前用户）。
USB_POWER_TRUSTED_UID = 0
MAX_USB_POWER_SLOTS_BYTES = 64 * 1024
USB_POWER_TIMEOUT_SECONDS = 60
USB_POWER_MAX_PORT = 16
USB_POWER_MAX_DELAY = 30
USB_POWER_ACTIONS = ("on", "off", "cycle")
USB_POWER_ENV = {"LC_ALL": "C", "PATH": "/usr/bin:/bin"}
_USB_POWER_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")

# ── xhci-rebind 窄面（#2972 / ADR-0037 D9）────────────────────────────────────
# 无目标参数子命令：只枚举固定驱动目录 /sys/bus/pci/drivers/xhci_hcd 下已绑定的
# PCI BDF 符号链接（realpath 须落于 /sys/devices/ 下且末段等于该 BDF），仅以
# O_WRONLY | O_NOFOLLOW 向驱动目录下的普通属性文件 unbind/bind 写入 BDF 字符串。
XHCI_SYSFS_BASE = "/sys/bus/pci/drivers/xhci_hcd"
XHCI_REBIND_DELAY_SECONDS = 2.0
XHCI_REBIND_TIMEOUT_SECONDS = 30.0
_PCI_BDF_RE = re.compile(r"^[0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7]\Z")
XHCI_MARKER_NO_PCI_IDS = "STP_XHCI_REBIND_NO_PCI_IDS"
XHCI_MARKER_WRITE_FAILED = "STP_XHCI_REBIND_WRITE_FAILED"
XHCI_MARKER_PARTIAL = "STP_XHCI_REBIND_PARTIAL"
XHCI_MARKER_TIMEOUT = "STP_XHCI_REBIND_TIMEOUT"
XHCI_MARKER_PROBE_FAILED = "STP_XHCI_REBIND_PROBE_FAILED"

# ── usbnet-* 窄面（#4212 / ADR-0037 D10 / ADR-0066 D6）───────────────────────
# USB 反向供网受限通道：
# - 授权锚点固定在 root:root 非 g/o 可写的 /etc/stp-usbnet.conf（默认 usbnet_enabled=false）；
# - 状态与撤销台账固定在 /run/stp-agent-priv/usbnet-bindings.json（0700 目录 / 0600 文件 /
#   flock 排他锁 / 同目录原子写），单调代次水位仅按 device_serial 索引（不按 usb_devpath）；
# - 控制面签发的短期 usbnet_grant 经纯 Python RFC 8032 Ed25519 验签（零三方依赖）；
# - 外部命令固定绝对路径、固定 argv、清空环境、<= 30s 超时、不经 shell。
USBNET_CONF_PATH = "/etc/stp-usbnet.conf"
USBNET_RUN_DIR = "/run/stp-agent-priv"
USBNET_LEDGER_FILENAME = "usbnet-bindings.json"
USBNET_LOCK_FILENAME = "usbnet-bindings.lock"
USBNET_TRUSTED_UID = 0
USBNET_TRUSTED_GID = 0
USBNET_MAX_CONF_BYTES = 64 * 1024
USBNET_MAX_LEDGER_BYTES = 2 * 1024 * 1024
USBNET_MIN_MAX_DEVICES = 1
USBNET_MAX_MAX_DEVICES = 32
USBNET_DEFAULT_MAX_DEVICES = 8
USBNET_GRANT_MIN_TTL_SECONDS = 300
USBNET_GRANT_MAX_TTL_SECONDS = 1800
USBNET_GRANT_FUTURE_SKEW_SECONDS = 30
USBNET_REVOKED_GC_AGE_SECONDS = 3600
USBNET_TEARDOWN_COOLDOWN_SECONDS = 30
USBNET_CMD_TIMEOUT_SECONDS = 30
USBNET_CMD_ENV = {"LC_ALL": "C", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"}

IP_BIN = "/usr/sbin/ip"
BRIDGE_BIN = "/usr/sbin/bridge"
IPTABLES_BIN = "/usr/sbin/iptables"
DNSMASQ_BIN = "/usr/sbin/dnsmasq"
NMCLI_BIN = "/usr/bin/nmcli"

NET_SYSFS_BASE = "/sys/class/net"
USB_DRIVERS_SYSFS_BASE = "/sys/bus/usb/drivers"
USBNET_PROC_NET_ROUTE = "/proc/net/route"
USBNET_PROC_ROOT = "/proc"
USBNET_IP_FORWARD_PATH = "/proc/sys/net/ipv4/ip_forward"

USBNET_ALLOWED_DRIVERS = frozenset(("rndis_host", "cdc_ncm"))
USBNET_ALLOWED_EGRESS_CHOICES = ("direct", "vpn")
USBNET_EGRESS_BRIDGE_NAME = {"direct": "br-stpusb-dir", "vpn": "br-stpusb-vpn"}
USBNET_EGRESS_CHAIN_SUFFIX = {"direct": "DIRECT", "vpn": "VPN"}
USBNET_GRANT_KEYS = frozenset((
    "session_id",
    "generation",
    "host_id",
    "device_serial",
    "usb_devpath",
    "egress",
    "iat",
    "exp",
))
USBNET_CONF_KEYS = frozenset((
    "usbnet_enabled",
    "host_id",
    "control_plane_verify_pubkey",
    "allowed_usb_ports",
    "allowed_egresses",
    "egress_profiles",
    "usbnet_max_devices",
))
USBNET_PROFILE_KEYS = frozenset((
    "bridge",
    "subnet_cidr",
    "upstream_dns",
    "uplink_iface",
    "local_dns_forward",
    "vpn_gateway_ips",
))
USBNET_BLOCKED_DEST_CIDRS = (
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "100.64.0.0/10",
    "127.0.0.0/8",
    "224.0.0.0/4",
)

_USBNET_BRIDGE_RE = re.compile(r"^br-stpusb-(dir|vpn)\Z")
_USBNET_IFACE_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,14}\Z")
_USBNET_INTF_RE = re.compile(r"^([0-9]+-[0-9]+(?:\.[0-9]+)*):[0-9]+\.[0-9]+\Z")
_USBNET_ID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}\Z")
_USBNET_SERIAL_RE = re.compile(r"^[A-Za-z0-9._:-]{1,64}\Z")
_USBNET_MAC_RE = re.compile(r"^[0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5}\Z")
_USBNET_USB_BUS_PART_RE = re.compile(r"^usb[0-9]+\Z")
_USBNET_B64URL_SEG_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")
_USBNET_VENDOR_RE = re.compile(r"^[0-9a-fA-F]{4}\Z")

USBNET_MARKER_DISABLED = "STP_USBNET_DISABLED"
USBNET_MARKER_GRANT_REVOKED = "STP_USBNET_GRANT_REVOKED"
USBNET_MARKER_ATTACH_ROLLBACK = "STP_USBNET_ATTACH_ROLLBACK"
USBNET_MARKER_BINDING_MISMATCH = "STP_USBNET_BINDING_MISMATCH"
USBNET_MARKER_ACTIVE_BINDINGS = "STP_USBNET_ACTIVE_BINDINGS"
USBNET_MARKER_TEARDOWN_INCOMPLETE = "STP_USBNET_TEARDOWN_INCOMPLETE"

# ── flash 链窄面（ADR-0037 D5 / #2133；#2284 最小权限；#2353 判据改成员资格）──
# udev 规则：固定路径 + **两个固定形态之一**（与 flash_preflight 最新版本的
# `_UDEV_RULE_PATH` / `_UDEV_RULE_LINE`(_LEGACY) 同源；0e8d = MediaTek ttyACM）。
# #2284：0660 + GROUP（Agent 用户属该组即可写，其它本地用户不可写）／0666 退化。
# #2353：形态由**调用者（Agent 用户）是否已持久属于 dialout** 决定，而非「组是否
# 存在」——组存在而调用者不是成员时，0660 对它等同于不可写（刷机 EACCES）。调用方
# 仍无参数面（D5 的窄面约束不变）。
UDEV_RULE_PATH = "/etc/udev/rules.d/98-ttyacm-mtk.rules"
UDEV_RULE_LINE = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n'
)
UDEV_RULE_LINE_LEGACY = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'
)


def _invoking_user() -> "str | None":
    """sudo 注入的调用者（SUDO_UID/SUDO_USER）；取不到返回 None。

    wrapper 只由 Agent 进程经 `sudo -n` 调用，因此该用户就是真正要写 ttyACM 的
    那个用户——0660 是否「可写」必须以它为准（#2353）。
    """
    uid = os.environ.get("SUDO_UID")
    if uid:
        try:
            return pwd.getpwuid(int(uid)).pw_name
        except (KeyError, ValueError):
            return None
    return os.environ.get("SUDO_USER") or None


def _user_in_dialout(user: "str | None") -> bool:
    """用户是否已**持久**属于 dialout（成员表或主组）。

    user=None（如 root 直接调用，非 Agent 路径）→ 退回 #2284 的旧判据：只看组是
    否存在。该路径下无法判定「谁会来写串口」，保持既有行为不扩大改动面。
    """
    try:
        entry = grp.getgrnam("dialout")
    except KeyError:
        return False
    if user is None:
        return True
    if user in entry.gr_mem:
        return True
    try:
        return pwd.getpwnam(user).pw_gid == entry.gr_gid
    except (KeyError, OSError):
        return False


def _udev_rule_line() -> str:
    """按调用者（Agent 用户）是否属于 dialout 选择规则形态（#2284 / #2353）。"""
    if _user_in_dialout(_invoking_user()):
        return UDEV_RULE_LINE
    return UDEV_RULE_LINE_LEGACY


def _udev_rule_present(body, line: str) -> bool:
    """文件内容是否**已含**目标规则行（逐行比对，忽略注释/空行）。

    #2284：安装链会在规则前写一行「为什么是这个形态」的说明（issue 建议 3：
    能回答「本机为什么是 0666」）。若按整文件比对，wrapper 每次都会判「不一致」
    重写一次，把理由擦掉且白记一次 changed；故只看目标行在不在。
    """
    if not body:
        return False
    wanted = line.strip()
    return any(seg.strip() == wanted for seg in body.splitlines())


# sysfs USB 设备根：authorized 门控的运行期写目标（flash_firmware 同源常量）。
# 注意：`/sys/bus/usb/devices/<port>` 在内核 sysfs 里是**符号链接**，指向
# `/sys/devices/.../<port>`；因此设备目录这一跳必须允许解析（校验解析结果），
# 只有属性文件（idVendor / authorized）用 O_NOFOLLOW 打开。
USB_SYSFS_BASE = "/sys/bus/usb/devices"
_SYSFS_DEVICES_ROOT = "/sys/devices"
_MTK_VENDOR_ID = "0e8d"

_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_VERSION_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# USB 端口名（sysfs 形态：总线-端口，可带 .端口 级联），例 "1-1" / "1-5.3.1"。
# 刻意严于 ADR 草稿的字符集口径：只接受该结构，`..`/`/`/空白等任何可移动
# 路径语义的形态在进入路径拼接前即被拒绝（#2133）。用 `\Z` 而非 `$`——`$`
# 会放行尾随换行（"1-1\n"），端口名将带着换行进路径。
_USB_PORT_RE = re.compile(r"^[0-9]+-[0-9]+(?:\.[0-9]+)*\Z")

# 与 tools/ansible/roles/agent_deploy/defaults/main.yml 的 agent_install_excludes、
# 以及**契约包**（`backend/agent/contracts/artifact_digest.py::PAYLOAD_EXCLUDES`，
# tar/digest 的单一源）逐项同源（#2030；ADR-0054 后契约侧为单一源，wrapper/YAML
# 因无法 import Python 仍是拷贝）——三处一致由
# tests/test_ansible_digest_contract.py 锁定；stp_schemas/ 与 wrapper
# 自身不进安装目录，venv//logs/ 为宿主侧目录（ADR-0040 D1 明文排除）。
FIXED_EXCLUDES = [
    "__pycache__/",
    "scripts/",  # ADR-0051 Phase 3：脚本走包分发，不再随源码树同步（与契约包 PAYLOAD_EXCLUDES 同源）
    "*.pyc",
    "test_*.py",
    "tests/",
    "venv/",
    "logs/",
    "install_agent.sh",
    "agentctl.sh",
    "DEPLOY.md",
    ".env.example",
    "stability-test-agent.service",
    "hosts.txt",
    "stp_schemas/",
    "stp_agent_priv.py",
    ".deps_installed_sha",
]
# 主机本地资源：不传输 + 不被 --delete-excluded 删除（#1248 语义）
# 模式形态见下方 PROTECT_ONLY_PATHS 的说明：本项**同时**被 exclude，因此
# 目录节点级 protect（`resources/mtbf/`）即可保住整棵子树——rsync 不会进入并
# 清空一个受保护的目录（实测，见 tests/test_agent_priv_apply_code_protection.py）。
HOST_LOCAL_PATHS = ["resources/mtbf/"]
# ADR-0040 §4.3 P2 前置（#1950）：resources/ 仅加 protect（防 --delete 清掉
# 229MB 大件），**不 exclude**——载荷仍携带 resources/ 期间分发照旧；P2 载荷
# 收缩（agent-code 剔除 resources/）后分发自然停止、保护已在位。
# ADR-0040 D8：host-resources 层退役后本保护**永久保留**——退役只停下发，不做主机清理
# （存量副本与 mtbf/ 主机本地资产原样保留）。
#
# #2019：必须写 `resources/***` 而**不是** `resources/`——rsync 里尾斜杠模式
# 只匹配**目录节点本身**：源树一旦含任一 `resources/*`，`--delete` 仍会清掉
# 接收端 `resources/**` 的其余内容（实测：b.bin / sub/c.bin / mtbf/apk.bin 全删）。
# 加 `***` 才是「该目录及其全部内容」（也覆盖节点本身）。
PROTECT_ONLY_PATHS = ["resources/***"]
# 部署态元数据（#2091）：由部署流程单独受控写入（VERSION / write-digest 系），
# 既不在载荷里、也不允许被 apply-code 的 --delete/--delete-excluded 清掉——
# 否则 code-only 收敛会把记号删掉 → 文件与主机列记录分叉。语义同
# PROTECT_ONLY_PATHS：只防删除、不做 exclude。ARTIFACT_DIGEST_RESOURCES 自
# ADR-0040 D8 R4 起不再有写入方（资源层退役），主机上的存量文件仍按「不做主机清理」保留。
PROTECT_ONLY_METADATA = ["VERSION", "ARTIFACT_DIGEST", "ARTIFACT_DIGEST_RESOURCES"]


class PrivError(RuntimeError):
    """可读拒绝原因；统一 exit 2 + stderr 哨兵。"""


def _fail(message):
    raise PrivError(message)


# ---------------------------------------------------------------------------
# 基础校验与进程工具
# ---------------------------------------------------------------------------

def _run(argv, pass_fds=(), preexec_fn=None):
    """返回 (rc, stdout, stderr)，全部为 str。"""
    proc = subprocess.Popen(
        argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        pass_fds=pass_fds, preexec_fn=preexec_fn,
    )
    out, err = proc.communicate()
    return (
        proc.returncode,
        out.decode("utf-8", "replace"),
        err.decode("utf-8", "replace"),
    )


def _caller_uid():
    """sudo 调用者 uid；直接以 root 运行时返回 (None, None) 表示可信。"""
    raw = os.environ.get("SUDO_UID", "").strip()
    if not raw:
        return None, None
    try:
        uid = int(raw)
    except ValueError:
        _fail("SUDO_UID is not an integer")
    gid_raw = os.environ.get("SUDO_GID", "").strip()
    gid = int(gid_raw) if gid_raw.isdigit() else None
    if uid == 0:
        return None, None
    return uid, gid


def _require_root():
    if os.geteuid() != 0:
        _fail("must run as root (sudo)")


def is_within(path, parent):
    """realpath(path) 是否位于 realpath(parent) 内（组件级，非字符串前缀）。"""
    path_real = os.path.realpath(path)
    parent_real = os.path.realpath(parent)
    if path_real == parent_real:
        return True
    return path_real.startswith(parent_real.rstrip(os.sep) + os.sep)


def validate_usb_port(port):
    """USB 端口名校验（纯函数）：合法返回原名，非法即拒绝（#2133/D5）。"""
    if not isinstance(port, str) or not _USB_PORT_RE.match(port):
        _fail(
            "--port must be a sysfs USB port name like '1-1' or '1-5.3.1', "
            "got %r" % (port,)
        )
    return port


def is_kernel_usb_device_path(resolved, port):
    """解析后的设备目录是否是内核 sysfs 设备树中与端口名对应的真实目录。

    （纯函数；``resolved`` 应为 ``realpath`` 结果）判据两条：位于
    ``/sys/devices`` 之下 + basename 与端口名一致——这样「经符号链接落到
    别处」的目录（非内核设备树）会被拒，而内核标准布局
    ``/sys/devices/.../usb1/1-5.3.1`` 正常放行。
    """
    root = os.path.realpath(_SYSFS_DEVICES_ROOT)
    if not resolved.startswith(root.rstrip(os.sep) + os.sep):
        return False
    return os.path.basename(resolved) == port


# #1553：`fix-ownership` 对 conf 的 INSTALL_DIR 做 `chown -R -h`，`apply-code` 往
# `INSTALL_DIR/agent/` 做 `rsync --delete`——两者都以 root 执行。若 INSTALL_DIR 被
# 指到系统目录（`/etc`、`/`、`/usr/local` 等），等于把这些目录整棵交给 AGENT_USER。
#
# 刻意**不含 `/srv`**：`/srv/stp` 是既有的安装形态（容器冒烟
# tools/dev/stp_agent_priv_smoke.sh 即用它），把它禁掉会打断合法部署。
_INSTALL_DIR_FORBIDDEN_ROOTS = (
    "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/lib64", "/boot",
    "/root", "/home", "/dev", "/proc", "/sys", "/run",
)


def _validate_install_dir(install_dir):
    """INSTALL_DIR 基线护栏（#1553）：绝对非根，且与系统关键目录不得互相包含。

    「互相包含」两个方向都要拦：
    - install_dir 在关键目录**之下**（如 `/usr/local`）——对它 `chown -R` 会把
      `/usr/local/sbin/stp-agent-priv` 本体交给 AGENT_USER，随后用同一条 NOPASSWD
      规则以 root 执行任意代码；
    - install_dir 是某关键目录的**祖先**（如 `/etc` 之于 `/etc/sudoers.d`，`/` 之于
      一切）——`chown -R` 一次就把整棵子树交出去。

    放在这里而不是只放 cmd_bootstrap：任何读 conf 的子命令都要过，这样即使某台
    主机上已经存在被写坏的 conf，`apply-code` / `fix-ownership` 也会先拒绝。
    标准安装目录 `/opt/stability-test-agent` 两个方向都不命中。
    """
    if not install_dir.startswith("/") or install_dir == "/":
        _fail("INSTALL_DIR must be an absolute non-root path")
    normalized = install_dir.rstrip("/")
    if normalized != os.path.normpath(normalized) or normalized.startswith("//"):
        _fail("INSTALL_DIR must be a normalized absolute path")
    for critical in _INSTALL_DIR_FORBIDDEN_ROOTS:
        if (
            normalized == critical
            or normalized.startswith(critical + "/")
            or critical.startswith(normalized + "/")
        ):
            _fail(
                "INSTALL_DIR must not overlap a system directory: %s vs %s "
                "(fix-ownership chowns it recursively and apply-code rsyncs into it "
                "as root)" % (install_dir, critical)
            )


def _reject_anchor_drift(conf_path, install_dir, user, group, service):
    """conf 一旦存在，安装锚点不得再变（#1553）。

    安装链（``install_agent.sh``）与 Ansible 更新
    （``update_agent.yml`` 的 ``become: true``）每次都传同一组值，因此重跑
    bootstrap 是幂等的、不受影响；而被边界圈住的主体即使能经 NOPASSWD 面调用
    bootstrap，也无法把 INSTALL_DIR 移向系统目录。

    刻意**不做**「要求 SUDO_UID 未设置」的调用者校验：Ansible 更新以
    ``ansible_user=android`` 执行 ``become: true``，此时 ``SUDO_UID`` 已被设置，
    那条检查会打断正常部署。合法迁移安装目录时，先由 root 删除 conf 再 bootstrap。
    """
    try:
        with open(conf_path, "r", encoding="utf-8") as handle:
            existing = {}
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                existing[key.strip()] = value.strip()
    except OSError:
        return  # 首次 bootstrap：conf 尚不存在

    wanted = {
        "INSTALL_DIR": install_dir,
        "AGENT_USER": user,
        "AGENT_GROUP": group,
        "SERVICE_NAME": service,
    }
    drift = [
        "%s: %r -> %r" % (key, existing.get(key), value)
        for key, value in wanted.items()
        if existing.get(key) not in (None, value)
    ]
    if drift:
        _fail(
            "bootstrap must not re-point an existing install (deliberate migration: "
            "remove %s as root first): %s" % (conf_path, "; ".join(drift))
        )


def _load_conf(path):
    try:
        st = os.stat(path)
    except OSError as exc:
        _fail("config not found: %s (%s)" % (path, exc))
    if not stat.S_ISREG(st.st_mode):
        _fail("config is not a regular file: %s" % path)
    if st.st_uid != 0:
        _fail("config must be owned by root: %s" % path)
    if st.st_mode & 0o022:
        _fail("config must not be group/other writable: %s" % path)

    conf = {}
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            conf[key.strip()] = value.strip()

    for key in ("INSTALL_DIR", "AGENT_USER", "AGENT_GROUP", "SERVICE_NAME"):
        if not conf.get(key):
            _fail("config missing %s: %s" % (key, path))
    install_dir = conf["INSTALL_DIR"]
    _validate_install_dir(install_dir)
    for key in ("AGENT_USER", "AGENT_GROUP", "SERVICE_NAME"):
        if not _NAME_RE.match(conf[key]):
            _fail("config %s has invalid characters" % key)
    return conf


def _conf_path():
    return os.environ.get("STP_AGENT_PRIV_CONF", DEFAULT_CONF_PATH)


def _open_directory(path, dir_fd=None, create=False):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    descriptor = os.open("/" if os.path.isabs(path) else ".", flags, dir_fd=dir_fd)
    try:
        for component in path.split(os.sep):
            if not component or component == ".":
                continue
            if component == "..":
                _fail("parent traversal is not allowed")
            if create:
                try:
                    os.mkdir(component, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


@contextmanager
def _target_directory(conf, child=None, create=False):
    descriptor = _open_directory(conf["INSTALL_DIR"])
    try:
        if child:
            child_descriptor = _open_directory(child, dir_fd=descriptor, create=create)
            os.close(descriptor)
            descriptor = child_descriptor
        yield descriptor
    finally:
        os.close(descriptor)


def _agent_identity(conf):
    account = pwd.getpwnam(conf["AGENT_USER"])
    return account.pw_uid, grp.getgrnam(conf["AGENT_GROUP"]).gr_gid


def _atomic_write_at(directory_fd, name, body, mode, owner=None):
    temporary = ".stp-priv-" + uuid.uuid4().hex
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600, dir_fd=directory_fd,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            if owner is not None:
                os.fchown(handle.fileno(), *owner)
            os.fchmod(handle.fileno(), mode)
        os.replace(temporary, name, src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
    except Exception:
        try:
            os.unlink(temporary, dir_fd=directory_fd)
        except OSError:
            pass
        raise


def _atomic_write(path, body, mode):
    descriptor = _open_directory(os.path.dirname(path) or ".")
    try:
        _atomic_write_at(descriptor, os.path.basename(path), body, mode)
    finally:
        os.close(descriptor)


def _read_regular_at(directory_fd, name, limit, owner=None):
    descriptor = os.open(
        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd,
    )
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        if not stat.S_ISREG(metadata.st_mode):
            _fail("input must be a regular file")
        if owner is not None and metadata.st_uid != owner:
            _fail("input must be owned by the calling user")
        if metadata.st_size > limit:
            _fail("input too large")
        body = handle.read(limit + 1)
        if len(body.encode("utf-8")) > limit:
            _fail("input too large")
        return body, metadata


def _read_sysfs_attr(directory_fd, name, limit):
    """读 sysfs 属性文件（**不得**用 st_size 判定大小，#2160）。

    sysfs 属性文件的 ``st_size`` 恒为页大小（实测 4096），与内容长度无关
    （``idVendor`` 内容 5 字节而 st_size=4096）——沿用 `_read_regular_at`
    的 ``st_size > limit`` 判定会使读取恒拒（"input too large"）。这里只
    校验「常规文件 + 实际读到的字节数」；读取按 ``limit+1`` 截断，超限即拒。
    """
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                         dir_fd=directory_fd)
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            _fail("sysfs attribute must be a regular file: %s" % name)
        body = handle.read(limit + 1)
    if not body:
        _fail("sysfs attribute is empty: %s" % name)
    if len(body.encode("utf-8")) > limit:
        _fail("sysfs attribute too large: %s" % name)
    return body


def _visudo_check(path):
    visudo = shutil.which("visudo") or "/usr/sbin/visudo"
    if not os.path.exists(visudo):
        return
    rc, _, err = _run([visudo, "-cf", path])
    if rc != 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        _fail("visudo rejected generated sudoers: %s" % err.strip()[:300])


def _decode_b64(raw, label):
    if not raw or len(raw) > MAX_ENV_PAYLOAD_BYTES:
        _fail("%s payload missing or too large" % label)
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception as exc:
        _fail("%s payload is not valid base64/utf-8: %s" % (label, exc))


# ---------------------------------------------------------------------------
# 子命令
# ---------------------------------------------------------------------------

def cmd_capabilities(args, conf):
    """打印本 wrapper 支持的子命令（每行一个）——供交付链做**能力**判定（#2319）。

    为什么需要它：``selftest`` 只保证 wrapper **自洽**（属主/权限 + parser↔契约表两两
    一致），对「本机 wrapper 是否具备调用方将要使用的子命令」没有判别力——缺一个子命令
    但内部自洽的旧 wrapper 同样打 OK、``exit 0``。控制面远端脚本据此在**任何写动作之前**
    fail-closed，而不是走到第一次调用处才被 argparse 拒绝（那时代码已同步、服务已重启）。
    """
    for command in sorted(_SUBCOMMAND_CONTRACT):
        print(command)
    for token in _EXTRA_CAPABILITIES:
        print(token)
    return 0


def cmd_selftest(args, conf):
    problems = []
    try:
        st = os.stat(WRAPPER_PATH)
        if st.st_uid != 0 or (st.st_mode & 0o022):
            problems.append("wrapper must be root-owned and not writable by others")
    except OSError as exc:
        problems.append("wrapper stat failed: %s" % exc)
    # #2011：子命令接线契约（真实 argv parse 断言）——能力探针的失败面由此闭合。
    problems.extend(_validate_parser_contract(_build_parser()))
    if problems:
        print("STP_AGENT_PRIV_SELFTEST_FAIL: " + "; ".join(problems))
        return 1
    print("STP_AGENT_PRIV_SELFTEST_OK wrapper=%s conf=%s install_dir=%s"
          % (WRAPPER_PATH, _conf_path(), conf["INSTALL_DIR"]))
    return 0


def build_sudoers_lines(user, service):
    """生成 NOPASSWD sudoers 内容（纯函数，便于单测锁定规则面）。

    只允许：固定服务名的 systemctl 子命令 + wrapper 单命令；不得包含
    任意 rsync/cp/chmod/chown/ln/stat 规则（#1250 修复面）。
    """
    lines = [
        "# Generated by stp-agent-priv bootstrap (#1250) - do not edit by hand.",
        "# 服务管理（固定命令，无参数面）",
    ]
    for binary in ("/bin/systemctl", "/usr/bin/systemctl"):
        for action in ("start", "stop", "restart", "status"):
            lines.append(
                "%s ALL=(root) NOPASSWD: %s %s %s"
                % (user, binary, action, service)
            )
        lines.append(
            "%s ALL=(root) NOPASSWD: %s daemon-reload" % (user, binary)
        )
    lines += [
        "# 升级提权入口：wrapper 内部校验参数与路径（ADR-0037）",
        "%s ALL=(root) NOPASSWD: %s" % (user, WRAPPER_PATH),
    ]
    return lines


def cmd_bootstrap(args, _conf):
    _require_root()
    install_dir = os.path.realpath(args.install_dir)
    _validate_install_dir(install_dir)
    if not os.path.isdir(install_dir):
        _fail("--install-dir does not exist: %s" % install_dir)
    for label, value in (
        ("--user", args.user), ("--group", args.group), ("--service", args.service),
    ):
        if not _NAME_RE.match(value):
            _fail("%s has invalid characters" % label)

    conf_path = _conf_path()
    _reject_anchor_drift(conf_path, install_dir, args.user, args.group, args.service)
    conf_body = (
        "# stp-agent-priv config (#1250) - generated by bootstrap, do not edit\n"
        "INSTALL_DIR=%s\nAGENT_USER=%s\nAGENT_GROUP=%s\nSERVICE_NAME=%s\n"
        % (install_dir, args.user, args.group, args.service)
    )
    _atomic_write(conf_path, conf_body, 0o644)

    sudoers_lines = build_sudoers_lines(args.user, args.service)
    sudoers_path = os.path.join(SUDOERS_DIR, args.service)
    os.makedirs(SUDOERS_DIR, exist_ok=True)
    _atomic_write(sudoers_path, "\n".join(sudoers_lines) + "\n", 0o440)
    _visudo_check(sudoers_path)
    print("STP_AGENT_PRIV_BOOTSTRAP_OK conf=%s sudoers=%s" % (conf_path, sudoers_path))
    return 0


def build_apply_code_filters() -> list:
    """apply-code 的 rsync filter 参数（纯函数，单测锁定保护面）。

    #2091：``--delete``/``--delete-excluded`` 下，未在 filter 里出现的接收端
    文件会被删除；``protect`` 是唯一能同时拦住「不在源里」与「被 exclude」
    两类删除的语义。
    """
    args = []
    for item in FIXED_EXCLUDES:
        args.append("--exclude=%s" % item)
    for item in HOST_LOCAL_PATHS:
        args.append("--exclude=%s" % item)
        args.append("--filter=protect %s" % item)
    for item in PROTECT_ONLY_PATHS:
        # 只防删除不拦同步（#1950：载荷仍携带 resources/ 期间分发照旧）
        args.append("--filter=protect %s" % item)
    for item in PROTECT_ONLY_METADATA:
        # #2091：部署态元数据只保护、不 exclude（源树里本就没有这些文件）
        args.append("--filter=protect %s" % item)
    return args


def cmd_apply_code(args, conf):
    _require_root()
    caller_uid, _ = _caller_uid()
    staged = os.path.abspath(args.staged)
    if is_within(staged, conf["INSTALL_DIR"]):
        _fail("--staged must be outside INSTALL_DIR")
    if not os.path.isfile(RSYNC_BIN):
        _fail("rsync not found: %s" % RSYNC_BIN)
    agent_uid, agent_gid = _agent_identity(conf)
    if agent_uid == 0:
        _fail("apply-code requires a non-root AGENT_USER")

    def drop_privileges():
        os.initgroups(conf["AGENT_USER"], agent_gid)
        os.setgid(agent_gid)
        os.setuid(agent_uid)

    argv = [
        RSYNC_BIN, "-a", "--no-owner", "--no-group", "--delete",
        "--delete-excluded", "--safe-links",
    ]
    argv += build_apply_code_filters()
    staged_fd = _open_directory(staged)
    try:
        if caller_uid is not None and os.fstat(staged_fd).st_uid != caller_uid:
            _fail("--staged must be owned by the calling user")
        with _target_directory(conf, "agent", create=True) as target_fd:
            os.fchown(target_fd, agent_uid, agent_gid)
            argv += ["/proc/self/fd/%d/" % staged_fd, "/proc/self/fd/%d/" % target_fd]
            rc, _, err = _run(
                argv, pass_fds=(staged_fd, target_fd), preexec_fn=drop_privileges,
            )
    finally:
        os.close(staged_fd)
    if rc != 0:
        _fail("rsync failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_APPLY_CODE_OK")
    return 0


def cmd_install_schema(args, conf):
    _require_root()
    caller_uid, _ = _caller_uid()
    source = os.path.abspath(args.file)
    source_fd = _open_directory(os.path.dirname(source))
    try:
        body, _ = _read_regular_at(
            source_fd, os.path.basename(source), MAX_SCHEMA_BYTES, owner=caller_uid,
        )
        payload = json.loads(body)
    except (OSError, ValueError) as exc:
        _fail("--file is not valid JSON: %s" % exc)
    finally:
        os.close(source_fd)
    properties = payload.get("properties") if isinstance(payload, dict) else None
    if not isinstance(payload, dict) or "$schema" not in payload or not isinstance(properties, dict) \
            or "lifecycle" not in properties:
        _fail("--file is not a Pipeline schema ($schema/lifecycle mismatch)")

    with _target_directory(conf, "schemas", create=True) as target_fd:
        _atomic_write_at(
            target_fd, "pipeline_schema.json", body, 0o644, _agent_identity(conf),
        )
    print("STP_INSTALL_SCHEMA_OK")
    return 0


def cmd_write_version(args, conf):
    _require_root()
    version = args.version.strip()
    if not version:
        print("STP_WRITE_VERSION_SKIPPED")
        return 0
    if not _VERSION_RE.match(version):
        _fail("--version must be a short git SHA (7-40 hex chars)")
    with _target_directory(conf, "agent") as target_fd:
        _atomic_write_at(target_fd, "VERSION", version + "\n", 0o644, _agent_identity(conf))
    print("STP_WRITE_VERSION_OK version=%s" % version)
    return 0


def cmd_write_digest(args, conf):
    """ADR-0040 D2：部署收敛成功后受控写入 ARTIFACT_DIGEST（write-version 同族）。

    只有 agent-code 一个身份：ADR-0040 D8 R4 起 host-resources 层退役，原 ``--kind``
    （#1963，resources → ARTIFACT_DIGEST_RESOURCES）随之删除。
    """
    _require_root()
    digest = args.digest.strip()
    if not digest:
        print("STP_WRITE_DIGEST_SKIPPED")
        return 0
    prefix, _, hexpart = digest.partition(":")
    if prefix != "sha256" or not _SHA256_RE.match(hexpart):
        _fail("--digest must be 'sha256:<64 hex chars>' (ADR-0040 D1)")
    with _target_directory(conf, "agent") as target_fd:
        _atomic_write_at(
            target_fd, "ARTIFACT_DIGEST", digest + "\n", 0o644, _agent_identity(conf),
        )
    print("STP_WRITE_DIGEST_OK digest=%s" % digest)
    return 0


def _write_env_preserving_owner(directory_fd, lines, metadata):
    """原子替换 .env，但保留原 uid/gid 与 mode——与旧写法（原地截断）等价。

    #2069 第二层：逐行底线校验——键名侧的白名单在 `_sync_env`，但**任何**写入
    路径都不得让值里夹带的换行顶出额外行（未来的新写路径同样被这层拦住）。
    """
    for line in lines:
        _reject_unsafe_env_line(line)
    body = "\n".join(lines) + ("\n" if lines else "")
    _atomic_write_at(
        directory_fd, ".env", body, metadata.st_mode & 0o777,
        (metadata.st_uid, metadata.st_gid),
    )


_ENV_LINE_FORBIDDEN = (("CR", "\r"), ("LF", "\n"), ("NUL", "\x00"))


def _reject_unsafe_env_line(text, label="env line"):
    """#2069：含 CR/LF/NUL 的 `.env` 行一律拒绝（换行 = 向配置文件注入新行）。

    错误信息只回显**位置标签**，不回显值内容（override 可能承载凭据类配置）。
    """
    for name, char in _ENV_LINE_FORBIDDEN:
        if char in text:
            _fail("%s contains a forbidden control character (%s)" % (label, name))


def cmd_sync_env(args, conf):
    _require_root()
    with _target_directory(conf) as target_fd:
        return _sync_env(args, target_fd)


def _sync_env(args, target_fd):
    body, metadata = _read_regular_at(target_fd, ".env", MAX_ENV_PAYLOAD_BYTES)
    lines = body.splitlines()

    if args.secret_b64:
        secret = _decode_b64(args.secret_b64, "secret")
        replaced = False
        updated = []
        for line in lines:
            if line.startswith("AGENT_SECRET="):
                updated.append("AGENT_SECRET=" + secret)
                replaced = True
            else:
                updated.append(line)
        if not replaced:
            updated.append("AGENT_SECRET=" + secret)
        _write_env_preserving_owner(target_fd, updated, metadata)
        print("STP_ENV_SECRET_SYNCED=1")
        return 0

    overrides = json.loads(_decode_b64(args.overrides_b64 or "", "overrides"))
    path_keys = json.loads(_decode_b64(args.path_keys_b64 or "", "path_keys"))
    if not isinstance(overrides, dict) or not isinstance(path_keys, list):
        _fail("overrides/path_keys payload shape invalid")
    for key, value in overrides.items():
        if not _NAME_RE.match(str(key)) or not isinstance(value, str):
            _fail("override entry invalid: %r" % key)
        # #2069：值侧与键侧同档校验——值内换行会顶出额外行（如注入 LD_PRELOAD）。
        _reject_unsafe_env_line(value, "override value for %r" % key)

    # #3356：退役键——merge 后从 .env 整行删除。空载荷 = 无退役键（老调用方兼容）。
    if args.retired_keys_b64:
        retired_keys = json.loads(
            _decode_b64(args.retired_keys_b64, "retired_keys")
        )
    else:
        retired_keys = []
    if not isinstance(retired_keys, list) or not all(
        isinstance(key, str) for key in retired_keys
    ):
        _fail("retired_keys payload shape invalid")
    for key in retired_keys:
        if not _NAME_RE.match(key):
            _fail("retired key invalid: %r" % key)
    _conflict = sorted(set(retired_keys) & set(overrides))
    if _conflict:
        # 渲染面与退役表相顶 = 控制面两表矛盾：同步每轮先写后删，渲染面静默失效。
        _fail("retired keys also in overrides: %s" % ",".join(_conflict))

    if not overrides and not retired_keys:
        print("STP_ENV_SYNCED=")
        print("STP_ENV_RETIRED_REMOVED=")
        print("STP_ENV_PATH_MISSING=")
        return 0

    seen = set()
    updated_keys = []
    new_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            new_lines.append(line)
            continue
        key = line.partition("=")[0].strip()
        if key in overrides:
            new_lines.append("%s=%s" % (key, overrides[key]))
            seen.add(key)
            updated_keys.append(key)
        else:
            new_lines.append(line)
    for key, value in overrides.items():
        if key not in seen:
            new_lines.append("%s=%s" % (key, value))
            updated_keys.append(key)

    # #3356：删退役键行（在 overrides merge 之后——两集合已验证不相交）。
    # 删除只发生在解析出 KEY=VALUE 形态的行上；注释与空行原样保留。
    retired_set = set(retired_keys)
    removed_keys = set()
    if retired_set:
        kept_lines = []
        for line in new_lines:
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in line:
                key = line.partition("=")[0].strip()
                if key in retired_set:
                    removed_keys.add(key)
                    continue
            kept_lines.append(line)
        new_lines = kept_lines

    if overrides or removed_keys:
        _write_env_preserving_owner(target_fd, new_lines, metadata)
    print("STP_ENV_SYNCED=" + ",".join(sorted(updated_keys)))
    print("STP_ENV_RETIRED_REMOVED=" + ",".join(sorted(removed_keys)))

    missing = {
        key: overrides[key]
        for key in sorted(path_keys)
        if key in overrides and not os.path.exists(overrides[key])
    }
    encoded = base64.b64encode(
        json.dumps(missing, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    print("STP_ENV_PATH_MISSING=" + encoded)
    return 0


def cmd_deps_marker(args, conf):
    _require_root()
    sha = args.sha.strip()
    if not _SHA256_RE.match(sha):
        _fail("--sha must be a sha256 hex digest")
    with _target_directory(conf) as target_fd:
        _atomic_write_at(
            target_fd, ".deps_installed_sha", sha + "\n", 0o644, _agent_identity(conf),
        )
    print("STP_DEPS_MARKER_OK")
    return 0


def cmd_fix_ownership(args, conf):
    _require_root()
    owner = _agent_identity(conf)
    with _target_directory(conf) as target_fd:
        for _, directories, files, directory_fd in os.fwalk(
            ".", topdown=False, onerror=_fail, follow_symlinks=False, dir_fd=target_fd,
        ):
            for name in directories + files:
                os.chown(name, *owner, dir_fd=directory_fd, follow_symlinks=False)
            os.fchown(directory_fd, *owner)
    print("STP_FIX_OWNERSHIP_OK")
    return 0


def cmd_restart(args, conf):
    _require_root()
    rc, _, err = _run([SYSTEMCTL_BIN, "restart", conf["SERVICE_NAME"]])
    if rc != 0:
        _fail("systemctl restart failed rc=%s: %s" % (rc, err.strip()[:300]))
    print("STP_RESTART_OK")
    return 0


def cmd_ensure_udev_rule(args, conf):
    """写固定 udev 规则（MTK ttyACM）并 reload（#2133 / D5；#2284；#2353）。

    面约束：路径是常量，内容是**两个固定形态之一**（由**调用者是否已持久属于
    dialout** 决定，见 `_udev_rule_line`）——调用方仍无参数面；目录逐级
    O_NOFOLLOW 打开、写入走临时文件 + os.replace（不跟随目标位置的 symlink）；
    幂等——内容一致时不重写，仅执行 reload。``udevadm control --reload`` 失败即
    拒绝；``trigger`` 为尽力而为（与旧版 flash_preflight 修复分支同语义）。
    """
    _require_root()
    if not os.path.isfile(UDEVADM_BIN):
        _fail("udevadm not found: %s" % UDEVADM_BIN)
    rules_dir = os.path.dirname(UDEV_RULE_PATH)
    name = os.path.basename(UDEV_RULE_PATH)
    line = _udev_rule_line()
    changed = 0
    descriptor = _open_directory(rules_dir, create=True)
    try:
        try:
            body, _ = _read_regular_at(descriptor, name, 4096)
        except (PrivError, OSError):
            body = None
        if not _udev_rule_present(body, line):
            _atomic_write_at(descriptor, name, line, 0o644)
            changed = 1
    finally:
        os.close(descriptor)
    rc, _, err = _run([UDEVADM_BIN, "control", "--reload"])
    if rc != 0:
        _fail("udevadm control --reload failed rc=%s: %s" % (rc, err.strip()[:300]))
    _run([UDEVADM_BIN, "trigger"])
    print("STP_ENSURE_UDEV_RULE_OK path=%s changed=%d" % (UDEV_RULE_PATH, changed))
    return 0


def cmd_usb_authorized(args, conf):
    """切换 MTK 设备 sysfs ``authorized``（#2133 / D5）。

    flash_firmware 的门控面：多设备台架刷机时隐藏非目标 MTK 口（authorized=0）
    并在结束后恢复（authorized=1）。面约束：端口名走 ``_USB_PORT_RE``（路径
    语义在拼接前被拒）；设备目录允许解析内核 sysfs 符号链接，但解析结果必须
    是 ``/sys/devices`` 下与端口名同名的真实目录（``is_kernel_usb_device_path``）；
    目标必须是 ``idVendor=0e8d`` 的 MediaTek 设备；值仅 {0,1}。属性文件
    （idVendor / authorized）以 O_NOFOLLOW 打开，sysfs 属性**原地写**（不可
    rename），单次 write。
    """
    _require_root()
    port = validate_usb_port(args.port)
    value = args.value
    if value not in ("0", "1"):
        _fail("--value must be 0 or 1")
    device_dir = os.path.join(USB_SYSFS_BASE, port)
    resolved = os.path.realpath(device_dir)
    if not is_kernel_usb_device_path(resolved, port):
        _fail(
            "--port %s does not resolve to a kernel sysfs USB device (got %s)"
            % (port, resolved)
        )
    try:
        descriptor = os.open(
            device_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC,
        )
    except OSError as exc:
        _fail("cannot open USB device dir for %s: %s" % (port, exc))
    try:
        try:
            vendor = _read_sysfs_attr(descriptor, "idVendor", 64)
        except (PrivError, OSError) as exc:
            _fail("cannot read idVendor for %s: %s" % (port, exc))
        if vendor.strip().lower() != _MTK_VENDOR_ID:
            _fail(
                "--port %s is not a MediaTek device (idVendor=%r)"
                % (port, vendor.strip()[:16])
            )
        try:
            target = os.open(
                "authorized", os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=descriptor,
            )
        except OSError as exc:
            _fail("cannot open authorized for %s: %s" % (port, exc))
        try:
            os.write(target, (value + "\n").encode("ascii"))
        finally:
            os.close(target)
    finally:
        os.close(descriptor)
    print("STP_USB_AUTHORIZED_OK port=%s value=%s" % (port, value))
    return 0


def validate_since_epoch(value, now):
    """``--since-epoch`` 取值校验（纯函数）：非负整数且不晚于当前时间（#2957）。"""
    if value < 0:
        _fail("--since-epoch must be >= 0, got %d" % value)
    if value > now:
        _fail("--since-epoch must not be in the future (got %d, now=%d)" % (value, now))
    return value


def validate_kernel_log_lines(value):
    """``--lines`` 取值校验（纯函数）：None 或 1..MAX（#2957）。"""
    if value is None:
        return None
    if not 1 <= value <= MAX_KERNEL_LOG_LINES:
        _fail("--lines must be within 1..%d, got %d" % (MAX_KERNEL_LOG_LINES, value))
    return value


def build_kernel_log_argv(*, boot, since_epoch, lines):
    """固定 argv 构造（纯函数，单测锁定白名单面）。

    只可能出现：``-k --no-pager -o cat`` + ``--boot|--since @<int>`` + ``--lines=<n>``。
    任何路径/单元/匹配表达式（``--file`` / ``-D`` / ``-M`` / ``-u`` / ``--grep`` 等）
    都不在构造面里——它们连参数解析层都过不去。
    """
    argv = [JOURNALCTL_BIN, "-k", "--no-pager", "-o", "cat"]
    argv += ["--boot"] if boot else ["--since", "@%d" % since_epoch]
    if lines is not None:
        argv.append("--lines=%d" % lines)
    return argv


def _run_kernel_log(argv, timeout=None, max_bytes=None):
    """执行固定形态的 journalctl 并搬运输出；**不复用 ``_run``**（#2957 约束②）。

    不复用的理由：``_run`` 继承调用者环境、无超时、无输出上限。本子命令按裁决
    显式收口三件事——环境清空（``KERNEL_LOG_ENV``）、30s 超时、8 MiB 上限；
    超限即判**截断样本**（不可当完整结果），由调用方以非零退出码上报。

    Returns:
        ``(status, stdout_bytes, detail)``；``status`` ∈
        ``ok`` / ``truncated`` / ``timeout`` / ``failed``。
    """
    timeout = KERNEL_LOG_TIMEOUT_SECONDS if timeout is None else timeout
    max_bytes = MAX_KERNEL_LOG_BYTES if max_bytes is None else max_bytes
    try:
        proc = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(KERNEL_LOG_ENV),
            close_fds=True,
        )
    except OSError as exc:
        return "failed", b"", "cannot exec %s: %s" % (argv[0], exc)

    deadline = time.monotonic() + timeout
    chunks = []
    total = 0
    status = "ok"
    fd = proc.stdout.fileno()
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                break
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                status = "timeout"
                break
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                # 只判超额、不保留超额内容：截断样本一律作废（#2957 实测 373,600 行）
                status = "truncated"
                break
            chunks.append(chunk)
    finally:
        if status != "ok":
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        detail_err = b""
        try:
            detail_err = proc.stderr.read(8192) or b""
        except OSError:
            pass
        proc.stdout.close()
        proc.stderr.close()

    if status != "ok":
        return status, b"", detail_err.decode("utf-8", "replace").strip()
    if proc.returncode != 0:
        return (
            "failed",
            b"",
            "rc=%s %s" % (proc.returncode, detail_err.decode("utf-8", "replace").strip()[:300]),
        )
    return "ok", b"".join(chunks), ""


def cmd_read_kernel_log(args, conf):
    """只读搬运内核日志（#2957 / ADR-0037 D7）。

    面约束：``--boot`` 与 ``--since-epoch`` 二选一（解析层互斥 + required），
    ``--since-epoch`` ∈ [0, now]、``--lines`` ∈ [1, 5000]；除这两个整数外不接受
    任何 journalctl 选项。解析仍在 Agent 侧（``kernel_usb_faults``），本命令
    只搬运原始输出、不做过滤判定；输出被截断时**不做部分投递**，以非零退出码
    + stderr 标记告知调用方「这一次不可用」。
    """
    _require_root()
    since_epoch = None
    if args.since_epoch is not None:
        since_epoch = validate_since_epoch(args.since_epoch, int(time.time()))
    lines = validate_kernel_log_lines(args.lines)
    argv = build_kernel_log_argv(boot=bool(args.boot), since_epoch=since_epoch, lines=lines)
    status, payload, detail = _run_kernel_log(argv)
    if status == "truncated":
        print(
            "STP_READ_KERNEL_LOG_TRUNCATED limit_bytes=%d" % MAX_KERNEL_LOG_BYTES,
            file=sys.stderr,
        )
        return 3
    if status == "timeout":
        print(
            "STP_READ_KERNEL_LOG_TIMEOUT seconds=%d" % KERNEL_LOG_TIMEOUT_SECONDS,
            file=sys.stderr,
        )
        return 3
    if status != "ok":
        print("STP_READ_KERNEL_LOG_FAILED %s" % detail, file=sys.stderr)
        return 3
    sys.stdout.write(payload.decode("utf-8", "replace"))
    return 0


def validate_usb_power_serial(serial):
    """usb-power 的 serial 校验（纯函数）：只接受 adb serial 的常见字符集。"""
    if not isinstance(serial, str) or not _USB_POWER_SERIAL_RE.match(serial):
        _fail("--serial must match [A-Za-z0-9._:-]{1,64}, got %r" % (serial,))
    return serial


def validate_usb_power_expect_slot(value):
    """--expect-slot 校验（纯函数）：`<hub_location>:<port>`，取值口径与映射条目相同。"""
    if not isinstance(value, str) or value.count(":") != 1:
        _fail("--expect-slot must be <hub_location>:<port>, got %r" % (value,))
    location, _, port_text = value.partition(":")
    if not _USB_PORT_RE.match(location):
        _fail("--expect-slot has an invalid hub_location %r" % (location,))
    if not re.match(r"^[1-9][0-9]?\Z", port_text) or not 1 <= int(port_text) <= USB_POWER_MAX_PORT:
        _fail("--expect-slot port must be an integer within 1..%d" % USB_POWER_MAX_PORT)
    return location, int(port_text)


def validate_usb_power_request(action, delay):
    """动作与延迟校验（纯函数）：delay 只对 cycle 有效，取值 1..30。"""
    if action not in USB_POWER_ACTIONS:
        _fail("--action must be one of %s, got %r" % ("/".join(USB_POWER_ACTIONS), action))
    if delay is None:
        return action, None
    if action != "cycle":
        _fail("--delay is only valid with --action cycle")
    if isinstance(delay, bool) or not isinstance(delay, int):
        _fail("--delay must be an integer")
    if not 1 <= delay <= USB_POWER_MAX_DELAY:
        _fail("--delay must be within 1..%d, got %d" % (USB_POWER_MAX_DELAY, delay))
    return action, delay


def _require_trusted_file(metadata, what):
    """属主为可信 uid、普通文件、非 group/other 可写。"""
    if not stat.S_ISREG(metadata.st_mode):
        _fail("%s must be a regular file" % what)
    if metadata.st_uid != USB_POWER_TRUSTED_UID:
        _fail("%s must be owned by uid %d" % (what, USB_POWER_TRUSTED_UID))
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail("%s must not be group/other writable" % what)


def parse_usb_power_slots(body):
    """解析槽位映射（纯函数）：返回 {serial: (hub_location, port)}。

    任一条目非法、serial 重复或 (hub_location, port) 重复时整份拒绝
    （ADR-0063 D1 第 4 条）——宁可全部切电不可用，也不按一份有歧义的表断电。
    """
    try:
        doc = json.loads(body)
    except ValueError as exc:
        _fail("usb power slots file is not valid JSON: %s" % exc)
    if not isinstance(doc, dict) or set(doc) != {"schema", "slots"} \
            or type(doc.get("schema")) is not int or doc["schema"] != 1:
        _fail("usb power slots file must be an object with exactly schema=1 and slots")
    slots = doc.get("slots")
    if not isinstance(slots, list):
        _fail("usb power slots file must have a slots list")
    mapping = {}
    seen_slots = set()
    for index, entry in enumerate(slots):
        if not isinstance(entry, dict) or set(entry) != {"serial", "hub_location", "port"}:
            _fail("slot #%d must have exactly serial/hub_location/port" % index)
        serial = entry["serial"]
        location = entry["hub_location"]
        port = entry["port"]
        if not isinstance(serial, str) or not _USB_POWER_SERIAL_RE.match(serial):
            _fail("slot #%d has an invalid serial" % index)
        if not isinstance(location, str) or not _USB_PORT_RE.match(location):
            _fail("slot #%d has an invalid hub_location" % index)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= USB_POWER_MAX_PORT:
            _fail("slot #%d port must be an integer within 1..%d" % (index, USB_POWER_MAX_PORT))
        if serial in mapping:
            _fail("serial %s appears more than once in usb power slots file" % serial)
        if (location, port) in seen_slots:
            _fail("slot %s port %d appears more than once in usb power slots file" % (location, port))
        mapping[serial] = (location, port)
        seen_slots.add((location, port))
    return mapping


def _load_usb_power_slots():
    """读 root 属主映射文件：父目录与文件都必须可信，文件以 O_NOFOLLOW 打开。"""
    directory = os.path.dirname(USB_POWER_SLOTS_PATH)
    try:
        dir_meta = os.lstat(directory)
    except OSError as exc:
        _fail("cannot stat usb power slots dir %s: %s" % (directory, exc))
    if not stat.S_ISDIR(dir_meta.st_mode):
        _fail("usb power slots dir must be a real directory")
    if dir_meta.st_uid != USB_POWER_TRUSTED_UID or dir_meta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail("usb power slots dir must be owned by uid %d and not group/other writable"
              % USB_POWER_TRUSTED_UID)
    try:
        descriptor = os.open(USB_POWER_SLOTS_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        _fail("cannot open usb power slots file %s: %s" % (USB_POWER_SLOTS_PATH, exc))
    with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
        metadata = os.fstat(handle.fileno())
        _require_trusted_file(metadata, "usb power slots file")
        if metadata.st_size > MAX_USB_POWER_SLOTS_BYTES:
            _fail("usb power slots file too large")
        body = handle.read(MAX_USB_POWER_SLOTS_BYTES + 1)
    if len(body.encode("utf-8")) > MAX_USB_POWER_SLOTS_BYTES:
        _fail("usb power slots file too large")
    return parse_usb_power_slots(body)


def build_usb_power_argv(location, port, action, delay):
    """固定 argv（纯函数，单测锁定白名单面）：uhubctl -l -p -a [-d]。"""
    argv = [UHUBCTL_BIN, "-l", location, "-p", str(port), "-a", action]
    if delay is not None:
        argv += ["-d", str(delay)]
    return argv


def cmd_usb_power(args, conf):
    """按 serial 给登记槽位切电（#3892 / ADR-0063 D2 / ADR-0037 D8）。

    面约束：调用方只给 serial / expect-slot / action / delay；槽位只来自 root 属主映射文件，
    expect-slot 只用于比对（不一致即 exit 2，不调 uhubctl）；
    Hub 位置必须解析为 ``/sys/devices`` 下同名的真实目录（与 usb-authorized 同判定）；
    uhubctl 本体必须 root 属主且非 group/other 可写；子进程环境清空、60s 超时、
    不经 shell。失败一律 exit 3 + ``STP_USB_POWER_*`` 标记。
    """
    _require_root()
    serial = validate_usb_power_serial(args.serial)
    expected = validate_usb_power_expect_slot(args.expect_slot)
    action, delay = validate_usb_power_request(args.action, args.delay)
    slots = _load_usb_power_slots()
    if serial not in slots:
        _fail("serial %s has no slot in %s" % (serial, USB_POWER_SLOTS_PATH))
    location, port = slots[serial]
    if (location, port) != expected:
        # 在任何切电动作之前拒绝：调用方与 wrapper 读到的映射不同（两次读之间被改写）。
        _fail("STP_USB_POWER_SLOT_MISMATCH serial=%s expected=%s:%d mapped=%s:%d"
              % (serial, expected[0], expected[1], location, port))
    resolved = os.path.realpath(os.path.join(USB_SYSFS_BASE, location))
    if not is_kernel_usb_device_path(resolved, location) or not os.path.isdir(resolved):
        _fail("hub_location %s does not resolve to a kernel sysfs USB device (got %s)"
              % (location, resolved))
    try:
        binary_meta = os.lstat(UHUBCTL_BIN)
    except OSError as exc:
        _fail("cannot stat %s: %s" % (UHUBCTL_BIN, exc))
    _require_trusted_file(binary_meta, UHUBCTL_BIN)
    argv = build_usb_power_argv(location, port, action, delay)
    try:
        proc = subprocess.run(
            argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=dict(USB_POWER_ENV), close_fds=True,
            timeout=USB_POWER_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        print("STP_USB_POWER_TIMEOUT seconds=%d" % USB_POWER_TIMEOUT_SECONDS, file=sys.stderr)
        return 3
    except OSError as exc:
        print("STP_USB_POWER_FAILED cannot exec %s: %s" % (UHUBCTL_BIN, exc), file=sys.stderr)
        return 3
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip()[-400:]
        print("STP_USB_POWER_FAILED rc=%d %s" % (proc.returncode, tail), file=sys.stderr)
        return 3
    print("STP_USB_POWER_OK serial=%s location=%s port=%d action=%s"
          % (serial, location, port, action))
    return 0


class _XhciRebindTimeout(Exception):
    """单次 xhci-rebind 超过总超时上限（含内核同步 probe 阻塞）。"""


def is_kernel_pci_device_path(resolved, bdf):
    """校验 BDF 符号链接的 realpath 位于 ``_SYSFS_DEVICES_ROOT`` 下且末段等于该 BDF。"""
    if not _PCI_BDF_RE.match(bdf):
        return False
    root = os.path.realpath(_SYSFS_DEVICES_ROOT).rstrip("/")
    prefix = root + "/"
    return (
        resolved.startswith(prefix)
        and os.path.basename(resolved) == bdf
        and os.path.isdir(resolved)
    )


def _write_sysfs_attr_at(directory_fd, name, payload):
    """以 O_WRONLY | O_NOFOLLOW 打开驱动目录下普通属性文件并写入单次 payload。"""
    try:
        target = os.open(
            name, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        # ELOOP 表示目标属性文件被替换成符号链接，属安全边界拒绝（exit 2）。
        if exc.errno == errno.ELOOP:
            _fail("sysfs attribute %s must not be a symlink: %s" % (name, exc))
        raise
    try:
        if not stat.S_ISREG(os.fstat(target).st_mode):
            _fail("sysfs attribute %s must be a regular file" % name)
        os.write(target, payload.encode("ascii"))
    finally:
        os.close(target)


def _enumerate_xhci_bdfs(driver_fd, base_path):
    """枚举 ``xhci_hcd`` 驱动目录下已绑定 BDF 符号链接并校验 realpath 落于 /sys/devices/。"""
    targets = []
    for name in sorted(os.listdir(driver_fd)):
        if not _PCI_BDF_RE.match(name):
            continue
        try:
            meta = os.lstat(name, dir_fd=driver_fd)
        except OSError as exc:
            _fail("cannot lstat xhci entry %s: %s" % (name, exc))
        if not stat.S_ISLNK(meta.st_mode):
            _fail("xhci BDF entry %s must be a kernel symlink" % name)
        resolved = os.path.realpath(os.path.join(base_path, name))
        if not is_kernel_pci_device_path(resolved, name):
            _fail(
                "xhci BDF %s does not resolve under %s (got %s)"
                % (name, _SYSFS_DEVICES_ROOT, resolved)
            )
        targets.append((name, resolved))
    return targets


def _verify_xhci_rebound(driver_fd, bdf, real_pci_dir):
    """核验重绑后驱动目录重现 BDF 链接且已校验真实 PCI 目录下 enable != 0（§6.1 判据）。"""
    try:
        meta = os.lstat(bdf, dir_fd=driver_fd)
    except OSError as exc:
        return False, "driver_link_missing:%s" % exc
    if not stat.S_ISLNK(meta.st_mode):
        return False, "driver_entry_not_symlink"
    try:
        pci_fd = _open_directory(real_pci_dir)
    except (PrivError, OSError) as exc:
        return False, "pci_dir_open_failed:%s" % exc
    try:
        enable_text = _read_sysfs_attr(pci_fd, "enable", 32).strip()
    except (PrivError, OSError) as exc:
        return False, "enable_read_failed:%s" % exc
    finally:
        os.close(pci_fd)
    if enable_text == "0":
        return False, "enable_is_0"
    return True, "ok"


@contextmanager
def _xhci_alarm_budget(timeout_seconds):
    """用 SIGALRM 兜住同步内核 unbind/bind probe 阻塞，避免总超时失效。"""
    if timeout_seconds <= 0:
        raise _XhciRebindTimeout("timeout budget exhausted")
    use_alarm = hasattr(signal, "SIGALRM") and hasattr(signal, "setitimer")
    prev_handler = None
    if use_alarm:
        def _on_alarm(signum, frame):
            raise _XhciRebindTimeout("xhci-rebind exceeded %.1fs" % timeout_seconds)
        try:
            prev_handler = signal.signal(signal.SIGALRM, _on_alarm)
            signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
        except ValueError:
            # 非主线程测试场景不支持 signal.signal，降级靠 monotonic deadline 判定。
            use_alarm = False
    try:
        yield
    finally:
        if use_alarm:
            signal.setitimer(signal.ITIMER_REAL, 0.0)
            signal.signal(signal.SIGALRM, prev_handler)


def cmd_xhci_rebind(args, conf):
    """无目标参数 xHCI 主控 unbind→bind 恢复通道（#2972 / ADR-0037 D9）。

    - exit 0：至少 1 个有效 BDF，全部 unbind/bind 完成且驱动链接重现、enable != 0；
    - exit 2：非 root、固定驱动目录或属性文件 O_NOFOLLOW / realpath 校验失败；
    - exit 3：无有效 BDF / 写入失败 / 部分成功 / 总超时 / probe 失败（§6.1 -110 态），
      stderr 输出 ``STP_XHCI_REBIND_*`` 标记与逐 BDF 结构化摘要。
    """
    _require_root()
    try:
        driver_fd = _open_directory(XHCI_SYSFS_BASE)
    except OSError as exc:
        _fail("cannot open xhci driver dir %s: %s" % (XHCI_SYSFS_BASE, exc))
    try:
        # 校验驱动目录下 unbind/bind 均存在且非 symlink（在任何写动作之前）
        for attr in ("unbind", "bind"):
            try:
                attr_meta = os.lstat(attr, dir_fd=driver_fd)
            except OSError as exc:
                _fail("missing xhci driver attribute %s: %s" % (attr, exc))
            if not stat.S_ISREG(attr_meta.st_mode):
                _fail("xhci driver attribute %s must be a regular file" % attr)

        targets = _enumerate_xhci_bdfs(driver_fd, XHCI_SYSFS_BASE)
        if not targets:
            print(
                "%s summary=%s" % (XHCI_MARKER_NO_PCI_IDS, json.dumps([], separators=(",", ":"))),
                file=sys.stderr,
            )
            return 3

        deadline = time.monotonic() + float(XHCI_REBIND_TIMEOUT_SECONDS)
        results = []
        timed_out = False

        try:
            with _xhci_alarm_budget(float(XHCI_REBIND_TIMEOUT_SECONDS)):
                for idx, (bdf, real_pci_dir) in enumerate(targets):
                    if time.monotonic() >= deadline:
                        timed_out = True
                        for rem_bdf, _ in targets[idx:]:
                            results.append({"bdf": rem_bdf, "status": "timeout", "detail": "budget_exhausted"})
                        break
                    try:
                        _write_sysfs_attr_at(driver_fd, "unbind", bdf)
                    except OSError as exc:
                        results.append({"bdf": bdf, "status": "write_failed", "detail": "unbind:%s" % exc})
                        continue

                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        results.append({"bdf": bdf, "status": "timeout", "detail": "after_unbind"})
                        for rem_bdf, _ in targets[idx + 1:]:
                            results.append({"bdf": rem_bdf, "status": "timeout", "detail": "budget_exhausted"})
                        break
                    if XHCI_REBIND_DELAY_SECONDS > 0:
                        sleep_s = min(float(XHCI_REBIND_DELAY_SECONDS), remaining)
                        time.sleep(sleep_s)
                        if time.monotonic() >= deadline:
                            timed_out = True
                            results.append({"bdf": bdf, "status": "timeout", "detail": "during_delay"})
                            for rem_bdf, _ in targets[idx + 1:]:
                                results.append({"bdf": rem_bdf, "status": "timeout", "detail": "budget_exhausted"})
                            break

                    try:
                        _write_sysfs_attr_at(driver_fd, "bind", bdf)
                    except OSError as exc:
                        # bind 报错时检查是否落入 §6.1 probe 失败态（无链接或 enable==0）
                        ok_after, detail_after = _verify_xhci_rebound(driver_fd, bdf, real_pci_dir)
                        if not ok_after and (
                            detail_after == "enable_is_0"
                            or "driver_link_missing" in detail_after
                        ):
                            results.append({
                                "bdf": bdf,
                                "status": "probe_failed",
                                "detail": "bind:%s;%s" % (exc, detail_after),
                            })
                        else:
                            results.append({"bdf": bdf, "status": "write_failed", "detail": "bind:%s" % exc})
                        continue

                    ok_after, detail_after = _verify_xhci_rebound(driver_fd, bdf, real_pci_dir)
                    if not ok_after:
                        results.append({"bdf": bdf, "status": "probe_failed", "detail": detail_after})
                    else:
                        results.append({"bdf": bdf, "status": "ok", "detail": "ok"})
        except _XhciRebindTimeout as exc:
            timed_out = True
            seen_bdfs = {item["bdf"] for item in results}
            for rem_bdf, _ in targets:
                if rem_bdf not in seen_bdfs:
                    results.append({"bdf": rem_bdf, "status": "timeout", "detail": str(exc)})
    finally:
        os.close(driver_fd)

    statuses = [item["status"] for item in results]
    if results and all(st == "ok" for st in statuses):
        bdfs = ",".join(item["bdf"] for item in results)
        print("STP_XHCI_REBIND_OK count=%d bdfs=%s" % (len(results), bdfs))
        return 0

    if timed_out or "timeout" in statuses:
        marker = XHCI_MARKER_TIMEOUT
    elif "probe_failed" in statuses:
        marker = XHCI_MARKER_PROBE_FAILED
    elif "ok" in statuses:
        marker = XHCI_MARKER_PARTIAL
    else:
        marker = XHCI_MARKER_WRITE_FAILED

    print(
        "%s summary=%s" % (marker, json.dumps(results, separators=(",", ":"))),
        file=sys.stderr,
    )
    return 3


# ---------------------------------------------------------------------------
# usbnet-* 受限通道（#4212 / ADR-0037 D10 / ADR-0066 D6）
# ---------------------------------------------------------------------------


class UsbnetDisabledError(Exception):
    """主机 /etc/stp-usbnet.conf 中 usbnet_enabled != true（exit 2 + STP_USBNET_DISABLED）。"""


class _UsbnetCmdError(RuntimeError):
    """usbnet 外部系统命令执行失败或超时。"""


# ── 纯 Python RFC 8032 Ed25519 验签（零三方依赖，仅用 stdlib hashlib.sha512）──

_ED25519_P = (1 << 255) - 19
_ED25519_L = (1 << 252) + 27742317777372353535851937790883648493
_ED25519_D = (-121665 * pow(121666, _ED25519_P - 2, _ED25519_P)) % _ED25519_P
_ED25519_I = pow(2, (_ED25519_P - 1) // 4, _ED25519_P)
_ED25519_SPKI_PREFIX = b"\x30\x2a\x30\x05\x06\x03\x2b\x65\x70\x03\x21\x00"


def _ed25519_recover_x(y, sign):
    if y >= _ED25519_P:
        return None
    y2 = (y * y) % _ED25519_P
    u = (y2 - 1) % _ED25519_P
    v = (_ED25519_D * y2 + 1) % _ED25519_P
    v3 = (v * v * v) % _ED25519_P
    v7 = (v3 * v3 * v) % _ED25519_P
    x = (u * v3 * pow((u * v7) % _ED25519_P, (_ED25519_P - 5) // 8, _ED25519_P)) % _ED25519_P
    vx2 = (v * x * x) % _ED25519_P
    if vx2 == u:
        pass
    elif vx2 == (-u) % _ED25519_P:
        x = (x * _ED25519_I) % _ED25519_P
    else:
        return None
    if x == 0 and sign:
        return None
    if (x & 1) != sign:
        x = _ED25519_P - x
    return x


def _ed25519_point_decode(encoded):
    if not isinstance(encoded, (bytes, bytearray)) or len(encoded) != 32:
        return None
    val = int.from_bytes(encoded, "little")
    y = val & ((1 << 255) - 1)
    sign = (val >> 255) & 1
    x = _ed25519_recover_x(y, sign)
    if x is None:
        return None
    return (x, y, 1, (x * y) % _ED25519_P)


def _ed25519_point_add(p1, p2):
    x1, y1, z1, t1 = p1
    x2, y2, z2, t2 = p2
    a = ((y1 - x1) * (y2 - x2)) % _ED25519_P
    b = ((y1 + x1) * (y2 + x2)) % _ED25519_P
    c = (t1 * 2 * _ED25519_D * t2) % _ED25519_P
    d = (z1 * 2 * z2) % _ED25519_P
    e = (b - a) % _ED25519_P
    f = (d - c) % _ED25519_P
    g = (d + c) % _ED25519_P
    h = (b + a) % _ED25519_P
    return (
        (e * f) % _ED25519_P,
        (g * h) % _ED25519_P,
        (f * g) % _ED25519_P,
        (e * h) % _ED25519_P,
    )


def _ed25519_scalar_mult(point, scalar):
    acc = (0, 1, 1, 0)
    cur = point
    k = scalar
    while k > 0:
        if k & 1:
            acc = _ed25519_point_add(acc, cur)
        cur = _ed25519_point_add(cur, cur)
        k >>= 1
    return acc


def _ed25519_point_equal(p1, p2):
    return (
        (p1[0] * p2[2] - p2[0] * p1[2]) % _ED25519_P == 0
        and (p1[1] * p2[2] - p2[1] * p1[2]) % _ED25519_P == 0
    )


_ED25519_BY = (4 * pow(5, _ED25519_P - 2, _ED25519_P)) % _ED25519_P
_ED25519_BX = _ed25519_recover_x(_ED25519_BY, 0)
_ED25519_BASE = (_ED25519_BX, _ED25519_BY, 1, (_ED25519_BX * _ED25519_BY) % _ED25519_P)


def ed25519_verify(pubkey_bytes, message, signature):
    """RFC 8032 Ed25519 验签（纯 Python stdlib 实现）。"""
    if not isinstance(pubkey_bytes, (bytes, bytearray)) or len(pubkey_bytes) != 32:
        return False
    if not isinstance(signature, (bytes, bytearray)) or len(signature) != 64:
        return False
    if not isinstance(message, (bytes, bytearray)):
        return False
    a_point = _ed25519_point_decode(pubkey_bytes)
    if a_point is None:
        return False
    r_bytes = bytes(signature[:32])
    r_point = _ed25519_point_decode(r_bytes)
    if r_point is None:
        return False
    s_scalar = int.from_bytes(signature[32:], "little")
    if s_scalar >= _ED25519_L:
        return False
    h_digest = hashlib.sha512(r_bytes + bytes(pubkey_bytes) + bytes(message)).digest()
    h_scalar = int.from_bytes(h_digest, "little") % _ED25519_L
    lhs = _ed25519_scalar_mult(_ED25519_BASE, s_scalar)
    rhs = _ed25519_point_add(r_point, _ed25519_scalar_mult(a_point, h_scalar))
    return _ed25519_point_equal(lhs, rhs)


def _b64url_decode_nopad(segment, label):
    if not isinstance(segment, str) or not segment or not _USBNET_B64URL_SEG_RE.match(segment):
        _fail("%s is not valid base64url" % label)
    pad = "=" * ((4 - len(segment) % 4) % 4)
    try:
        return base64.urlsafe_b64decode(segment + pad)
    except Exception as exc:
        _fail("%s base64url decode failed: %s" % (label, exc))


def parse_ed25519_pubkey(raw):
    """解析控制面 Ed25519 公钥（支持 SPKI PEM 与 32 字节 raw base64url/base64）。"""
    if not isinstance(raw, str) or not raw.strip():
        _fail("control_plane_verify_pubkey must be a non-empty string")
    text = raw.strip()
    key_bytes = None
    if text.startswith("-----BEGIN PUBLIC KEY-----"):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        if len(lines) < 3 or lines[0] != "-----BEGIN PUBLIC KEY-----" or lines[-1] != "-----END PUBLIC KEY-----":
            _fail("control_plane_verify_pubkey has malformed PEM envelope")
        try:
            der = base64.b64decode("".join(lines[1:-1]), validate=True)
        except Exception as exc:
            _fail("control_plane_verify_pubkey PEM base64 invalid: %s" % exc)
        if len(der) != 44 or not der.startswith(_ED25519_SPKI_PREFIX):
            _fail("control_plane_verify_pubkey PEM is not a valid Ed25519 SPKI key")
        key_bytes = der[12:]
    else:
        body = text[len("ed25519:"):] if text.startswith("ed25519:") else text
        pad = "=" * ((4 - len(body) % 4) % 4)
        for decoder in (base64.urlsafe_b64decode, base64.b64decode):
            try:
                cand = decoder(body + pad)
            except Exception:
                continue
            if len(cand) == 32:
                key_bytes = cand
                break
        if key_bytes is None:
            _fail("control_plane_verify_pubkey must be Ed25519 SPKI PEM or 32-byte base64url/base64")
    if _ed25519_point_decode(key_bytes) is None:
        _fail("control_plane_verify_pubkey is not a valid Ed25519 curve point")
    return key_bytes


# ── /etc/stp-usbnet.conf 校验与加载（ADR-0037 D10.2）────────────────────────


def _validate_ipv4_unicast(value, label):
    if not isinstance(value, str) or not value.strip():
        _fail("%s must be a non-empty IPv4 string" % label)
    try:
        addr = ipaddress.IPv4Address(value.strip())
    except ValueError as exc:
        _fail("%s is not a valid IPv4 address: %s" % (label, exc))
    if addr.is_unspecified or addr.is_loopback or addr.is_multicast or addr.is_link_local:
        _fail("%s must be a routable IPv4 unicast address, got %s" % (label, addr))
    return str(addr)


def _validate_usbnet_profile(egress, profile):
    if not isinstance(profile, dict):
        _fail("egress_profiles[%s] must be an object" % egress)
    unknown = set(profile) - USBNET_PROFILE_KEYS
    if unknown:
        _fail("egress_profiles[%s] has unknown keys: %s" % (egress, ",".join(sorted(unknown))))
    bridge = profile.get("bridge")
    if not isinstance(bridge, str) or not _USBNET_BRIDGE_RE.match(bridge):
        _fail("egress_profiles[%s].bridge must match ^br-stpusb-(dir|vpn)$" % egress)
    expected_bridge = USBNET_EGRESS_BRIDGE_NAME.get(egress)
    if bridge != expected_bridge:
        _fail("egress_profiles[%s].bridge must be %s, got %r" % (egress, expected_bridge, bridge))
    cidr_raw = profile.get("subnet_cidr")
    if not isinstance(cidr_raw, str):
        _fail("egress_profiles[%s].subnet_cidr must be a string" % egress)
    try:
        net = ipaddress.IPv4Network(cidr_raw, strict=True)
    except ValueError as exc:
        _fail("egress_profiles[%s].subnet_cidr invalid: %s" % (egress, exc))
    if not net.is_private or net.is_loopback or net.is_link_local or net.is_multicast:
        _fail("egress_profiles[%s].subnet_cidr must be a private IPv4 subnet" % egress)
    if net.prefixlen > 29 or net.prefixlen < 16:
        _fail("egress_profiles[%s].subnet_cidr prefixlen must be in 16..29" % egress)
    dns_list = profile.get("upstream_dns")
    if not isinstance(dns_list, list) or not (1 <= len(dns_list) <= 8):
        _fail("egress_profiles[%s].upstream_dns must be a list of 1..8 IPv4 addresses" % egress)
    validated_dns = []
    for idx, dns_item in enumerate(dns_list):
        ip_str = _validate_ipv4_unicast(dns_item, "egress_profiles[%s].upstream_dns[%d]" % (egress, idx))
        if ip_str not in validated_dns:
            validated_dns.append(ip_str)
    uplink = profile.get("uplink_iface")
    if uplink is not None:
        if (
            not isinstance(uplink, str)
            or not _USBNET_IFACE_RE.match(uplink)
            or uplink in ("lo", "docker0")
            or uplink.startswith("br-")
        ):
            _fail("egress_profiles[%s].uplink_iface invalid: %r" % (egress, uplink))
    local_dns_forward = profile.get("local_dns_forward", False)
    if type(local_dns_forward) is not bool:
        _fail("egress_profiles[%s].local_dns_forward must be a boolean" % egress)
    vpn_gateways_raw = profile.get("vpn_gateway_ips", [])
    if not isinstance(vpn_gateways_raw, list) or len(vpn_gateways_raw) > 8:
        _fail("egress_profiles[%s].vpn_gateway_ips must be a list of <= 8 IPv4 addresses" % egress)
    vpn_gateways = [
        _validate_ipv4_unicast(item, "egress_profiles[%s].vpn_gateway_ips[%d]" % (egress, idx))
        for idx, item in enumerate(vpn_gateways_raw)
    ]
    return {
        "bridge": bridge,
        "subnet_cidr": str(net),
        "network": net,
        "gateway_ip": str(net.network_address + 1),
        "dhcp_start": str(net.network_address + 2),
        "dhcp_end": str(net.broadcast_address - 1),
        "netmask": str(net.netmask),
        "upstream_dns": validated_dns,
        "uplink_iface": uplink,
        "local_dns_forward": local_dns_forward,
        "vpn_gateway_ips": vpn_gateways,
    }


def parse_usbnet_conf(body):
    """解析并严格校验 /etc/stp-usbnet.conf（纯函数，ADR-0037 D10.2）。

    usbnet_enabled != true 时抛出 UsbnetDisabledError（exit 2 + STP_USBNET_DISABLED）；
    JSON 非法或 schema 校验失败时抛出 PrivError（exit 2 + STP_AGENT_PRIV_ERROR）。
    """
    try:
        doc = json.loads(body)
    except ValueError as exc:
        _fail("usbnet config is not valid JSON: %s" % exc)
    if not isinstance(doc, dict):
        _fail("usbnet config must be a JSON object")
    unknown = set(doc) - USBNET_CONF_KEYS
    if unknown:
        _fail("usbnet config has unknown keys: %s" % ",".join(sorted(unknown)))

    enabled = doc.get("usbnet_enabled", False)
    if type(enabled) is not bool:
        _fail("usbnet_enabled must be a boolean")

    max_devices = doc.get("usbnet_max_devices", USBNET_DEFAULT_MAX_DEVICES)
    if type(max_devices) is not int or not (USBNET_MIN_MAX_DEVICES <= max_devices <= USBNET_MAX_MAX_DEVICES):
        _fail(
            "usbnet_max_devices must be an integer within %d..%d"
            % (USBNET_MIN_MAX_DEVICES, USBNET_MAX_MAX_DEVICES)
        )

    # 先校验基本字段形态，避免损坏配置被误报为「正常禁用态」。
    if "allowed_usb_ports" in doc and not isinstance(doc["allowed_usb_ports"], list):
        _fail("allowed_usb_ports must be a list")
    if "allowed_egresses" in doc and not isinstance(doc["allowed_egresses"], list):
        _fail("allowed_egresses must be a list")
    if "egress_profiles" in doc and not isinstance(doc["egress_profiles"], dict):
        _fail("egress_profiles must be an object")

    if not enabled:
        raise UsbnetDisabledError("usbnet_enabled is false")

    host_id = doc.get("host_id")
    if not isinstance(host_id, str) or not _USBNET_ID_RE.match(host_id):
        _fail("host_id must match [A-Za-z0-9._:-]{1,128}")

    pubkey_bytes = parse_ed25519_pubkey(doc.get("control_plane_verify_pubkey"))

    ports_raw = doc.get("allowed_usb_ports")
    if not isinstance(ports_raw, list) or not ports_raw:
        _fail("allowed_usb_ports must be a non-empty list when usbnet_enabled is true")
    allowed_ports = []
    seen_ports = set()
    for port in ports_raw:
        if not isinstance(port, str) or not _USB_PORT_RE.match(port):
            _fail("allowed_usb_ports contains invalid USB port: %r" % (port,))
        if port in seen_ports:
            _fail("allowed_usb_ports contains duplicate port: %s" % port)
        seen_ports.add(port)
        allowed_ports.append(port)

    egresses_raw = doc.get("allowed_egresses")
    if not isinstance(egresses_raw, list) or not egresses_raw:
        _fail("allowed_egresses must be a non-empty list when usbnet_enabled is true")
    allowed_egresses = []
    seen_egresses = set()
    for egress in egresses_raw:
        if not isinstance(egress, str) or egress not in USBNET_ALLOWED_EGRESS_CHOICES:
            _fail("allowed_egresses contains unsupported egress: %r" % (egress,))
        if egress in seen_egresses:
            _fail("allowed_egresses contains duplicate egress: %s" % egress)
        seen_egresses.add(egress)
        allowed_egresses.append(egress)

    profiles_raw = doc.get("egress_profiles")
    if not isinstance(profiles_raw, dict):
        _fail("egress_profiles must be an object")
    profiles = {}
    for egress, profile_doc in profiles_raw.items():
        if egress not in USBNET_ALLOWED_EGRESS_CHOICES:
            _fail("egress_profiles contains unsupported egress key: %r" % (egress,))
        profiles[egress] = _validate_usbnet_profile(egress, profile_doc)
    for egress in allowed_egresses:
        if egress not in profiles:
            _fail("egress_profiles missing definition for allowed egress %r" % (egress,))

    return {
        "usbnet_enabled": True,
        "host_id": host_id,
        "control_plane_verify_pubkey_bytes": pubkey_bytes,
        "allowed_usb_ports": allowed_ports,
        "allowed_egresses": allowed_egresses,
        "egress_profiles": profiles,
        "usbnet_max_devices": max_devices,
    }


def _load_usbnet_conf():
    """读取并校验 root:root 属主的 /etc/stp-usbnet.conf（D10.2）。"""
    parent_dir = os.path.dirname(USBNET_CONF_PATH) or "/"
    try:
        parent_meta = os.lstat(parent_dir)
    except OSError as exc:
        _fail("cannot stat usbnet config parent dir %s: %s" % (parent_dir, exc))
    if not stat.S_ISDIR(parent_meta.st_mode):
        _fail("usbnet config parent dir must be a real directory: %s" % parent_dir)
    if parent_meta.st_uid != USBNET_TRUSTED_UID or parent_meta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail(
            "usbnet config parent dir must be owned by uid %d and not group/other writable: %s"
            % (USBNET_TRUSTED_UID, parent_dir)
        )
    try:
        fd = os.open(USBNET_CONF_PATH, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        _fail("cannot open usbnet config %s: %s" % (USBNET_CONF_PATH, exc))
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        meta = os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode):
            _fail("usbnet config must be a regular file: %s" % USBNET_CONF_PATH)
        if meta.st_uid != USBNET_TRUSTED_UID or meta.st_gid != USBNET_TRUSTED_GID:
            _fail(
                "usbnet config must be owned by %d:%d: %s"
                % (USBNET_TRUSTED_UID, USBNET_TRUSTED_GID, USBNET_CONF_PATH)
            )
        if meta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            _fail("usbnet config must not be group/other writable: %s" % USBNET_CONF_PATH)
        if meta.st_size > USBNET_MAX_CONF_BYTES:
            _fail("usbnet config exceeds %d bytes" % USBNET_MAX_CONF_BYTES)
        body = handle.read(USBNET_MAX_CONF_BYTES + 1)
    if len(body.encode("utf-8")) > USBNET_MAX_CONF_BYTES:
        _fail("usbnet config exceeds %d bytes" % USBNET_MAX_CONF_BYTES)
    return parse_usbnet_conf(body)


# ── /run/stp-agent-priv/usbnet-bindings.json 原子台账（ADR-0037 D10.3）──────


def _usbnet_binding_key(session_id, generation):
    return "%s#%d" % (session_id, generation)


def _empty_usbnet_ledger():
    return {
        "schema": 1,
        "active_bindings": {},
        "revocation_state": {
            "revoked_sessions": [],
            "min_allowed_generation_by_serial": {},
            "min_allowed_iat_by_egress": {},
        },
    }


def _validate_usbnet_ledger(doc):
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        _fail("usbnet ledger must be an object with schema=1")
    active = doc.get("active_bindings")
    rev = doc.get("revocation_state")
    if not isinstance(active, dict) or not isinstance(rev, dict):
        _fail("usbnet ledger missing active_bindings or revocation_state")
    seen_ports = set()
    seen_ifaces = set()
    norm_active = {}
    for key, entry in active.items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            _fail("usbnet ledger active_bindings entry malformed")
        sid = entry.get("session_id")
        gen = entry.get("generation")
        state = entry.get("state")
        egress = entry.get("egress")
        serial = entry.get("device_serial")
        iface = entry.get("iface")
        ifindex = entry.get("ifindex")
        devpath = entry.get("usb_devpath")
        mac = entry.get("mac")
        attached_at = entry.get("attached_at")
        if (
            not isinstance(sid, str) or not _USBNET_ID_RE.match(sid)
            or type(gen) is not int or gen < 1
            or key != _usbnet_binding_key(sid, gen)
            or state not in ("attaching", "attached")
            or egress not in USBNET_ALLOWED_EGRESS_CHOICES
            or not isinstance(serial, str) or not _USBNET_SERIAL_RE.match(serial)
            or not isinstance(iface, str) or not _USBNET_IFACE_RE.match(iface)
            or type(ifindex) is not int or ifindex < 1
            or not isinstance(devpath, str) or not _USB_PORT_RE.match(devpath)
            or not isinstance(mac, str) or not _USBNET_MAC_RE.match(mac)
            or type(attached_at) is not int or attached_at < 0
        ):
            _fail("usbnet ledger active_bindings[%s] has invalid fields" % key)
        if devpath in seen_ports:
            _fail("usbnet ledger duplicate usb_devpath %s in active_bindings" % devpath)
        if iface in seen_ifaces:
            _fail("usbnet ledger duplicate iface %s in active_bindings" % iface)
        seen_ports.add(devpath)
        seen_ifaces.add(iface)
        norm_active[key] = {
            "session_id": sid,
            "generation": gen,
            "state": state,
            "egress": egress,
            "device_serial": serial,
            "iface": iface,
            "ifindex": ifindex,
            "usb_devpath": devpath,
            "mac": mac.lower(),
            "attached_at": attached_at,
        }

    revoked_raw = rev.get("revoked_sessions")
    min_gen_raw = rev.get("min_allowed_generation_by_serial")
    min_iat_raw = rev.get("min_allowed_iat_by_egress")
    if (
        not isinstance(revoked_raw, list)
        or not isinstance(min_gen_raw, dict)
        or not isinstance(min_iat_raw, dict)
    ):
        _fail("usbnet ledger revocation_state malformed")
    norm_revoked = []
    for item in revoked_raw:
        if not isinstance(item, dict):
            _fail("usbnet ledger revoked_sessions entry malformed")
        sid = item.get("session_id")
        gen = item.get("generation")
        serial = item.get("device_serial", "")
        revoked_at = item.get("revoked_at")
        if (
            not isinstance(sid, str) or not _USBNET_ID_RE.match(sid)
            or type(gen) is not int or gen < 1
            or not isinstance(serial, str)
            or (serial != "" and not _USBNET_SERIAL_RE.match(serial))
            or type(revoked_at) is not int or revoked_at < 0
        ):
            _fail("usbnet ledger revoked_sessions entry has invalid fields")
        norm_revoked.append({
            "session_id": sid,
            "generation": gen,
            "device_serial": serial,
            "revoked_at": revoked_at,
        })
    norm_min_gen = {}
    for serial, val in min_gen_raw.items():
        if not isinstance(serial, str) or not _USBNET_SERIAL_RE.match(serial) or type(val) is not int or val < 0:
            _fail("usbnet ledger min_allowed_generation_by_serial invalid entry")
        norm_min_gen[serial] = val
    norm_min_iat = {}
    for egress, val in min_iat_raw.items():
        if egress not in USBNET_ALLOWED_EGRESS_CHOICES or type(val) is not int or val < 0:
            _fail("usbnet ledger min_allowed_iat_by_egress invalid entry")
        norm_min_iat[egress] = val
    return {
        "schema": 1,
        "active_bindings": norm_active,
        "revocation_state": {
            "revoked_sessions": norm_revoked,
            "min_allowed_generation_by_serial": norm_min_gen,
            "min_allowed_iat_by_egress": norm_min_iat,
        },
    }


def _gc_revoked_sessions(ledger, now):
    """D10.3 有界裁剪：仅当 gen < min_gen[serial] 或 now - revoked_at > 3600 才移除。"""
    rev = ledger["revocation_state"]
    min_gen = rev["min_allowed_generation_by_serial"]
    kept = []
    seen = set()
    for item in rev["revoked_sessions"]:
        pair = (item["session_id"], item["generation"])
        if pair in seen:
            continue
        serial = item.get("device_serial", "")
        floor_gen = min_gen.get(serial, 0) if serial else 0
        if item["generation"] < floor_gen:
            continue
        if (now - item["revoked_at"]) > USBNET_REVOKED_GC_AGE_SECONDS:
            continue
        seen.add(pair)
        kept.append(item)
    rev["revoked_sessions"] = kept


def _record_revocation(ledger, session_id, generation, device_serial, now, advance_watermark=True):
    rev = ledger["revocation_state"]
    updated = False
    for item in rev["revoked_sessions"]:
        if item["session_id"] == session_id and item["generation"] == generation:
            if device_serial and not item.get("device_serial"):
                item["device_serial"] = device_serial
            item["revoked_at"] = max(item["revoked_at"], now)
            updated = True
            break
    if not updated:
        rev["revoked_sessions"].append({
            "session_id": session_id,
            "generation": generation,
            "device_serial": device_serial or "",
            "revoked_at": now,
        })
    if advance_watermark and device_serial:
        cur = rev["min_allowed_generation_by_serial"].get(device_serial, 0)
        if generation > cur:
            rev["min_allowed_generation_by_serial"][device_serial] = generation


def _open_usbnet_run_dir(create=False):
    """以 O_NOFOLLOW 校验并打开 /run/stp-agent-priv（root:root, 0700, 非 symlink）。"""
    run_dir = USBNET_RUN_DIR
    if not create and not os.path.lexists(run_dir):
        return None
    parent_dir = os.path.dirname(run_dir) or "/"
    name = os.path.basename(run_dir)
    if not name:
        _fail("invalid USBNET_RUN_DIR: %s" % run_dir)
    try:
        parent_fd = _open_directory(parent_dir)
    except OSError as exc:
        _fail("cannot open usbnet run parent dir %s: %s" % (parent_dir, exc))
    try:
        pmeta = os.fstat(parent_fd)
        if pmeta.st_uid != USBNET_TRUSTED_UID or pmeta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
            _fail("usbnet run parent dir must be trusted and not group/other writable: %s" % parent_dir)
        created = False
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
            except OSError as exc:
                _fail("cannot create usbnet run dir %s: %s" % (run_dir, exc))
        try:
            dir_fd = os.open(
                name,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except OSError as exc:
            _fail("cannot open usbnet run dir %s (must be real 0700 dir): %s" % (run_dir, exc))
    finally:
        os.close(parent_fd)

    try:
        if created:
            os.fchmod(dir_fd, 0o700)
            if os.geteuid() == 0:
                os.fchown(dir_fd, USBNET_TRUSTED_UID, USBNET_TRUSTED_GID)
        meta = os.fstat(dir_fd)
        if not stat.S_ISDIR(meta.st_mode):
            _fail("usbnet run dir is not a directory: %s" % run_dir)
        if meta.st_uid != USBNET_TRUSTED_UID or meta.st_gid != USBNET_TRUSTED_GID:
            _fail("usbnet run dir must be owned by %d:%d" % (USBNET_TRUSTED_UID, USBNET_TRUSTED_GID))
        if (meta.st_mode & 0o777) != 0o700:
            _fail("usbnet run dir must have mode 0700, got %o" % (meta.st_mode & 0o777))
        return dir_fd
    except Exception:
        os.close(dir_fd)
        raise


def _read_usbnet_ledger_at(dir_fd):
    """在 dir_fd 下以 O_NOFOLLOW 读取并校验 usbnet-bindings.json（0600, root:root）。"""
    try:
        fd = os.open(
            USBNET_LEDGER_FILENAME,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=dir_fd,
        )
    except FileNotFoundError:
        return _empty_usbnet_ledger()
    except OSError as exc:
        _fail("cannot open usbnet ledger: %s" % exc)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        meta = os.fstat(handle.fileno())
        if not stat.S_ISREG(meta.st_mode):
            _fail("usbnet ledger must be a regular file")
        if meta.st_uid != USBNET_TRUSTED_UID or meta.st_gid != USBNET_TRUSTED_GID:
            _fail("usbnet ledger must be owned by %d:%d" % (USBNET_TRUSTED_UID, USBNET_TRUSTED_GID))
        if (meta.st_mode & 0o777) != 0o600:
            _fail("usbnet ledger must have mode 0600, got %o" % (meta.st_mode & 0o777))
        if meta.st_size > USBNET_MAX_LEDGER_BYTES:
            _fail("usbnet ledger exceeds size limit")
        body = handle.read(USBNET_MAX_LEDGER_BYTES + 1)
    if len(body.encode("utf-8")) > USBNET_MAX_LEDGER_BYTES:
        _fail("usbnet ledger exceeds size limit")
    try:
        doc = json.loads(body)
    except ValueError as exc:
        _fail("usbnet ledger is corrupted JSON: %s" % exc)
    return _validate_usbnet_ledger(doc)


def _write_usbnet_ledger_at(dir_fd, ledger):
    """在持有 flock 下以同目录临时文件 + fsync + os.replace 原子落盘台账（0600）。"""
    validated = _validate_usbnet_ledger(ledger)
    body = json.dumps(validated, sort_keys=True, indent=2) + "\n"
    temporary = ".usbnet-bindings-" + uuid.uuid4().hex
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
        0o600,
        dir_fd=dir_fd,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(body)
            handle.flush()
            os.fchmod(handle.fileno(), 0o600)
            if os.geteuid() == 0:
                os.fchown(handle.fileno(), USBNET_TRUSTED_UID, USBNET_TRUSTED_GID)
            os.fsync(handle.fileno())
        os.replace(temporary, USBNET_LEDGER_FILENAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(temporary, dir_fd=dir_fd)
        except OSError:
            pass
        raise


@contextmanager
def _locked_usbnet_ledger():
    """获取 /run/stp-agent-priv 目录句柄与 flock 排他锁，返回 (dir_fd, ledger)。"""
    dir_fd = _open_usbnet_run_dir(create=True)
    lock_fd = None
    try:
        try:
            lock_fd = os.open(
                USBNET_LOCK_FILENAME,
                os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                0o600,
                dir_fd=dir_fd,
            )
        except OSError as exc:
            _fail("cannot open usbnet ledger lock file: %s" % exc)
        lmeta = os.fstat(lock_fd)
        if not stat.S_ISREG(lmeta.st_mode):
            _fail("usbnet ledger lock must be a regular file")
        if lmeta.st_uid != USBNET_TRUSTED_UID or lmeta.st_gid != USBNET_TRUSTED_GID:
            _fail("usbnet ledger lock owner mismatch")
        if lmeta.st_mode & 0o077:
            os.fchmod(lock_fd, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        ledger = _read_usbnet_ledger_at(dir_fd)
        yield dir_fd, ledger
    finally:
        if lock_fd is not None:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(lock_fd)
        os.close(dir_fd)


# ── usbnet_grant 令牌解析与验签（ADR-0037 D10.4）────────────────────────────


def verify_usbnet_grant(token, conf, expect_devpath, now=None):
    """校验控制面签发的 Ed25519 usbnet_grant（格式：<b64url_json>.<b64url_sig>）。"""
    if not isinstance(token, str) or token.count(".") != 1 or len(token) > 8192:
        _fail("grant token format invalid")
    payload_seg, sig_seg = token.split(".", 1)
    sig_bytes = _b64url_decode_nopad(sig_seg, "grant signature")
    if len(sig_bytes) != 64:
        _fail("grant Ed25519 signature must be 64 bytes")
    if not ed25519_verify(
        conf["control_plane_verify_pubkey_bytes"],
        payload_seg.encode("ascii"),
        sig_bytes,
    ):
        _fail("grant Ed25519 signature verification failed")

    payload_bytes = _b64url_decode_nopad(payload_seg, "grant payload")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        _fail("grant payload is not valid JSON: %s" % exc)
    if not isinstance(payload, dict) or set(payload) != USBNET_GRANT_KEYS:
        _fail("grant payload keys invalid")

    sid = payload["session_id"]
    gen = payload["generation"]
    host_id = payload["host_id"]
    serial = payload["device_serial"]
    devpath = payload["usb_devpath"]
    egress = payload["egress"]
    iat = payload["iat"]
    exp = payload["exp"]

    if not isinstance(sid, str) or not _USBNET_ID_RE.match(sid):
        _fail("grant session_id invalid")
    if type(gen) is not int or gen < 1:
        _fail("grant generation must be an integer >= 1")
    if not isinstance(host_id, str) or host_id != conf["host_id"]:
        _fail("grant host_id mismatch")
    if not isinstance(serial, str) or not _USBNET_SERIAL_RE.match(serial):
        _fail("grant device_serial invalid")
    if not isinstance(devpath, str) or not _USB_PORT_RE.match(devpath):
        _fail("grant usb_devpath invalid")
    if egress not in conf["allowed_egresses"]:
        _fail("grant egress %r not in allowed_egresses" % (egress,))
    if type(iat) is not int or type(exp) is not int or iat <= 0 or exp <= 0:
        _fail("grant iat/exp must be positive integers")

    ttl = exp - iat
    if not (USBNET_GRANT_MIN_TTL_SECONDS <= ttl <= USBNET_GRANT_MAX_TTL_SECONDS):
        _fail(
            "grant lifetime (exp - iat = %d) must be within %d..%d seconds"
            % (ttl, USBNET_GRANT_MIN_TTL_SECONDS, USBNET_GRANT_MAX_TTL_SECONDS)
        )
    now_ts = int(time.time()) if now is None else int(now)
    if iat > now_ts + USBNET_GRANT_FUTURE_SKEW_SECONDS:
        _fail("grant iat is too far in the future (iat=%d now=%d)" % (iat, now_ts))
    if now_ts >= exp:
        _fail("grant has expired (exp=%d now=%d)" % (exp, now_ts))
    if devpath != expect_devpath:
        _fail("grant usb_devpath %s does not match --expect-devpath %s" % (devpath, expect_devpath))
    if devpath not in conf["allowed_usb_ports"]:
        _fail("grant usb_devpath %s is not in allowed_usb_ports" % devpath)
    return payload


def _check_grant_revocation_barriers(ledger, grant):
    """持锁检查三项撤销屏障（缺席键默认 0），命中返回原因字符串，未命中返回 None。"""
    rev = ledger["revocation_state"]
    sid = grant["session_id"]
    gen = grant["generation"]
    serial = grant["device_serial"]
    egress = grant["egress"]
    iat = grant["iat"]
    for item in rev["revoked_sessions"]:
        if item["session_id"] == sid and item["generation"] == gen:
            return "revoked_session(%s#%d)" % (sid, gen)
    min_gen = rev["min_allowed_generation_by_serial"].get(serial, 0)
    if gen < min_gen:
        return "generation_below_watermark(serial=%s gen=%d min=%d)" % (serial, gen, min_gen)
    min_iat = rev["min_allowed_iat_by_egress"].get(egress, 0)
    if iat <= min_iat:
        return "iat_below_egress_barrier(egress=%s iat=%d min=%d)" % (egress, iat, min_iat)
    return None


# ── 可执行 sysfs 网卡身份校验（ADR-0037 D10.4 / D10.5）────────────────────


def _host_default_route_ifaces():
    """读 /proc/net/route 获取宿主机默认路由上联网卡集合（拒绝被误挂桥）。"""
    ifaces = set()
    try:
        with open(USBNET_PROC_NET_ROUTE, "r", encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return ifaces
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        iface, dest, _gw, flags_hex = parts[0], parts[1], parts[2], parts[3]
        if dest == "00000000":
            try:
                flags = int(flags_hex, 16)
            except ValueError:
                flags = 0
            if flags & 0x1:  # RTF_UP
                ifaces.add(iface)
    return ifaces


def validate_usbnet_iface_name(iface):
    """D10.4 接口名静态校验（不含宿主保留/默认路由上联网卡）。"""
    if not isinstance(iface, str) or not _USBNET_IFACE_RE.match(iface):
        _fail("--iface must match ^[a-zA-Z0-9][a-zA-Z0-9._-]{0,14}$, got %r" % (iface,))
    if iface in ("lo", "docker0") or iface.startswith("br-"):
        _fail("--iface %r is a forbidden system/bridge interface" % (iface,))
    if iface in _host_default_route_ifaces():
        _fail("--iface %r is a host default-route uplink interface" % (iface,))
    return iface


def _read_net_master(net_real):
    """读取网卡当前挂入的网桥名（未挂桥返回 None）。"""
    master_path = os.path.join(net_real, "master")
    try:
        meta = os.lstat(master_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        _fail("cannot lstat master for %s: %s" % (net_real, exc))
    if not stat.S_ISLNK(meta.st_mode):
        _fail("unexpected non-symlink master entry under %s" % net_real)
    return os.path.basename(os.path.realpath(master_path))


def _read_brport_isolated(net_real):
    """经已校验 <net_real> 以 O_NOFOLLOW + S_ISREG 回读 brport/isolated。"""
    net_fd = _open_directory(net_real)
    try:
        brport_fd = os.open(
            "brport",
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=net_fd,
        )
        try:
            return _read_sysfs_attr(brport_fd, "isolated", 16).strip()
        finally:
            os.close(brport_fd)
    finally:
        os.close(net_fd)


def _read_net_ifindex(net_real):
    net_fd = _open_directory(net_real)
    try:
        text = _read_sysfs_attr(net_fd, "ifindex", 32).strip()
    finally:
        os.close(net_fd)
    if not text.isdigit() or int(text) < 1:
        _fail("invalid sysfs ifindex: %r" % (text,))
    return int(text)


def _inspect_usbnet_iface(iface, require_driver=True):
    """D10.4 可执行 sysfs 身份核验：仅对三处内核符号链接 realpath，叶子属性全 O_NOFOLLOW 读取。"""
    validate_usbnet_iface_name(iface)
    devices_root = os.path.realpath(_SYSFS_DEVICES_ROOT).rstrip("/")
    devices_prefix = devices_root + "/"
    virtual_root = os.path.join(devices_root, "virtual")

    # ① /sys/class/net/<iface> -> /sys/devices/.../net/<iface>（排除 /sys/devices/virtual/）
    net_link = os.path.join(NET_SYSFS_BASE, iface)
    if not os.path.lexists(net_link):
        _fail("network interface not found in sysfs: %s" % iface)
    net_real = os.path.realpath(net_link)
    if not net_real.startswith(devices_prefix) or is_within(net_real, virtual_root):
        _fail("interface %s does not resolve to a non-virtual /sys/devices path (got %s)" % (iface, net_real))
    if os.path.basename(net_real) != iface or os.path.basename(os.path.dirname(net_real)) != "net":
        _fail("interface %s sysfs path must end with /net/%s (got %s)" % (iface, iface, net_real))
    if os.path.lexists(os.path.join(net_real, "bridge")):
        _fail("interface %s is a bridge interface and cannot be attached" % iface)

    # ② <net_real>/device -> /sys/devices/.../usbN/.../<bus>-<port>/<bus>-<port>:<cfg>.<intf>
    dev_link = os.path.join(net_real, "device")
    if not os.path.lexists(dev_link):
        _fail("interface %s has no sysfs device link" % iface)
    usb_intf_real = os.path.realpath(dev_link)
    if not usb_intf_real.startswith(devices_prefix) or is_within(usb_intf_real, virtual_root):
        _fail("interface %s device does not resolve under /sys/devices (got %s)" % (iface, usb_intf_real))
    intf_match = _USBNET_INTF_RE.match(os.path.basename(usb_intf_real))
    if not intf_match:
        _fail("interface %s device is not a USB interface directory (got %s)" % (iface, usb_intf_real))
    intf_port = intf_match.group(1)
    usb_dev_real = os.path.dirname(usb_intf_real)
    usb_devpath = os.path.basename(usb_dev_real)
    if not _USB_PORT_RE.match(usb_devpath) or usb_devpath != intf_port:
        _fail("interface %s USB parent dir %s mismatches interface port %s" % (iface, usb_devpath, intf_port))
    if not is_kernel_usb_device_path(usb_dev_real, usb_devpath):
        _fail("interface %s USB device path invalid: %s" % (iface, usb_dev_real))
    rel_parts = usb_dev_real[len(devices_prefix):].split("/")
    if not any(_USBNET_USB_BUS_PART_RE.match(part) for part in rel_parts):
        _fail("interface %s is not under a USB bus path: %s" % (iface, usb_dev_real))

    # ③ <usb_intf_real>/driver -> /sys/bus/usb/drivers/{rndis_host,cdc_ncm}
    driver_name = None
    driver_link = os.path.join(usb_intf_real, "driver")
    if require_driver or os.path.lexists(driver_link):
        if not os.path.lexists(driver_link):
            _fail("interface %s USB interface has no bound driver" % iface)
        driver_real = os.path.realpath(driver_link)
        drivers_root = os.path.realpath(USB_DRIVERS_SYSFS_BASE).rstrip("/")
        driver_name = os.path.basename(driver_real)
        if os.path.dirname(driver_real) != drivers_root or driver_name not in USBNET_ALLOWED_DRIVERS:
            _fail(
                "interface %s driver must be in %s under %s (got %s)"
                % (iface, sorted(USBNET_ALLOWED_DRIVERS), drivers_root, driver_real)
            )

    # 打开真实目录并经 O_NOFOLLOW + S_ISREG 读取叶子属性
    net_fd = _open_directory(net_real)
    try:
        ifindex_text = _read_sysfs_attr(net_fd, "ifindex", 32).strip()
        mac_text = _read_sysfs_attr(net_fd, "address", 64).strip().lower()
    finally:
        os.close(net_fd)
    if not ifindex_text.isdigit() or int(ifindex_text) < 1:
        _fail("interface %s has invalid ifindex %r" % (iface, ifindex_text))
    if not _USBNET_MAC_RE.match(mac_text):
        _fail("interface %s has invalid MAC address %r" % (iface, mac_text))

    usb_dev_fd = _open_directory(usb_dev_real)
    try:
        serial_text = _read_sysfs_attr(usb_dev_fd, "serial", 128).strip()
        vendor_text = _read_sysfs_attr(usb_dev_fd, "idVendor", 32).strip().lower()
    finally:
        os.close(usb_dev_fd)
    if not _USBNET_SERIAL_RE.match(serial_text):
        _fail("interface %s USB device has invalid serial %r" % (iface, serial_text))
    if not _USBNET_VENDOR_RE.match(vendor_text):
        _fail("interface %s USB device has invalid idVendor %r" % (iface, vendor_text))

    return {
        "iface": iface,
        "net_real": net_real,
        "usb_intf_real": usb_intf_real,
        "usb_dev_real": usb_dev_real,
        "usb_devpath": usb_devpath,
        "driver": driver_name,
        "ifindex": int(ifindex_text),
        "mac": mac_text,
        "serial": serial_text,
        "idVendor": vendor_text,
        "master": _read_net_master(net_real),
    }


# ── 外部命令收口与三层默认拒绝隔离（ADR-0037 D10.6）────────────────────────


def _require_trusted_usbnet_binary(path):
    """校验外部工具为绝对路径、可信属主且非 group/other 可写（允许 Debian alternatives 链接）。"""
    if not isinstance(path, str) or not path.startswith("/"):
        _fail("external binary must be an absolute path: %r" % (path,))
    try:
        meta = os.stat(path)
    except OSError as exc:
        _fail("required binary missing: %s (%s)" % (path, exc))
    if not stat.S_ISREG(meta.st_mode):
        _fail("binary must resolve to a regular file: %s" % path)
    if meta.st_uid != USBNET_TRUSTED_UID:
        _fail("binary must be owned by uid %d: %s" % (USBNET_TRUSTED_UID, path))
    if meta.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        _fail("binary must not be group/other writable: %s" % path)


def _run_usbnet_cmd(argv, timeout=None, check=True):
    """执行固定绝对路径 + 固定 argv + 清空环境 + <= 30s 超时 + 不经 shell 的系统命令。"""
    _require_trusted_usbnet_binary(argv[0])
    limit = USBNET_CMD_TIMEOUT_SECONDS if timeout is None else min(timeout, USBNET_CMD_TIMEOUT_SECONDS)
    try:
        proc = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(USBNET_CMD_ENV),
            close_fds=True,
            timeout=limit,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        if check:
            raise _UsbnetCmdError("timeout(%ds): %s" % (limit, " ".join(argv))) from exc
        return 124, "", "timeout"
    except OSError as exc:
        if check:
            raise _UsbnetCmdError("exec failed %s: %s" % (argv[0], exc)) from exc
        return 127, "", str(exc)
    out = proc.stdout.decode("utf-8", "replace")
    err = proc.stderr.decode("utf-8", "replace")
    if check and proc.returncode != 0:
        raise _UsbnetCmdError(
            "rc=%d cmd=%s err=%s" % (proc.returncode, " ".join(argv), err.strip()[:240])
        )
    return proc.returncode, out, err


def _usbnet_chain_names(egress):
    suffix = USBNET_EGRESS_CHAIN_SUFFIX[egress]
    return (
        "STP_USBNET_IN_%s" % suffix,
        "STP_USBNET_FWD_%s" % suffix,
        "STP_USBNET_NAT_%s" % suffix,
    )


def _usbnet_dnsmasq_paths(egress):
    return (
        os.path.join(USBNET_RUN_DIR, "dnsmasq-%s.pid" % egress),
        os.path.join(USBNET_RUN_DIR, "dnsmasq-%s.leases" % egress),
    )


def _usbnet_dnsmasq_status(dir_fd, egress, bridge):
    """只读检查该出口 dnsmasq 是否存活且命令行匹配受控网桥（防 PID 复用误判）。"""
    pid_name = "dnsmasq-%s.pid" % egress
    pid_path, lease_path = _usbnet_dnsmasq_paths(egress)
    info = {
        "running": False,
        "pid": None,
        "pid_file": pid_path,
        "lease_file": lease_path,
    }
    if dir_fd is None:
        return info
    try:
        fd = os.open(pid_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=dir_fd)
    except FileNotFoundError:
        return info
    except OSError:
        return info
    try:
        with os.fdopen(fd, "r", encoding="utf-8") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                return info
            raw_pid = handle.read(32).strip()
    except OSError:
        return info
    if not raw_pid.isdigit():
        return info
    pid = int(raw_pid)
    if pid <= 1:
        return info
    cmdline_path = os.path.join(USBNET_PROC_ROOT, str(pid), "cmdline")
    try:
        with open(cmdline_path, "rb") as handle:
            parts = [p.decode("utf-8", "replace") for p in handle.read(4096).split(b"\x00") if p]
    except OSError:
        return info
    if not parts:
        return info
    exe_base = os.path.basename(parts[0])
    if exe_base != "dnsmasq":
        return info
    if ("--interface=%s" % bridge) not in parts:
        return info
    info["running"] = True
    info["pid"] = pid
    return info


def _iptables_chain_exists(table, chain):
    rc, _, _ = _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", table, "-L", chain, "-n"], check=False)
    return rc == 0


def _iptables_ensure_chain(table, chain):
    if not _iptables_chain_exists(table, chain):
        _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", table, "-N", chain])
    _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", table, "-F", chain])


def _iptables_ensure_jump(table, parent_chain, rule_args, target_chain):
    check_argv = [IPTABLES_BIN, "-w", "-t", table, "-C", parent_chain] + list(rule_args) + ["-j", target_chain]
    rc, _, _ = _run_usbnet_cmd(check_argv, check=False)
    if rc != 0:
        insert_argv = [IPTABLES_BIN, "-w", "-t", table, "-I", parent_chain, "1"] + list(rule_args) + [
            "-j", target_chain,
        ]
        _run_usbnet_cmd(insert_argv)


def _iptables_delete_jumps(table, parent_chain, rule_args, target_chain):
    check_argv = [IPTABLES_BIN, "-w", "-t", table, "-C", parent_chain] + list(rule_args) + ["-j", target_chain]
    del_argv = [IPTABLES_BIN, "-w", "-t", table, "-D", parent_chain] + list(rule_args) + ["-j", target_chain]
    for _ in range(16):
        rc, _, _ = _run_usbnet_cmd(check_argv, check=False)
        if rc != 0:
            break
        _run_usbnet_cmd(del_argv, check=False)


def _install_usbnet_firewall(egress, profile):
    """按 D10.6 在专属规则链安装 INPUT/FORWARD(DOCKER-USER)/NAT 三层默认拒绝隔离。"""
    bridge = profile["bridge"]
    subnet_cidr = profile["subnet_cidr"]
    gateway_ip = profile["gateway_ip"]
    upstream_dns = profile["upstream_dns"]
    uplink_iface = profile["uplink_iface"]
    local_dns_forward = profile["local_dns_forward"]
    vpn_gateways = profile["vpn_gateway_ips"]
    in_chain, fwd_chain, nat_chain = _usbnet_chain_names(egress)

    # ① INPUT 专属链：对源自受控网桥流量默认 DROP，仅放行 DHCP / ICMP echo-request / 可选本地 DNS
    _iptables_ensure_chain("filter", in_chain)
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain,
        "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "ACCEPT",
    ])
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain,
        "-p", "udp", "--sport", "68", "--dport", "67", "-j", "ACCEPT",
    ])
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain,
        "-p", "udp", "--sport", "67", "--dport", "68", "-j", "ACCEPT",
    ])
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain,
        "-p", "icmp", "--icmp-type", "echo-request", "-j", "ACCEPT",
    ])
    if local_dns_forward:
        for proto in ("udp", "tcp"):
            _run_usbnet_cmd([
                IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain,
                "-d", "%s/32" % gateway_ip, "-p", proto, "--dport", "53", "-j", "ACCEPT",
            ])
    _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", "filter", "-A", in_chain, "-j", "DROP"])
    _iptables_ensure_jump("filter", "INPUT", ["-i", bridge], in_chain)

    # ② FORWARD / DOCKER-USER 专属链：白名单放行上游 DNS 与授权出口网关，阻断 RFC1918/管理段/Docker 桥
    _iptables_ensure_chain("filter", fwd_chain)
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
        "-m", "conntrack", "--ctstate", "RELATED,ESTABLISHED", "-j", "ACCEPT",
    ])
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
        "-i", bridge, "-o", bridge, "-j", "DROP",
    ])
    for dns_ip in upstream_dns:
        for proto in ("udp", "tcp"):
            _run_usbnet_cmd([
                IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
                "-i", bridge, "-d", "%s/32" % dns_ip, "-p", proto, "--dport", "53", "-j", "ACCEPT",
            ])
    for gw_ip in vpn_gateways:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
            "-i", bridge, "-d", "%s/32" % gw_ip, "-j", "ACCEPT",
        ])
    for blocked_cidr in USBNET_BLOCKED_DEST_CIDRS:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
            "-i", bridge, "-d", blocked_cidr, "-j", "DROP",
        ])
    _run_usbnet_cmd([
        IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
        "-i", bridge, "-o", "docker0", "-j", "DROP",
    ])
    if uplink_iface:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
            "-i", bridge, "-o", uplink_iface, "-j", "ACCEPT",
        ])
    else:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain,
            "-i", bridge, "-j", "ACCEPT",
        ])
    _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", "filter", "-A", fwd_chain, "-j", "DROP"])
    _iptables_ensure_jump("filter", "FORWARD", ["-o", bridge], fwd_chain)
    _iptables_ensure_jump("filter", "FORWARD", ["-i", bridge], fwd_chain)
    if _iptables_chain_exists("filter", "DOCKER-USER"):
        _iptables_ensure_jump("filter", "DOCKER-USER", ["-o", bridge], fwd_chain)
        _iptables_ensure_jump("filter", "DOCKER-USER", ["-i", bridge], fwd_chain)

    # ③ NAT 专属链：仅对受控子网出网做 MASQUERADE
    _iptables_ensure_chain("nat", nat_chain)
    if uplink_iface:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "nat", "-A", nat_chain,
            "-s", subnet_cidr, "-o", uplink_iface, "-j", "MASQUERADE",
        ])
    else:
        _run_usbnet_cmd([
            IPTABLES_BIN, "-w", "-t", "nat", "-A", nat_chain,
            "-s", subnet_cidr, "!", "-o", bridge, "-j", "MASQUERADE",
        ])
    _iptables_ensure_jump("nat", "POSTROUTING", ["-s", subnet_cidr, "!", "-o", bridge], nat_chain)


def _remove_usbnet_firewall(egress, profile):
    """摘除并删除该出口全部专属规则链，绝不触碰宿主机其他规则。"""
    bridge = profile["bridge"]
    subnet_cidr = profile["subnet_cidr"]
    in_chain, fwd_chain, nat_chain = _usbnet_chain_names(egress)

    _iptables_delete_jumps("filter", "INPUT", ["-i", bridge], in_chain)
    _iptables_delete_jumps("filter", "FORWARD", ["-i", bridge], fwd_chain)
    _iptables_delete_jumps("filter", "FORWARD", ["-o", bridge], fwd_chain)
    if _iptables_chain_exists("filter", "DOCKER-USER"):
        _iptables_delete_jumps("filter", "DOCKER-USER", ["-i", bridge], fwd_chain)
        _iptables_delete_jumps("filter", "DOCKER-USER", ["-o", bridge], fwd_chain)
    _iptables_delete_jumps("nat", "POSTROUTING", ["-s", subnet_cidr, "!", "-o", bridge], nat_chain)

    for table, chain in (("filter", in_chain), ("filter", fwd_chain), ("nat", nat_chain)):
        if _iptables_chain_exists(table, chain):
            _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", table, "-F", chain], check=False)
            _run_usbnet_cmd([IPTABLES_BIN, "-w", "-t", table, "-X", chain], check=False)


def _build_dnsmasq_argv(egress, profile):
    """D10.6：强制 --port=0 --bind-interfaces --interface=<bridge>，PID/租约落在 /run/stp-agent-priv/。"""
    pid_path, lease_path = _usbnet_dnsmasq_paths(egress)
    return [
        DNSMASQ_BIN,
        "--port=0",
        "--bind-interfaces",
        "--interface=%s" % profile["bridge"],
        "--except-interface=lo",
        "--listen-address=%s" % profile["gateway_ip"],
        "--dhcp-range=%s,%s,%s,12h" % (profile["dhcp_start"], profile["dhcp_end"], profile["netmask"]),
        "--dhcp-option=option:router,%s" % profile["gateway_ip"],
        "--dhcp-option=option:dns-server,%s" % ",".join(profile["upstream_dns"]),
        "--dhcp-leasefile=%s" % lease_path,
        "--pid-file=%s" % pid_path,
        "--conf-file=/dev/null",
        "--no-hosts",
        "--no-resolv",
    ]


def _enable_ipv4_forwarding():
    try:
        fd = os.open(USBNET_IP_FORWARD_PATH, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError as exc:
        raise _UsbnetCmdError("cannot open %s: %s" % (USBNET_IP_FORWARD_PATH, exc)) from exc
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise _UsbnetCmdError("%s is not a regular file" % USBNET_IP_FORWARD_PATH)
        os.write(fd, b"1\n")
    finally:
        os.close(fd)


def _detach_single_binding_locked(ledger, binding, conf, now):
    """按 D10.5 完备三分支安全收敛单条台账绑定；返回 (branch_result, error_detail_or_None)。"""
    sid = binding["session_id"]
    gen = binding["generation"]
    serial = binding["device_serial"]
    bound_iface = binding["iface"]
    egress = binding["egress"]
    expected_bridge = conf["egress_profiles"][egress]["bridge"]
    key = _usbnet_binding_key(sid, gen)

    try:
        live = _inspect_usbnet_iface(bound_iface, require_driver=False)
    except (PrivError, OSError):
        live = None

    # 分支 ①：接口消失、身份不符（接口名被新设备/新会话复用）或意外挂在非本受控他桥上
    if (
        live is None
        or live["ifindex"] != binding["ifindex"]
        or live["usb_devpath"] != binding["usb_devpath"]
        or live["mac"] != binding["mac"]
        or (live["master"] is not None and live["master"] != expected_bridge)
    ):
        ledger["active_bindings"].pop(key, None)
        _record_revocation(ledger, sid, gen, serial, now, advance_watermark=True)
        return "already_gone_or_reused", None

    # 分支 ②：身份匹配但未挂入任何网桥（含「预写 attaching 后、挂桥前中断」）
    if live["master"] is None:
        try:
            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", bound_iface, "down"])
        except _UsbnetCmdError as exc:
            return "error", str(exc)
        ledger["active_bindings"].pop(key, None)
        _record_revocation(ledger, sid, gen, serial, now, advance_watermark=True)
        return "detached_unbridged", None

    # 分支 ③：身份与本受控网桥成员关系完全匹配
    try:
        _run_usbnet_cmd([IP_BIN, "link", "set", "dev", bound_iface, "down"])
        _run_usbnet_cmd([IP_BIN, "link", "set", "dev", bound_iface, "nomaster"])
    except _UsbnetCmdError as exc:
        return "error", str(exc)
    ledger["active_bindings"].pop(key, None)
    _record_revocation(ledger, sid, gen, serial, now, advance_watermark=True)
    return "detached", None


# ── 六个 usbnet-* 子命令处理函数 ────────────────────────────────────────────


def cmd_usbnet_status(args, conf):
    """只读查询 usbnet 授权、单机上限、网桥、活跃绑定、撤销水位摘要与 dnsmasq 健康态（D10.2）。"""
    _require_root()
    try:
        uconf = _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    # 只读自检：必须具备受信任的外部工具（不修改任何系统状态）
    for binary in (IP_BIN, BRIDGE_BIN, IPTABLES_BIN, DNSMASQ_BIN):
        _require_trusted_usbnet_binary(binary)

    dir_fd = _open_usbnet_run_dir(create=False)
    try:
        ledger = _read_usbnet_ledger_at(dir_fd) if dir_fd is not None else _empty_usbnet_ledger()
        managed_bridges = {}
        dnsmasq_map = {}
        for egress in uconf["allowed_egresses"]:
            profile = uconf["egress_profiles"][egress]
            bridge = profile["bridge"]
            bridge_sysfs = os.path.join(NET_SYSFS_BASE, bridge)
            managed_bridges[egress] = {
                "bridge": bridge,
                "subnet_cidr": profile["subnet_cidr"],
                "exists": os.path.lexists(bridge_sysfs),
            }
            dnsmasq_map[egress] = _usbnet_dnsmasq_status(dir_fd, egress, bridge)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)

    rev = ledger["revocation_state"]
    min_iat = {egress: rev["min_allowed_iat_by_egress"].get(egress, 0) for egress in uconf["allowed_egresses"]}
    for egress, val in rev["min_allowed_iat_by_egress"].items():
        if egress not in min_iat:
            min_iat[egress] = val

    payload = {
        "usbnet_enabled": True,
        "host_id": uconf["host_id"],
        "usbnet_max_devices": uconf["usbnet_max_devices"],
        "allowed_egresses": uconf["allowed_egresses"],
        "allowed_usb_ports": uconf["allowed_usb_ports"],
        "managed_bridges": managed_bridges,
        "active_bindings": [
            ledger["active_bindings"][k] for k in sorted(ledger["active_bindings"])
        ],
        "revocation_state": {
            "min_allowed_iat_by_egress": min_iat,
            "min_allowed_generation_by_serial": dict(rev["min_allowed_generation_by_serial"]),
            "revoked_sessions_count": len(rev["revoked_sessions"]),
        },
        "dnsmasq": dnsmasq_map,
    }
    print(json.dumps(payload, sort_keys=True))
    return 0


def cmd_usbnet_ensure_bridge(args, conf):
    """幂等创建受控网桥、安装三层隔离专属规则链并启动 DHCP-only dnsmasq（D10.6）。"""
    _require_root()
    try:
        uconf = _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    egress = args.egress
    if egress not in uconf["allowed_egresses"]:
        _fail("egress %r is not enabled in allowed_egresses" % (egress,))
    profile = uconf["egress_profiles"][egress]
    bridge = profile["bridge"]

    with _locked_usbnet_ledger() as (dir_fd, _ledger):
        try:
            rc, _, _ = _run_usbnet_cmd([IP_BIN, "link", "show", "dev", bridge], check=False)
            if rc != 0:
                _run_usbnet_cmd([IP_BIN, "link", "add", "name", bridge, "type", "bridge"])
            _run_usbnet_cmd([
                IP_BIN, "addr", "replace",
                "%s/%d" % (profile["gateway_ip"], profile["network"].prefixlen),
                "dev", bridge,
            ])
            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", bridge, "up"])
            _enable_ipv4_forwarding()
            _install_usbnet_firewall(egress, profile)

            dm_status = _usbnet_dnsmasq_status(dir_fd, egress, bridge)
            if not dm_status["running"]:
                _run_usbnet_cmd(_build_dnsmasq_argv(egress, profile))
        except _UsbnetCmdError as exc:
            print("STP_USBNET_ENSURE_BRIDGE_FAILED egress=%s detail=%s" % (egress, exc), file=sys.stderr)
            return 3

    print(json.dumps({
        "result": "ok",
        "egress": egress,
        "bridge": bridge,
        "subnet_cidr": profile["subnet_cidr"],
        "gateway_ip": profile["gateway_ip"],
    }, sort_keys=True))
    return 0


def cmd_usbnet_attach(args, conf):
    """验签 usbnet_grant + 三项撤销屏障 + sysfs 身份核验 + 隔离前置两阶段提交挂桥（D10.4）。"""
    _require_root()
    try:
        uconf = _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    expect_devpath = validate_usb_port(args.expect_devpath)
    iface = validate_usbnet_iface_name(args.iface)
    now = int(time.time())
    grant = verify_usbnet_grant(args.grant, uconf, expect_devpath, now=now)

    sid = grant["session_id"]
    gen = grant["generation"]
    serial = grant["device_serial"]
    egress = grant["egress"]
    bridge = uconf["egress_profiles"][egress]["bridge"]
    key = _usbnet_binding_key(sid, gen)

    with _locked_usbnet_ledger() as (dir_fd, ledger):
        _gc_revoked_sessions(ledger, now)
        revoked_reason = _check_grant_revocation_barriers(ledger, grant)
        if revoked_reason is not None:
            print(
                "%s session_id=%s generation=%d reason=%s"
                % (USBNET_MARKER_GRANT_REVOKED, sid, gen, revoked_reason),
                file=sys.stderr,
            )
            return 3

        live = _inspect_usbnet_iface(iface, require_driver=True)
        if live["usb_devpath"] not in uconf["allowed_usb_ports"]:
            _fail("sysfs usb_devpath %s is not in allowed_usb_ports" % live["usb_devpath"])
        if live["usb_devpath"] != expect_devpath or live["usb_devpath"] != grant["usb_devpath"]:
            _fail(
                "sysfs usb_devpath %s mismatches expect/grant %s"
                % (live["usb_devpath"], expect_devpath)
            )
        if live["serial"] != serial:
            _fail("sysfs USB serial %r mismatches grant device_serial %r" % (live["serial"], serial))

        active = ledger["active_bindings"]
        existing = active.get(key)
        if existing is not None:
            if (
                existing["usb_devpath"] != live["usb_devpath"]
                or existing["device_serial"] != serial
                or existing["egress"] != egress
            ):
                _fail("binding identity conflict for %s" % key)
        other_bindings = {k: v for k, v in active.items() if k != key}
        if len(other_bindings) >= uconf["usbnet_max_devices"]:
            _fail("usbnet_max_devices limit reached (%d)" % uconf["usbnet_max_devices"])
        for other in other_bindings.values():
            if other["usb_devpath"] == live["usb_devpath"]:
                _fail("usb_devpath %s is already bound by %s" % (live["usb_devpath"], other["session_id"]))
            if other["iface"] == iface:
                _fail("iface %s is already bound by %s" % (iface, other["session_id"]))

        # ① 预写 state="attaching" 原子落盘
        active[key] = {
            "session_id": sid,
            "generation": gen,
            "state": "attaching",
            "egress": egress,
            "device_serial": serial,
            "iface": iface,
            "ifindex": live["ifindex"],
            "usb_devpath": live["usb_devpath"],
            "mac": live["mac"],
            "attached_at": now,
        }
        _write_usbnet_ledger_at(dir_fd, ledger)

        # ② down -> ③ master -> ④ isolated on + 回读 "1" -> ⑤ 复核 ifindex & isolated == "1" -> up
        try:
            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", iface, "down"])
            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", iface, "master", bridge])
            _run_usbnet_cmd([BRIDGE_BIN, "link", "set", "dev", iface, "isolated", "on"])
            try:
                iso_val = _read_brport_isolated(live["net_real"])
            except (PrivError, OSError) as exc:
                raise _UsbnetCmdError("brport/isolated readback failed: %s" % exc) from exc
            if iso_val != "1":
                raise _UsbnetCmdError("brport/isolated readback expected '1', got %r" % (iso_val,))

            # ⑤ UP 前复核 ifindex 未变且 brport/isolated 仍为 "1"
            try:
                cur_ifindex = _read_net_ifindex(live["net_real"])
                iso_val_recheck = _read_brport_isolated(live["net_real"])
            except (PrivError, OSError) as exc:
                raise _UsbnetCmdError("pre-UP sysfs recheck failed: %s" % exc) from exc
            if cur_ifindex != live["ifindex"] or iso_val_recheck != "1":
                raise _UsbnetCmdError(
                    "pre-UP recheck mismatch ifindex=%s(want %s) isolated=%r"
                    % (cur_ifindex, live["ifindex"], iso_val_recheck)
                )

            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", iface, "up"])
        except _UsbnetCmdError as exc:
            # 任一步失败：先强制 down 维持不转发，再尽力 nomaster
            _run_usbnet_cmd([IP_BIN, "link", "set", "dev", iface, "down"], check=False)
            nomaster_rc, _, _ = _run_usbnet_cmd(
                [IP_BIN, "link", "set", "dev", iface, "nomaster"], check=False,
            )
            unbridged = nomaster_rc == 0
            if not unbridged:
                try:
                    unbridged = _read_net_master(live["net_real"]) is None
                except (PrivError, OSError):
                    unbridged = False
            if unbridged:
                active.pop(key, None)
                _write_usbnet_ledger_at(dir_fd, ledger)
                rollback_state = "complete"
            else:
                rollback_state = "incomplete_port_down"
            print(
                "%s rollback=%s session_id=%s generation=%d reason=%s"
                % (USBNET_MARKER_ATTACH_ROLLBACK, rollback_state, sid, gen, exc),
                file=sys.stderr,
            )
            return 3

        # 原子更新为 attached 并单调推进按 device_serial 索引的最小代次水位
        active[key]["state"] = "attached"
        active[key]["attached_at"] = int(time.time())
        min_gen_map = ledger["revocation_state"]["min_allowed_generation_by_serial"]
        if gen > min_gen_map.get(serial, 0):
            min_gen_map[serial] = gen
        _write_usbnet_ledger_at(dir_fd, ledger)

    print(json.dumps({
        "result": "attached",
        "session_id": sid,
        "generation": gen,
        "egress": egress,
        "bridge": bridge,
        "iface": iface,
        "ifindex": live["ifindex"],
        "usb_devpath": live["usb_devpath"],
        "device_serial": serial,
    }, sort_keys=True))
    return 0


def cmd_usbnet_detach(args, conf):
    """基于台账安全摘桥与半态收敛（独立于短期 usbnet_grant 有效期，D10.5）。"""
    _require_root()
    try:
        uconf = _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    sid = args.session_id
    gen = args.generation
    if not isinstance(sid, str) or not _USBNET_ID_RE.match(sid):
        _fail("--session-id invalid")
    if type(gen) is not int or gen < 1:
        _fail("--generation must be an integer >= 1")
    expect_iface = args.expect_iface
    if expect_iface is not None and not _USBNET_IFACE_RE.match(expect_iface):
        _fail("--expect-iface invalid: %r" % (expect_iface,))
    expect_devpath = validate_usb_port(args.expect_devpath) if args.expect_devpath is not None else None

    key = _usbnet_binding_key(sid, gen)
    now = int(time.time())

    with _locked_usbnet_ledger() as (dir_fd, ledger):
        _gc_revoked_sessions(ledger, now)
        binding = ledger["active_bindings"].get(key)
        if binding is None:
            _record_revocation(ledger, sid, gen, "", now, advance_watermark=False)
            _write_usbnet_ledger_at(dir_fd, ledger)
            print(json.dumps({
                "result": "already_gone_or_reused",
                "reason": "not_in_ledger",
                "session_id": sid,
                "generation": gen,
            }, sort_keys=True))
            return 0

        if expect_iface is not None and expect_iface != binding["iface"]:
            _fail(
                "%s expect_iface=%s bound_iface=%s"
                % (USBNET_MARKER_BINDING_MISMATCH, expect_iface, binding["iface"])
            )
        if expect_devpath is not None and expect_devpath != binding["usb_devpath"]:
            _fail(
                "%s expect_devpath=%s bound_devpath=%s"
                % (USBNET_MARKER_BINDING_MISMATCH, expect_devpath, binding["usb_devpath"])
            )

        result, err = _detach_single_binding_locked(ledger, binding, uconf, now)
        if err is not None:
            print(
                "STP_USBNET_DETACH_FAILED session_id=%s generation=%d reason=%s" % (sid, gen, err),
                file=sys.stderr,
            )
            return 3
        _write_usbnet_ledger_at(dir_fd, ledger)

    print(json.dumps({
        "result": result,
        "session_id": sid,
        "generation": gen,
        "iface": binding["iface"],
        "usb_devpath": binding["usb_devpath"],
    }, sort_keys=True))
    return 0


def cmd_usbnet_revoke(args, conf):
    """记录撤销并单调推进 device_serial 代次水位，不删除 active_bindings（D10.5）。"""
    _require_root()
    try:
        _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    sid = args.session_id
    gen = args.generation
    serial = args.serial
    if not isinstance(sid, str) or not _USBNET_ID_RE.match(sid):
        _fail("--session-id invalid")
    if type(gen) is not int or gen < 1:
        _fail("--generation must be an integer >= 1")
    if not isinstance(serial, str) or not _USBNET_SERIAL_RE.match(serial):
        _fail("--serial invalid")

    now = int(time.time())
    with _locked_usbnet_ledger() as (dir_fd, ledger):
        _gc_revoked_sessions(ledger, now)
        _record_revocation(ledger, sid, gen, serial, now, advance_watermark=True)
        _write_usbnet_ledger_at(dir_fd, ledger)

    print(json.dumps({
        "result": "revoked",
        "session_id": sid,
        "generation": gen,
        "serial": serial,
    }, sort_keys=True))
    return 0


def _stop_usbnet_dnsmasq(dir_fd, egress, bridge):
    """停止该出口 dnsmasq 并清理 /run/stp-agent-priv/ 下 PID 与租约文件。"""
    errors = []
    status = _usbnet_dnsmasq_status(dir_fd, egress, bridge)
    if status["running"] and status["pid"] is not None:
        pid = status["pid"]
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except OSError as exc:
            errors.append("dnsmasq_sigterm:%s" % exc)
        # 有界等待退出
        for _ in range(30):
            if not _usbnet_dnsmasq_status(dir_fd, egress, bridge)["running"]:
                break
            time.sleep(0.1)
        if _usbnet_dnsmasq_status(dir_fd, egress, bridge)["running"]:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except OSError as exc:
                errors.append("dnsmasq_sigkill:%s" % exc)
            for _ in range(20):
                if not _usbnet_dnsmasq_status(dir_fd, egress, bridge)["running"]:
                    break
                time.sleep(0.1)

    for fname in ("dnsmasq-%s.pid" % egress, "dnsmasq-%s.leases" % egress):
        try:
            os.unlink(fname, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        except OSError as exc:
            errors.append("unlink_%s:%s" % (fname, exc))
    return errors


def cmd_usbnet_teardown(args, conf):
    """零残留关闭受控网桥：无条件先推进 min_allowed_iat_by_egress 落盘，再清理与零残留复核（D10.5）。"""
    _require_root()
    try:
        uconf = _load_usbnet_conf()
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2

    egress = args.egress
    if egress not in uconf["allowed_egresses"]:
        _fail("egress %r is not enabled in allowed_egresses" % (egress,))
    profile = uconf["egress_profiles"][egress]
    bridge = profile["bridge"]
    force = bool(args.force)

    with _locked_usbnet_ledger() as (dir_fd, ledger):
        now = int(time.time())
        _gc_revoked_sessions(ledger, now)
        egress_bindings = [
            (k, dict(v))
            for k, v in sorted(ledger["active_bindings"].items())
            if v["egress"] == egress
        ]
        if egress_bindings and not force:
            print(
                "%s egress=%s count=%d" % (USBNET_MARKER_ACTIVE_BINDINGS, egress, len(egress_bindings)),
                file=sys.stderr,
            )
            return 3

        # ① 无条件先持久化撤销屏障（即使 active_bindings 为空也推进 min_allowed_iat_by_egress）
        min_iat_map = ledger["revocation_state"]["min_allowed_iat_by_egress"]
        new_iat_barrier = max(min_iat_map.get(egress, 0), now + USBNET_TEARDOWN_COOLDOWN_SECONDS)
        min_iat_map[egress] = new_iat_barrier
        for _key, binding in egress_bindings:
            _record_revocation(
                ledger,
                binding["session_id"],
                binding["generation"],
                binding["device_serial"],
                now,
                advance_watermark=True,
            )
        _write_usbnet_ledger_at(dir_fd, ledger)

        errors = []
        # ② 逐条按三分支安全摘桥并移除 active_bindings
        for _key, binding in egress_bindings:
            _res, err = _detach_single_binding_locked(ledger, binding, uconf, now)
            if err is not None:
                errors.append("detach_%s#%d:%s" % (binding["session_id"], binding["generation"], err))
        _write_usbnet_ledger_at(dir_fd, ledger)

        # ③ 停止 dnsmasq 并清理 /run/stp-agent-priv/ 下 PID/租约文件
        errors.extend(_stop_usbnet_dnsmasq(dir_fd, egress, bridge))

        # ④ 摘除并删除专属规则链
        try:
            _remove_usbnet_firewall(egress, profile)
        except _UsbnetCmdError as exc:
            errors.append("firewall:%s" % exc)

        # ⑤ 删除受控网桥
        if os.path.lexists(os.path.join(NET_SYSFS_BASE, bridge)):
            rc_down, _, err_down = _run_usbnet_cmd([IP_BIN, "link", "set", "dev", bridge, "down"], check=False)
            if rc_down != 0:
                errors.append("bridge_down:%s" % err_down.strip()[:120])
            rc_del, _, err_del = _run_usbnet_cmd(
                [IP_BIN, "link", "delete", "dev", bridge, "type", "bridge"], check=False,
            )
            if rc_del != 0:
                errors.append("bridge_delete:%s" % err_del.strip()[:120])

        # ⑥ 零残留复核
        if os.path.lexists(os.path.join(NET_SYSFS_BASE, bridge)):
            errors.append("residue_bridge_exists:%s" % bridge)
        if _usbnet_dnsmasq_status(dir_fd, egress, bridge)["running"]:
            errors.append("residue_dnsmasq_running")
        for fname in ("dnsmasq-%s.pid" % egress, "dnsmasq-%s.leases" % egress):
            try:
                os.lstat(fname, dir_fd=dir_fd)
                errors.append("residue_file:%s" % fname)
            except FileNotFoundError:
                pass
            except OSError as exc:
                errors.append("residue_stat_%s:%s" % (fname, exc))
        in_chain, fwd_chain, nat_chain = _usbnet_chain_names(egress)
        for table, chain in (("filter", in_chain), ("filter", fwd_chain), ("nat", nat_chain)):
            if _iptables_chain_exists(table, chain):
                errors.append("residue_chain:%s/%s" % (table, chain))
        remaining_bindings = [
            k for k, v in ledger["active_bindings"].items() if v["egress"] == egress
        ]
        if remaining_bindings:
            errors.append("residue_active_bindings:%d" % len(remaining_bindings))

        if errors:
            print(
                "%s egress=%s errors=%s"
                % (USBNET_MARKER_TEARDOWN_INCOMPLETE, egress, json.dumps(errors, separators=(",", ":"))),
                file=sys.stderr,
            )
            return 3

    print(json.dumps({
        "result": "teardown_ok",
        "egress": egress,
        "bridge": bridge,
        "min_allowed_iat": new_iat_barrier,
    }, sort_keys=True))
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

# 子命令接线契约（#2011）。教训：`--help` 形态的能力探针**抓不到**「探针过、真调用
# 失败」——argparse 接线错误（add_parser 返回值被丢弃 / 参数挂到别的子解析器）时
# `--help` 照常 exit 0，而真实参数 exit 2（unrecognized arguments），远端脚本在
# `set -e` 下整段中止。因此把每个子命令的**代表性真实 argv**列成契约，由 selftest
# 逐个 parse 断言；控制面远端脚本开头即跑 selftest（失败 → 回落 legacy），本类缺陷
# 由此 fail-safe 而非带病上阵。
_SUBCOMMAND_CONTRACT = {
    "selftest": [],
    "capabilities": [],
    "bootstrap": [
        "--install-dir", "/opt/stability-test-agent",
        "--user", "android", "--group", "android",
        "--service", "stability-test-agent",
    ],
    "apply-code": ["--staged", "/tmp/staged"],
    "install-schema": ["--file", "/tmp/staged/pipeline_schema.json"],
    "write-version": ["--version", "deadbeef"],
    "write-digest": ["--digest", "sha256:" + "0" * 64],
    "sync-env": [
        "--secret-b64", "AA==", "--overrides-b64", "AA==", "--path-keys-b64", "AA==",
        "--retired-keys-b64", "AA==",
    ],
    "deps-marker": ["--sha", "0" * 64],
    "fix-ownership": [],
    "restart": [],
    "ensure-udev-rule": [],
    "usb-authorized": ["--port", "1-5.3.1", "--value", "0"],
    # #2957：只读内核日志（D7）。契约 argv 走 --since-epoch 形态（--boot 由解析层
    # 的互斥组覆盖，探针形态另有单测）。
    "read-kernel-log": ["--since-epoch", "1555555555"],
    # #3892：USB 切电（D8）。契约 argv 走 cycle + delay 形态（覆盖全部四个参数）。
    "usb-power": ["--serial", "ABC123", "--expect-slot", "1-1.4:3",
                  "--action", "cycle", "--delay", "12"],
    # #2972：xHCI 主控自动恢复（D9）。无目标参数子命令。
    "xhci-rebind": [],
    # #4212：USB 反向供网受限通道（D10）。六个窄子命令。
    "usbnet-ensure-bridge": ["--egress", "direct"],
    "usbnet-attach": [
        "--grant", "eyJ0ZXN0IjoxfQ." + "A" * 86,
        "--iface", "usb0",
        "--expect-devpath", "1-1.2",
    ],
    "usbnet-detach": [
        "--session-id", "sess-1",
        "--generation", "1",
        "--expect-iface", "usb0",
        "--expect-devpath", "1-1.2",
    ],
    "usbnet-revoke": [
        "--session-id", "sess-1",
        "--generation", "1",
        "--serial", "MTK0001",
    ],
    "usbnet-teardown": ["--egress", "direct", "--force"],
    "usbnet-status": [],
}

# #3356：参数级能力标记（capabilities 在子命令行之外逐行输出）。旧 wrapper 只报
# 子命令名，控制面远端脚本据此对**缺少新参数面**的 wrapper 在任何写动作之前
# fail-closed（子命令级判据对「同名的 sync-env 缺新 flag」没有判别力）。
# #3892：`usb-power/expect-slot` 标记 usb-power 实现了 `--expect-slot` 比对；
# flash_firmware 1.4.0 在取锁前要求此标记（ADR-0063 D3 第 9 条）。
# #4212：`usbnet` 与 `usbnet/grant-v1` 标记 D10 六个窄子命令与 Ed25519 grant 验签契约。
_EXTRA_CAPABILITIES = (
    "sync-env/retired-keys",
    "usb-power/expect-slot",
    "usbnet",
    "usbnet/grant-v1",
)


def _registered_subcommands(parser) -> list:
    """枚举 parser 实际注册的子命令（契约完备性检查用）。"""
    for action in getattr(parser, "_actions", []):
        choices = getattr(action, "choices", None)
        if isinstance(choices, dict) and choices:
            return sorted(choices)
    return []


def _validate_parser_contract(parser) -> list:
    """用真实 argv 逐个 parse 断言接线；返回问题清单（空 = 通过）。"""
    problems = []
    registered = _registered_subcommands(parser)
    for command in registered:
        if command not in _SUBCOMMAND_CONTRACT:
            problems.append("subcommand %s not covered by contract table" % command)
    for command, argv in sorted(_SUBCOMMAND_CONTRACT.items()):
        if command not in registered:
            problems.append("contract entry %s missing from parser" % command)
            continue
        sink = io.StringIO()
        try:
            with redirect_stderr(sink):
                ns = parser.parse_args([command] + list(argv))
        except SystemExit as exc:
            problems.append(
                "subcommand %s rejected contract argv (exit=%s): %s"
                % (command, exc.code, sink.getvalue().strip()[:200])
            )
            continue
        if getattr(ns, "command", None) != command:
            problems.append("subcommand %s parsed to command=%r" % (command, ns.command))
            continue
        for opt in argv:
            if not opt.startswith("--"):
                continue
            dest = opt.lstrip("-").replace("-", "_")
            if not hasattr(ns, dest):
                problems.append("subcommand %s does not wire %s" % (command, opt))
    return problems


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="stp-agent-priv",
        description="Privilege boundary wrapper for STP Agent hosts (#1250).",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("selftest", help="check wrapper/config integrity")
    # #2319：能力清单（交付链按集合比对；见 cmd_capabilities 的 docstring）
    sub.add_parser("capabilities", help="print supported subcommands (one per line)")

    p = sub.add_parser("bootstrap", help="write conf + sudoers (install time)")
    p.add_argument("--install-dir", required=True)
    p.add_argument("--user", required=True)
    p.add_argument("--group", required=True)
    p.add_argument("--service", required=True)

    p = sub.add_parser("apply-code", help="sync staged agent tree into install dir")
    p.add_argument("--staged", required=True)

    p = sub.add_parser("install-schema", help="install pipeline schema")
    p.add_argument("--file", required=True)

    p = sub.add_parser("write-version", help="write agent/VERSION")
    p.add_argument("--version", default="")

    p = sub.add_parser("write-digest", help="write agent/ARTIFACT_DIGEST (ADR-0040)")
    # #2011：--digest 属 write-digest（cmd_write_digest 读 args.digest）；此前它被
    # 误挂到下一段（当时的下一段是已随 ADR-0040 D8 R4 删除的 apply-resources）。
    p.add_argument("--digest", default="")

    p = sub.add_parser("sync-env", help="update .env secret/overrides")
    p.add_argument("--secret-b64", default="")
    p.add_argument("--overrides-b64", default="")
    p.add_argument("--path-keys-b64", default="")
    # #3356：退役键清单（JSON 数组的 b64），merge 后从 .env 删除这些行。
    p.add_argument("--retired-keys-b64", default="")

    p = sub.add_parser("deps-marker", help="write deps installed sha marker")
    p.add_argument("--sha", required=True)

    sub.add_parser("fix-ownership", help="restore install dir ownership")
    sub.add_parser("restart", help="restart the agent service")

    # ADR-0037 D5（#2133）：flash 链运行时提权窄面。
    sub.add_parser(
        "ensure-udev-rule",
        help="ensure the fixed MTK ttyACM udev rule and reload (ADR-0037 D5)",
    )
    p = sub.add_parser(
        "usb-authorized",
        help="toggle sysfs authorized for an MTK USB port (ADR-0037 D5)",
    )
    p.add_argument("--port", required=True)
    p.add_argument("--value", required=True, choices=["0", "1"])

    # ADR-0037 D7（#2957）：内核日志只读窄面。allow_abbrev=False——否则 `--since`
    # 这类缩写会被 argparse 接受，等于把「两个整数参数」的窄面解释权交给前缀匹配。
    p = sub.add_parser(
        "read-kernel-log",
        help="read the kernel log (journalctl -k) with a fixed argv (ADR-0037 D7)",
        allow_abbrev=False,
    )
    window = p.add_mutually_exclusive_group(required=True)
    window.add_argument("--boot", action="store_true", help="read the current boot")
    window.add_argument(
        "--since-epoch",
        type=int,
        default=None,
        help="read since a unix timestamp (>= 0, not in the future)",
    )
    p.add_argument(
        "--lines",
        type=int,
        default=None,
        help="limit output to the last N lines (1..%d)" % MAX_KERNEL_LOG_LINES,
    )

    # ADR-0037 D8 / ADR-0063 D2（#3892）：USB 切电窄面。只接受 serial，槽位查 root
    # 属主映射；allow_abbrev=False 理由同 read-kernel-log。
    p = sub.add_parser(
        "usb-power",
        help="power a registered USB hub slot on/off by device serial (ADR-0037 D8)",
        allow_abbrev=False,
    )
    p.add_argument("--serial", required=True)
    # 调用方读到的槽位（`<hub_location>:<port>`）。wrapper 只拿它与自己查表的结果
    # 比对，不一致即拒绝、不调 uhubctl；切电目标仍只来自映射（复核 #3899 comment
    # 6708089815：回显核对发生在切电之后，映射被改写时会先切错一次）。
    p.add_argument("--expect-slot", required=True, dest="expect_slot")
    p.add_argument("--action", required=True, choices=list(USB_POWER_ACTIONS))
    p.add_argument(
        "--delay", type=int, default=None,
        help="off time for cycle, 1..%d seconds" % USB_POWER_MAX_DELAY,
    )

    # ADR-0037 D9（#2972）：xHCI 主控自动恢复无目标参数窄面。
    sub.add_parser(
        "xhci-rebind",
        help="unbind and rebind bound xhci_hcd PCI controllers without target args (ADR-0037 D9)",
        allow_abbrev=False,
    )

    # ADR-0037 D10 / ADR-0066 D6（#4212）：USB 反向供网受限窄面（全部 allow_abbrev=False）。
    p = sub.add_parser(
        "usbnet-ensure-bridge",
        help="ensure managed USB-net bridge, isolation chains, and DHCP-only dnsmasq (ADR-0037 D10)",
        allow_abbrev=False,
    )
    p.add_argument("--egress", required=True, choices=list(USBNET_ALLOWED_EGRESS_CHOICES))

    p = sub.add_parser(
        "usbnet-attach",
        help="verify signed usbnet_grant and attach USB gadget iface with L2 isolation (ADR-0037 D10)",
        allow_abbrev=False,
    )
    p.add_argument("--grant", required=True)
    p.add_argument("--iface", required=True)
    p.add_argument("--expect-devpath", required=True, dest="expect_devpath")

    p = sub.add_parser(
        "usbnet-detach",
        help="safely detach a tracked USB-net session binding without requiring grant TTL (ADR-0037 D10)",
        allow_abbrev=False,
    )
    p.add_argument("--session-id", required=True, dest="session_id")
    p.add_argument("--generation", required=True, type=int)
    p.add_argument("--expect-iface", default=None, dest="expect_iface")
    p.add_argument("--expect-devpath", default=None, dest="expect_devpath")

    p = sub.add_parser(
        "usbnet-revoke",
        help="revoke a USB-net session and advance per-serial generation watermark (ADR-0037 D10)",
        allow_abbrev=False,
    )
    p.add_argument("--session-id", required=True, dest="session_id")
    p.add_argument("--generation", required=True, type=int)
    p.add_argument("--serial", required=True)

    p = sub.add_parser(
        "usbnet-teardown",
        help="tear down managed USB-net bridge, dnsmasq, and isolation chains with zero residue (ADR-0037 D10)",
        allow_abbrev=False,
    )
    p.add_argument("--egress", required=True, choices=list(USBNET_ALLOWED_EGRESS_CHOICES))
    p.add_argument("--force", action="store_true", default=False)

    sub.add_parser(
        "usbnet-status",
        help="read-only USB-net authorization, bindings, revocation summary, and dnsmasq health (ADR-0037 D10)",
        allow_abbrev=False,
    )
    return parser


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    conf = None
    if args.command != "bootstrap":
        try:
            conf = _load_conf(_conf_path())
        except PrivError as exc:
            if args.command == "selftest":
                print("STP_AGENT_PRIV_SELFTEST_FAIL: %s" % exc)
                return 1
            print("STP_AGENT_PRIV_ERROR: %s" % exc, file=sys.stderr)
            return 2

    handlers = {
        "selftest": cmd_selftest,
        "capabilities": cmd_capabilities,
        "bootstrap": cmd_bootstrap,
        "apply-code": cmd_apply_code,
        "install-schema": cmd_install_schema,
        "write-version": cmd_write_version,
        "write-digest": cmd_write_digest,
        "sync-env": cmd_sync_env,
        "deps-marker": cmd_deps_marker,
        "fix-ownership": cmd_fix_ownership,
        "restart": cmd_restart,
        "ensure-udev-rule": cmd_ensure_udev_rule,
        "usb-authorized": cmd_usb_authorized,
        "read-kernel-log": cmd_read_kernel_log,
        "usb-power": cmd_usb_power,
        "xhci-rebind": cmd_xhci_rebind,
        "usbnet-ensure-bridge": cmd_usbnet_ensure_bridge,
        "usbnet-attach": cmd_usbnet_attach,
        "usbnet-detach": cmd_usbnet_detach,
        "usbnet-revoke": cmd_usbnet_revoke,
        "usbnet-teardown": cmd_usbnet_teardown,
        "usbnet-status": cmd_usbnet_status,
    }
    try:
        return handlers[args.command](args, conf)
    except UsbnetDisabledError as exc:
        print("%s: %s" % (USBNET_MARKER_DISABLED, exc), file=sys.stderr)
        return 2
    except PrivError as exc:
        print("STP_AGENT_PRIV_ERROR: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # 未预期错误也不能静默
        print("STP_AGENT_PRIV_ERROR: unexpected: %s" % exc, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
