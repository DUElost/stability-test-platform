"""Flash-fleet host preflight — idempotent ensure for MLD 刷机前置条件.

编排位：Plan 中固定排在 flash_firmware 之前（每设备 job 各执行一次，
同 host 并发由 /tmp flock 去重）。一次性运维型步骤：修完即过，host 层
系统状态在两次派发之间不会自行变化。

逐项检查→缺则自动修复（fix=true 默认）：
    qt-libs      Debian 包 ×5（dpkg 断言；缺则 apt-get install）
    flashtool    资源目录入口存在且可执行（缺执行位则 chmod）
    dialout      当前用户在该组（缺则 usermod -aG；**本进程组集合不会
                 热更新**——记 pending_relogin 警告，重启 agent 后生效）
    udev-rule    0e8d ttyACM MODE=0666 规则在位（缺则写规则文件+reload）
    sudo         `sudo -n true` 可用性（不可用时其余修复项无法执行，
                 如实失败并列出）
    nfs          {STP_NFS_ROOT}/firmware/{family}/latest.json 可读
                 （mount/网络问题超出本脚本职责，如实失败）

输出契约与其余平台脚本一致：stdout 单行 JSON success/metrics，
stderr PROGRESS 戳。fix=false 时只检查不修复。

Environment:
    STP_NFS_ROOT           (fallback STP_AEE_NFS_ROOT → /mnt/stp-aee)
STP_STEP_PARAMS schema:
    fix                    : bool (optional, default true; false = 只检不修)
    skip_apt               : bool (optional, default false; 无外网/镜像的
                           host 跳过包类检查-修复，改记 warning)
    locales                : 预留，无副作用（与 oobe_skip 对齐命名习惯）
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
import shlex
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


def _sudo_sh(command: str) -> "tuple[int, str]":
    """sudo -n sh -c <cmd>；返回 (rc, 输出尾)。"""
    try:
        proc = subprocess_run(
            ["sudo", "-n", "sh", "-c", command],
            capture_output=True, text=True, timeout=120,
        )
    except Exception as exc:
        return -1, f"{type(exc).__name__}: {exc}"
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or "")).strip()[-300:]


def _user_in_dialout() -> bool:
    try:
        entry = grp.getgrnam("dialout")
        return entry.gr_gid in os.getgroups()
    except (KeyError, OSError, ValueError):
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
    elif not fix_mode or skip_apt:
        detail = f"missing: {','.join(missing)}"
        ok = False
        if skip_apt:
            detail += " (skip_apt=true, 记 warning 不判失败)"
            ok = True
            metrics["warnings"].append(detail)
        item("qt-libs", ok, detail=detail)
    else:
        rc, tail = _sudo_sh(
            "DEBIAN_FRONTEND=noninteractive apt-get install -y "
            "--no-install-recommends " + " ".join(missing))
        metrics["commands"].append({"name": "apt-install", "rc": rc})
        still = [p for p in missing if _check_dpkg_installed(p) is False]
        item("qt-libs", not still,
             fixed=not still, detail="installed" if not still else
             f"apt install failed rc={rc}: {tail}")

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

    # ── ③ dialout 组 ────────────────────────────────────────────────
    if _user_in_dialout():
        item("dialout-group", True)
    elif fix_mode:
        rc, tail = _sudo_sh(f"usermod -aG dialout {shlex.quote(user)}")
        now_ok = _user_in_dialout()
        if now_ok:
            item("dialout-group", True, fixed=True)
        else:
            note = (f"usermod rc={rc}; 已追加 {user}@dialout，但当前进程组"
                    f"集合需 agent 重启后生效（pending_relogin）。tail: {tail}"
                    ) if rc == 0 else f"usermod rc={rc}: {tail}"
            item("dialout-group", rc == 0, fixed=(rc == 0),
                 detail=note, warn=note)
    else:
        item("dialout-group", False, detail="user not in dialout (fix=false)")

    # ── ④ udev 规则 ─────────────────────────────────────────────────
    rules_dir = str(args.get("udev_rules_dir") or "/etc/udev/rules.d")
    if _udev_rule_ok(rules_dir):
        item("udev-rule", True)
    elif fix_mode:
        rc, tail = _sudo_sh(
            f"mkdir -p {shlex.quote(rules_dir)} && "
            f"printf '%s\\n' {shlex.quote(_UDEV_RULE_LINE.strip())} > "
            f"{shlex.quote(_UDEV_RULE_PATH)}"
            f" && chmod 644 {shlex.quote(_UDEV_RULE_PATH)}"
            " && udevadm control --reload"
            " && udevadm trigger 2>/dev/null || true"
        )
        now_ok = _udev_rule_ok(rules_dir)
        item("udev-rule", now_ok, fixed=now_ok,
             detail="" if now_ok else f"rule write rc={rc}: {tail}")
    else:
        item("udev-rule", False, detail="no 0e8d 0666 rule (fix=false)")

    # ── ⑤ sudo 可用性（决定其它修复项的可信度）──────────────────────
    sudo_rc, _ = _sudo_sh("true")
    sudo_ok = sudo_rc == 0
    need_sudo_note = any(
        it["check"] in ("qt-libs", "dialout-group", "udev-rule")
        and it.get("fixed") for it in metrics["items"])
    item("sudo-nopasswd", sudo_ok,
         detail="" if sudo_ok else
         ("android 用户无免密 sudo——以上修复项未真正落盘"
          if need_sudo_note else "无免密 sudo（本轮无需修复项，仅提示）"))

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
