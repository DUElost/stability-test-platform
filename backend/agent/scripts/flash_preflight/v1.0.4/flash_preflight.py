"""Flash-fleet host preflight — idempotent ensure for MLD 刷机前置条件.

编排位：Plan 中固定排在 flash_firmware 之前（每设备 job 各执行一次，
同 host 并发由 /tmp flock 去重）。一次性运维型步骤：修完即过，host 层
系统状态在两次派发之间不会自行变化。

逐项检查（v1.0.4：dialout 项改判「该用户能不能写」；v1.0.3：udev 规则判据改
**语义双形态**；v1.0.2：不再用 `sudo -n sh -c`）：
    qt-libs      Debian 包 ×5（dpkg 断言）。缺包 → 明确失败 + 指引
                 （apt 安装归 provisioning/install 链，运行期不装包）
    flashtool    资源目录入口存在且可执行（缺执行位则 chmod；属主即 agent
                 用户，无需提权）
    dialout      当前用户在**本次运行时**能否写 ttyACM = 进程组集合含 dialout
                 （`/etc/group` 的持久成员只用于给建议），或规则是旧形态 0666
                 （对任何本地用户放行，记 warning）。0660 规则只对 dialout
                 成员放行 → 进程组集合不含 dialout 即**判否**（v1.0.4 起；
                 v1.0.3 拿「规则在位」当可写证据 → 假通过，#2353）。
                 运行期不 usermod（归 provisioning/install 链）
    udev-rule    0e8d ttyACM 规则在位，**两形态皆算成立**（#2284）：
                 `GROUP="dialout", MODE="0660"`（新形态，最小权限）或
                 `MODE="0666"`（旧形态，兼容未升级主机）；缺则调 wrapper
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
v1.0.4 相对 v1.0.3（#2353：可写性判据与「规则在位」解耦）：
  - 新增 `_udev_rule_form()` 返回在位形态（`0666` / `0660` / 无），
    `_udev_rule_ok()` 改由它派生——udev-rule 项与 wrapper 修复流语义不变。
  - dialout 项改判「当前进程能不能写」：0660 形态只对 dialout 成员放行，
    进程组集合不含 dialout 即判否（含「持久成员已补齐但服务未重启」这一
    情形，指引=重启 agent）；0666 形态仍算放行并记 warning 提示收敛形态。
    条目名保持 `dialout-group`（下游 step_trace/展示按名消费）。
v1.0.3 相对 v1.0.2（#2284 / ADR-0037 D5 最小权限方向）：
  - udev 规则判据由「一行含 `0666`」改为**语义双形态**：`GROUP="dialout",
    MODE="0660"`（新形态——dialout 成员可写即可，其它本地用户不再可写）
    与 `MODE="0666"`（旧形态）都算成立。车队升级是渐进的，若只认新形态，
    未升级主机（仍写旧规则）会在 preflight 判缺失 → 调 wrapper 重写同样
    内容 → 仍失败 → **刷机被阻断**；故必须双形态并存一个收敛周期。
  - wrapper `ensure-udev-rule` 与 install/update 链同步改为「有 dialout 组
    写 0660 + GROUP，无该组才退化 0666」（内容仍为两个固定形态之一，无
    调用方参数面）。
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
#: 新形态（#2284）：dialout 成员可写即可 —— 0666 会把 MTK 串口开放给任何本地用户。
_UDEV_RULE_LINE = (
    'KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", GROUP="dialout", MODE="0660"\n'
)
#: 旧形态：兼容尚未升级的安装器/playbook（判据双形态并存一个收敛周期）。
_UDEV_RULE_LINE_LEGACY = (
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


def _udev_rule_form(rules_dir: str) -> "str | None":
    """在位的 ttyACM 0e8d 规则形态：`"0666"` / `"0660"` / `None`（#2353）。

    两种形态**放行面不同**：`"0666"` 对任何本地用户放行；`"0660"` 只对
    `GROUP="dialout"` 成员放行。故「规则在位」不能当作「当前用户可写」的
    证据——v1.0.3 混用两者，使 `dialout-group` 项在「0660 规则 + 用户不在
    dialout」时假通过（#2353）。
    """
    try:
        names = sorted(n for n in os.listdir(rules_dir) if n.endswith(".rules"))
    except OSError:
        return None
    for name in names:
        try:
            with open(os.path.join(rules_dir, name),
                      encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        for line in content.splitlines():
            low = line.lower().replace(" ", "")
            if 'idvendor}=="0e8d"' not in low and 'idvendor=="0e8d"' not in low:
                continue
            if "0660" in low and "dialout" in low:
                return "0660"
            if "0666" in low:
                return "0666"
    return None


def _udev_rule_ok(rules_dir: str) -> bool:
    """ttyACM 访问规则在位——**两种形态都算成立**（#2284）。

    判定式为「命中 (ATTR|ATTRS){idVendor}=="0e8d" 且满足其一」：

    - 新形态 `GROUP="dialout", MODE="0660"`：dialout 成员（Agent 用户已加入）
      可写，其它本地用户不可写——最小权限；
    - 旧形态 `MODE="0666"`：未升级主机仍在用。

    为什么必须双形态：车队升级是渐进的。若只认新形态，未升级主机（规则仍是
    0666）会被判缺失 → 调 wrapper `ensure-udev-rule` 重写（wrapper 也是新
    形态）→ 复查通过其实会成立；但若 wrapper 尚未升级（仍写 0666），第二轮
    复查仍失败 → **preflight 卡刷机**。双形态并存一个收敛周期即可消除该窗口。

    本函数只回答「规则是否在位」；「当前用户能不能写」由 `_udev_rule_form()`
    在 dialout 项里另行判定（#2353：混用两者会假通过）。
    """
    return _udev_rule_form(rules_dir) is not None


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
        item("udev-rule", False, detail="no ttyACM 0e8d rule (fix=false)")
    elif not _priv_capable("ensure-udev-rule"):
        item("udev-rule", False, detail=(
            "no ttyACM 0e8d rule; stp-agent-priv lacks ensure-udev-rule "
            "(outdated/missing wrapper) — run "
            "tools/ansible/playbooks/update_agent.yml on this host, then retry"))
    else:
        rc, tail = _priv_run("ensure-udev-rule")
        metrics["commands"].append({"name": "ensure-udev-rule", "rc": rc})
        now_ok = _udev_rule_ok(rules_dir)
        item("udev-rule", now_ok, fixed=now_ok,
             detail="" if now_ok else f"ensure-udev-rule rc={rc}: {tail}")

    # ── ④ ttyACM 可写性（规则形态 × 当前进程组集合；运行期不 usermod）──
    persistent = _user_dialout_persistent(user)
    in_process = _user_in_dialout()
    rule_form = _udev_rule_form(rules_dir)
    if in_process:
        item("dialout-group", True, detail=(
            "" if persistent else
            f"当前进程在 dialout 但持久成员表未见 {user}"
            "——重启后失效，建议 provisioning 补组"))
    elif rule_form == "0666":
        item("dialout-group", True, detail=(
            f"{user} 不在 dialout；ttyACM 访问由旧形态 udev MODE=0666 规则放行"
            "（任何本地用户可写——建议 provisioning 补组并收敛规则形态）"),
            warn=True)
    elif rule_form == "0660":
        # 0660 只对 dialout 成员放行：当前进程组集合不含 dialout 就是写不了串口，
        # 必须判否（v1.0.3 拿「规则在位」当可写证据 → 假通过，#2353）
        item("dialout-group", False, detail=(
            f"{user} 当前进程组集合不含 dialout，而 0660 规则只对 dialout "
            "成员放行——ttyACM 不可写；" + (
                "持久成员已补齐，重启 agent 后生效"
                if persistent else "provisioning 补组后重启 agent")))
    else:
        item("dialout-group", False, detail=(
            f"{user} 不在 dialout 且无 ttyACM 0e8d 规则——访问不可用；"
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
