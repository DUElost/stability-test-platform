"""Flash-fleet host preflight — idempotent ensure for MLD 刷机前置条件.

编排位：Plan 中固定排在 flash_firmware 之前（每设备 job 各执行一次，
同 host 并发由 /tmp flock 去重）。一次性运维型步骤：修完即过，host 层
系统状态在两次派发之间不会自行变化。

逐项检查（v1.0.2：**不再使用 `sudo -n sh -c`**，修复只经提权 wrapper 窄面）：
    qt-libs      Debian 包 ×5（dpkg 断言）。缺包 → 明确失败 + 指引
                 （apt 安装归 provisioning/install 链，运行期不装包）
    flashtool    资源目录入口存在且可执行（缺执行位则 chmod；属主即 agent
                 用户，无需提权）
    dialout      当前用户**持久成员**在 dialout（读 /etc/group；旧版读进程
                 组集合，usermod 后未重启会每次重报 fixed 假修复）。
                 缺 → 视 udev 规则可否放行降级（有 0666 规则记 warning），
                 两者皆无才失败；运行期不 usermod（归 provisioning）
    udev-rule    0e8d ttyACM MODE=0666 规则在位；缺则调 wrapper
                 `ensure-udev-rule`（固定路径/内容 + reload，ADR-0037 D5）
    priv-face    wrapper/窄面能力**可观测性**项（warning-only，不判失败）：
                 wrapper 是否在位、`ensure-udev-rule` 是否可用——迁移期
                 供车队侧观察收敛进度
    nfs          {STP_NFS_ROOT}/firmware/{family}/latest.json 可读
                 （mount/网络问题超出本脚本职责，如实失败）

输出契约与其余平台脚本一致：stdout 单行 JSON success/metrics，
stderr PROGRESS 戳。fix=false 时只检查不修复（对 udev 修复生效）。

Environment:
    STP_NFS_ROOT           (fallback STP_AEE_NFS_ROOT → /mnt/stp-aee)
STP_STEP_PARAMS schema:
    fix                    : bool (optional, default true; false = 只检不修)
    skip_apt               : bool (optional, default false; 无外网/镜像的
                           host 跳过包类检查，改记 warning)
    dialout_user           : str  (optional, default "android")
    locales                : 预留，无副作用（与 oobe_skip 对齐命名习惯）
v1.0.2 相对 v1.0.1（#2133 / ADR-0037 D5）：
  - 全部运行期 root 操作改走 wrapper 窄面：udev 修复 = `stp-agent-priv
    ensure-udev-rule`（能力探针，缺失时明确失败并给出 update_agent.yml
    指引）；删除 `_sudo_sh`（不再有 `sudo -n sh -c` 任意命令面）。
  - apt：只检不装，缺包即失败（含恢复指引）——装包归 provisioning。
  - `sudo-nopasswd` 硬门移除（该门在无免密 sudo 的主机上会整步失败，
    即使无需任何修复）；替换为 warning-only 的 `priv-face` 可观测项。
v1.0.1 相对 v1.0.0（Debian 13 t64 改名兼容）：
  - libglib2.0-0 在 Debian 13 实装名为 libglib2.0-0t64；apt 装别名 rc=0
    但按字面名复查永远失败 → v1.0.0 在 .66 生产首跑误报
    "qt-libs: apt install failed rc=0"（Run #234）。探测逻辑升级为
    字面名未命中时追加 t64 变体。
"""

import fcntl
import grp
import json
import os
import pwd
import subprocess
import sys
import time

_PROGRESS_PREFIX = "PROGRESS "
_LOCK_PATH = "/tmp/stp-flash-preflight.lock"
_UDEV_RULE_PATH = "/etc/udev/rules.d/98-ttyacm-mtk.rules"
_UDEV_RULE_LINE = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"\n'
)
_DEFAULT_PACKAGES = (
    "libice6", "libsm6", "libxrender1",
    "libfontconfig1", "libglib2.0-0",
)
_DEFAULT_FLASHTOOL_REL = (
    "..", "..", "..", "resources", "flashtool",
    "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100",
)


def _progress_stamp(seq: int, **fields) -> str:
    payload = {"seq": seq, "step": "flash_preflight", **fields}
    return _PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)


def _emit_progress(seq: "list[int]", **fields) -> None:
    seq[0] += 1
    sys.stderr.write(_progress_stamp(seq[0], **fields) + "\n")
    sys.stderr.flush()


def _step_params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _as_bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def subprocess_run(argv, **kwargs):
    """间接层：单测可注入。"""
    return subprocess.run(argv, **kwargs)


_PRIV_WRAPPER = "/usr/local/sbin/stp-agent-priv"


def _priv_run(subcommand: str, *extra: str) -> "tuple[int, str]":
    """经提权 wrapper 执行固定子命令（ADR-0037 边界面）；返回 (rc, 输出尾)。"""
    try:
        proc = subprocess_run(
            ["sudo", "-n", _PRIV_WRAPPER, subcommand, *extra],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()[-300:]


def _priv_wrapper_present() -> bool:
    """wrapper 本体是否在位且可执行（迁移期可观测性用）。"""
    return os.access(_PRIV_WRAPPER, os.X_OK)


def _priv_capable(subcommand: str) -> bool:
    """wrapper 窄面能力探针（真实 argv 形态；旧 wrapper/无 wrapper → False）。

    #2011 教训：`--help` 单独探针抓不到「探针过、真调用挂」，故探针携带
    子命令本身；调用侧失败一律带回执（rc + 输出尾）。
    """
    return _priv_run(subcommand, "--help")[0] == 0


def _user_in_dialout() -> bool:
    """当前进程组集合是否覆盖 dialout（本次运行的即时能力）。"""
    try:
        entry = grp.getgrnam("dialout")
        return entry.gr_gid in os.getgroups()
    except (KeyError, OSError, ValueError):
        return False


def _user_dialout_persistent(user: str) -> bool:
    """用户是否**持久**在 dialout（/etc/group 成员表或主组）。

    旧版读进程组集合：usermod 之后本进程组集合不热更新，导致每次运行都
    重报 fixed 的假修复（生产 36/36 次，2026-09-12 #2133 取证）。
    """
    try:
        entry = grp.getgrnam("dialout")
    except (KeyError, OSError, ValueError):
        return False
    if user in entry.gr_mem:
        return True
    try:
        return pwd.getpwnam(user).pw_gid == entry.gr_gid
    except (KeyError, OSError):
        return False


def _udev_rule_ok(rules_dir: str) -> bool:
    """一行同时含 0666 且命中 (ATTR|ATTRS){idVendor}=="0e8d" 即算成立。"""
    try:
        names = sorted(n for n in os.listdir(rules_dir) if n.endswith(".rules"))
    except OSError:
        return False
    for name in names:
        try:
            with open(os.path.join(rules_dir, name),
                      encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        for line in content.splitlines():
            low = line.lower().replace(" ", "")
            if "0666" in low and (
                'idvendor}=="0e8d"' in low or 'idvendor=="0e8d"' in low
            ):
                return True
    return False


def _dpkg_status(package: str) -> "str | None":
    """dpkg-query 状态行；None=非 dpkg 系统或查询失败。"""
    try:
        proc = subprocess_run(
            ["dpkg-query", "-W", "-f=${Status}", package],
            capture_output=True, text=True, timeout=20,
        )
    except Exception:
        return None
    return (proc.stdout or "").strip() or None


def _check_dpkg_installed(package: str) -> "bool | None":
    """True=已安装 False=未安装 None=无法判定（非 dpkg 系统）。

    Debian 13 t64 改名兼容：libglib2.0-0 实测实装为 libglib2.0-0t64——
    apt 装别名 rc=0 但按字面名复查永远失败（v1.0.0 在 .66 生产首跑踩中，
    Run #234）。字面名未命中时追加 t64 变体探测。
    """
    status = _dpkg_status(package)
    if status == "install ok installed":
        return True
    if package.endswith("-0"):
        alt = _dpkg_status(package + "t64")
        if alt == "install ok installed":
            return True
        if alt is not None:
            return False
    # 字面名查询成功但未安装 → 确定缺失；查询失败且无 t64 → 无法判定
    return False if status is not None else None


def _locate_flashtool() -> "str | None":
    base = os.path.dirname(os.path.abspath(__file__))
    candidate = os.path.normpath(os.path.join(base, *_DEFAULT_FLASHTOOL_REL))
    exe = os.path.join(candidate, "flash_tool")
    if os.path.isfile(exe):
        return exe
    return None


def _acquire_lock(seq: "list[int]", timeout: int = 60) -> int:
    """O_NOFOLLOW 打开锁文件——防本地用户预建 symlink 指向可写文件被截断
    （PR-Agent #468 安全评审发现）。返回裸 fd，调用方负责 UNLOCK+close。
    注：flash_firmware 的同款 open(...,"w") 模式因版本目录已冻结不能在本
    PR 修改，已记入 v1.3.6 候补清单。"""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fd = os.open(_LOCK_PATH,
                         os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError:
            pass
        if time.monotonic() >= deadline:
            raise TimeoutError(f"preflight lock busy >{timeout}s")
        waited = round(timeout - (deadline - time.monotonic()), 1)
        _emit_progress(seq, stage="lock-wait", waited_seconds=waited)
        time.sleep(5)


def main() -> None:
    args = _step_params()
    started_at = time.time()
    seq: "list[int]" = [0]
    fix_mode = _as_bool(args.get("fix"), default=True)
    skip_apt = _as_bool(args.get("skip_apt"), default=False)
    import getpass
    user = str(args.get("dialout_user") or getpass.getuser())

    metrics: dict = {"items": [], "warnings": [], "fixed": [], "commands": []}

    lock_fd = None
    try:
        lock_fd = _acquire_lock(seq)
    except TimeoutError as exc:
        _output(False, error_message=str(exc), metrics=metrics)
        return

    def item(name: str, ok: bool, *, fixed: bool = False,
             detail: str = "", warn: bool = False) -> dict:
        rec = {"check": name, "ok": ok, "fixed": fixed, "detail": detail}
        metrics["items"].append(rec)
        if fixed:
            metrics["fixed"].append(name)
        if warn and detail:
            metrics["warnings"].append(detail)
        _emit_progress(seq, stage=name.replace("_", "-"),
                       ok=ok, fixed=fixed)
        return rec

    # ── ① Qt 运行库 ──────────────────────────────────────────────────
    missing = [p for p in _DEFAULT_PACKAGES
               if _check_dpkg_installed(p) is False]
    if not missing:
        unknown = [p for p in _DEFAULT_PACKAGES
                   if _check_dpkg_installed(p) is None]
        item("qt-libs", True,
             detail=("non-dpkg system, skipped: " + ",".join(unknown))
             if unknown else "")
    elif skip_apt:
        detail = (f"missing: {','.join(missing)} "
                  "(skip_apt=true, 记 warning 不判失败)")
        metrics["warnings"].append(detail)
        item("qt-libs", True, detail=detail)
    else:
        # v1.0.2：运行期不再安装（ADR-0037 D5 把装包归 provisioning）。
        item("qt-libs", False, detail=(
            f"missing: {','.join(missing)}；运行期不再安装（ADR-0037 D5，"
            "apt 归 provisioning/install 链）——补包后重跑本步"))

    # ── ② flash_tool 在位 + 执行位 ───────────────────────────────────
    exe = _locate_flashtool()
    if exe is None:
        item("flashtool", False,
             detail="flash_tool not found under resources/flashtool/"
                    "(该资源不经 git 分发——新装机需按装机手册放置)")
    elif os.access(exe, os.X_OK):
        item("flashtool", True)
    elif fix_mode:
        try:
            os.chmod(exe, 0o755)
        except OSError as exc:
            item("flashtool", False, detail=f"chmod failed: {exc}")
        else:
            item("flashtool", os.access(exe, os.X_OK), fixed=True,
                 detail="chmod +x applied")
    else:
        item("flashtool", False, detail="not executable (fix=false)")

    # ── ③ udev 规则（缺则经 wrapper 窄面修复；先于 dialout——dialout 的
    #     降级判定要读修复后的状态）────────────────────────────────────
    rules_dir = str(args.get("udev_rules_dir") or "/etc/udev/rules.d")
    if _udev_rule_ok(rules_dir):
        item("udev-rule", True)
    elif not fix_mode:
        item("udev-rule", False, detail="no 0e8d 0666 rule (fix=false)")
    elif not _priv_capable("ensure-udev-rule"):
        item("udev-rule", False, detail=(
            "no 0e8d 0666 rule; stp-agent-priv lacks ensure-udev-rule "
            "(outdated/missing wrapper) — run "
            "tools/ansible/playbooks/update_agent.yml on this host, then retry"))
    else:
        rc, tail = _priv_run("ensure-udev-rule")
        metrics["commands"].append({"name": "ensure-udev-rule", "rc": rc})
        now_ok = _udev_rule_ok(rules_dir)
        item("udev-rule", now_ok, fixed=now_ok,
             detail="" if now_ok else f"ensure-udev-rule rc={rc}: {tail}")

    # ── ④ dialout 组（持久成员判定；运行期不 usermod）──────────────────
    persistent = _user_dialout_persistent(user)
    in_process = _user_in_dialout()
    rule_ok = _udev_rule_ok(rules_dir)
    if persistent and in_process:
        item("dialout-group", True)
    elif persistent:
        item("dialout-group", True,
             detail=(f"{user} 已是 dialout 持久成员；当前进程组集合未刷新"
                     "（pending_relogin）——重启 agent 后生效"),
             warn=True)
    elif in_process:
        item("dialout-group", True, detail=(
            f"当前进程在 dialout 但持久成员表未见 {user}——建议 provisioning 补组"))
    elif rule_ok:
        item("dialout-group", True, detail=(
            f"{user} 不在 dialout；ttyACM 访问由 udev 0666 规则放行"
            "（建议 provisioning 补组）"), warn=True)
    else:
        item("dialout-group", False, detail=(
            f"{user} 不在 dialout 且无 0666 规则——ttyACM 访问不可用；"
            "运行期不再 usermod（归 provisioning/install 链）"))

    # ── ⑤ 提权窄面可观测性（warning-only；迁移期看收敛进度）───────────
    wrapper_present = _priv_wrapper_present()
    if wrapper_present and _priv_capable("ensure-udev-rule"):
        item("priv-face", True, detail="wrapper=ok; ensure-udev-rule=ok")
    elif not wrapper_present:
        item("priv-face", True, warn=True,
             detail="stp-agent-priv 缺失或不可执行（update_agent.yml 未跑）")
    else:
        item("priv-face", True, warn=True,
             detail=("stp-agent-priv 在位但无 ensure-udev-rule"
                     "（旧版本，待 update_agent.yml 铺开）"))

    # ── ⑥ NFS 固件指针可达 ──────────────────────────────────────────
    nfs_root = (os.environ.get("STP_NFS_ROOT")
                or os.environ.get("STP_AEE_NFS_ROOT")
                or "/mnt/stp-aee")
    latest = os.path.join(nfs_root, "firmware", "MLD", "latest.json")
    readable = False
    try:
        with open(latest, encoding="utf-8") as handle:
            json.load(handle)
        readable = True
    except FileNotFoundError:
        detail = f"missing: {latest}"
    except (OSError, ValueError) as exc:
        detail = f"unreadable: {latest} ({type(exc).__name__})"
    item("nfs-firmware-pointer", readable,
         detail=detail if not readable else "")

    # ── 汇总 ────────────────────────────────────────────────────────
    if lock_fd is not None:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)

    metrics["duration_seconds"] = round(time.time() - started_at, 2)
    failed = [it for it in metrics["items"] if not it["ok"]]
    if failed:
        _output(False, error_message=(
            "preflight failed: " + "; ".join(
                f"{it['check']}: {it['detail']}" for it in failed)),
            metrics=metrics)
        return
    metrics["success_summary"] = {
        "fixed": len(metrics["fixed"]),
        "warnings": len(metrics["warnings"]),
    }
    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
