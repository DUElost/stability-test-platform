"""Flash firmware via SP Flash Tool (MTK platform).

Environment:
    STP_NFS_ROOT         (prepended to relative firmware_dir; firmware 根默认
                          取 {STP_NFS_ROOT}/firmware)
    STP_STEP_PARAMS      (required JSON)
    STP_FLASH_TOOL_DIR   (optional override for flash_tool location)
    STP_JOB_ID           (used to tag metrics)
    STP_DEVICE_SERIAL    (fingerprint 路由与刷前/刷后版本比对用)
    STP_ADB_PATH         (default adb)

STP_STEP_PARAMS schema:
    firmware_dir            : str  (显式固件目录；缺省走指纹路由，见下)
    da_file                 : str  (显式 DA 文件；可由 manifest.json 提供)
    scatter_file            : str  (显式 scatter 文件；可由 manifest.json 提供)
    command                 : str  (optional, default firmware-upgrade)
    boot_mode               : str  (optional, default auto)
    timeout_seconds         : int  (optional, default 1200)
    flash_tool_dir          : str  (optional; overrides STP_FLASH_TOOL_DIR)
    reboot_to_flash         : bool (optional, default true; adb reboot before flash_tool)
    reboot_target           : str  (optional, default "normal"; "normal" 发不带 target 的
                          adb reboot——完整上电流经 BROM 窗口，工具才能抓中；
                          "bootloader"/"fastboot" 为显式选项，热重启直达专用
                          模式会跳过 BROM（v1.3.2 真机实证）)
    pre_reboot_wait_seconds : int  (optional, default 5; v1.3.3 起语义为工具
                          进入 USB 扫描后、发 adb reboot 前的提前量)
    firmware_root           : str  (optional; env STP_FLASH_FIRMWARE_ROOT；
                          默认 {STP_NFS_ROOT}/firmware)
    version                 : str  (optional; env STP_FLASH_FIRMWARE_VERSION；
                          缺省读 {root}/{family}/latest.json)
    family                  : str  (optional; 显式指定机型族，缺省按指纹路由)
    skip_if_current         : bool (optional, default true; env STP_FLASH_SKIP_IF_CURRENT；
                          刷前 getprop 比对，已是目标版本则 skipped 收场)
    verify_version          : bool (optional, default true; env STP_FLASH_VERIFY_VERSION；
                          刷后回读版本核验，不一致判失败)
    verify_wait_seconds     : int  (optional, default 300; 刷后等设备回到 adb 的上限)
    boot_stabilize_seconds  : int  (optional, default 20; 刷后 boot_completed=1 且 USB
                          拓扑稳定的持续窗口——首刷二次重启窗口在锁内消化)
    boot_stabilize_max_wait : int  (optional, default 120; boot 稳定等待上限,超时按
                          「设备确认卡死」语义放行,不判失败)
    gate_other_mtk          : bool (optional, default true; env STP_FLASH_GATE_OTHER_MTK；
                          隐藏同 host 其它处于刷机态的 MTK 口（authorized=0），刷完恢复。
                          只动非目标口；普通态(pid=2046)手机不受影响)
    max_attempts            : int  (optional, default 2, 1..4; env STP_FLASH_MAX_ATTEMPTS；
                          整链路尝试次数：每次 = 重启 + 启动 flash_tool + 等结果)
    retry_backoff_seconds   : int  (optional, default 10; env STP_FLASH_RETRY_BACKOFF；
                          相邻两次尝试之间的间隔)
    strict_env_check        : bool (optional, default false; env STP_FLASH_STRICT_ENV_CHECK；
                          true 时把 ttyACM 写入路径不明确从 WARNING 升级为失败)

v1.3.6 相对 v1.3.5（per-model 版本映射）：
  - 族级指针 latest.json 支持机型级版本：{"versions": {"MLD_LX2": "...",
    "MLD_LX3": "..."}}，按 getprop model 取版本；旧 {"version": "..."} 单键
    兼容回落。机型键匹配支持下划线/连字符互转（双拼写现场教训）。
  - 背景：同族多机型多固件并存时（LX2 V62 / LX3 V71），单键指针只能指
    一个版本——换版本需切指针+错峰派发（2026-08-28 LX2 批量验证的补丁
    形态）。本版使 LX2/LX3 可并行路由，无需新 Plan/切指针。
  - 本机型无 versions 键 → fail-fast，错误信息指引补键。

v1.3.8 相对 v1.3.7（门控保持——BROM 幽灵重枚举逃逸，2026-08-30 实证）：
  - **一次性门控可被重枚举逃逸**。.68 存在常驻 BROM 幽灵设备（1-7.4.4，
    serial 空 pid 2000）：authorized=0 只对当前 USB 实例生效，设备周期性
    重枚举后新实例回到 auth=1——工具扫描/下载期间抓到幽灵，3.3G 固件写进
    它（BROM 态不变），目标设备空等 → verify mismatch。dmesg 佐证：
    run 260 目标 BROM 窗口仅 3s（正常重启流程快速经过），幽灵 2000 常驻
    （我手动禁用 10 分钟后它随 run 260 门控重枚举回归）。
  - 修复：**门控保持**——工具运行期间周期重写非目标刷机态口
    authorized=0：on_running（adb reboot 前）一次 + on_percent 每 10s
    节流一次，直到下载完成。重试轮既有 regate 保留。
  - metrics.gating 新增 `regate_count`（压制轮次,诊断用）。

v1.3.7 相对 v1.3.6（锁内 boot 稳定等待，2026-08-30 run 258 实证）：
  - **verify 通过 ≠ 设备完成首次开机**。run 258（.68 串行 14 台）11 台
    「Download Succeeded 但目标设备未变」：verify 只等 adb 回归 + 版本一致，
    而 HONOR 首刷后 boot 完成后会再重启一次（初始化），重启窗口期以
    BROM/preloader 可捕获态暴露（新 USB 实例 authorized=1,门控失效），
    下一任持锁者的工具扫描撞上它,把新固件刷进上一台。v1.3.4 持锁穿过
    verify 只覆盖 verify 期间；本版在 verify 通过后**继续持锁**等
    boot_completed=1 且 USB 拓扑指纹稳定 boot_stabilize_seconds（设备
    不再重启）才释放锁。失败/卡死路径保持 v1.3.4 兜底语义（确认卡死
    即放行——卡死设备不会重启,无窗口）。
  - 新增 PROGRESS 阶段 `boot-stabilize`；metrics 新增顶层键 `boot_stable`
    （boot_completed / stable_seconds_elapsed / ok / reason）。

v1.3.5 相对 v1.3.4（核验预算上调）：
  - verify_wait_seconds 默认 180 → **300**。`.87` hub 树三台次实测：刷后
    启动回归普遍超过 180s（046 在预算耗尽后自行回归、166/193 停 OOBE 需
    人工），而 `.66` 从未超过 ~105s——上调对快 host 无损、对慢 host 是
    把「刷写成功却判失败」修正为「多等一会儿拿真结论」。

v1.3.4 相对 v1.3.3（锁语义真机修正）：
  - **持锁穿过 re-enumerate 与 verify**。场景 2 实证（2026-08-26 .66 双机
    并发）：旧顺序在工具退出后立即释放锁，本机手机的看门狗重启发生在锁外，
    下一任持锁者的工具扫描窗撞上这个可捕获态，把新固件刷进了错误的手机
    （B 核验失败拦截、A 被重复刷写一遍）。改为成功路径在 verify 完成、
    自己的手机稳定后才交出锁；失败与异常路径立即结算保持原兜底语义。
  - 新增 PROGRESS 阶段 `lock-released`；metrics.gating.restore 契约不变。

v1.3.3 相对 v1.3.2（reboot 时序真机修正）：
  - **工具先启动、reboot 后发**。172.21.15.66 实证：脚本旧顺序（reboot →
    睡 5s → 才起 flash_tool）下，BROM 窗口在上电最初几秒即关闭，工具启动时
    只能通过 udev add@ 事件看到普通态(2046)枚举、两轮 S_TIMEOUT(1042)；
    同机对照「工具先扫描 → 再 reboot」秒抓 BROM 并 Download Succeeded。
    Agent Note Decision 流程图的原序（工具在前）才是正确时序，v1.2.0 遗留
    的实现顺序与之相悖且从未被真机验证。
  - 实现：_run_flash_tool_with_progress 新增 on_running 回调（Popen 与
    reader 线程就绪后、进入轮询前触发一次），adb reboot 移入该回调；
    pre_reboot_wait_seconds 语义变为「工具起扫后的提前量」（缺省 5s，
    手工成功样本为 8s）。

v1.3.2 相对 v1.3.1（reboot 语义真机修正）：
  - **默认 reboot_target 从 "bootloader" 改为 "normal"**。172.21.15.66 对照
    实验（2026-08-25）：`adb reboot bootloader` 热重启直达 HONOR
    fastboot/download 态(pid 201c)，跳过 BROM → 等待中的 flash_tool 两轮
    S_TIMEOUT(1042)；不带 target 的普通 `adb reboot` 走完整上电、流经
    BROM(2000) 窗口 → 工具秒抓并 Download Succeeded @36MB/s。该参数自
    v1.2.0 引入以来从未被真机验证，今日首验即证伪。
  - `"normal"` / `""` / 缺省 → 发不带 target 的 `adb reboot`；
    "bootloader"/"fastboot" 保留为显式选项。

v1.3.1 相对 v1.3.0（真机回归发现的路由表缺口）：
  - getprop ro.product.model 实测返回**连字符**型号（MLD-LX3，172.21.15.66
    回归实测；adb devices 的 model 字段显示下划线，两者不是同一来源），
    路由表原只有下划线键 → 默认参数无法路由、必须显式传 family。
    补齐连字符键（MLD-LX2/LX3、ELA-LX2/LX3），下划线键保留——不同批次
    固件两种拼写可能并存。manifest models 白名单与路由表无关，不受影响。

v1.3.0 相对 v1.2.0（多设备消歧 · 门控 / 重试环 / 环境预检）：
  - **环境预检**：启动 flash_tool 前一次性检查——可执行位、动态库完整性
    （ldd "not found" 扫描）、adb 可用性、ttyACM 写入路径（dialout 组或
    udev MODE=0666 规则，二者皆无 → WARNING，strict_env_check 时升级为失败）。
    硬失败项在拿锁之前短路，错误信息直接给修复动作
    （15.66 实测：缺 dialout 组时症状是刷机中途 STATUS_ERR，极难定位）。
  - **门控**：以 STP_DEVICE_SERIAL 在 sysfs 反查目标口（必须在 adb reboot
    **之前**做——BROM 态手机 serial 为空），然后把其它所有处于刷机态
    （pid ∈ {0003,2000,2001,201c,2026,3000}）的 MTK 口 authorized=0；
    普通态(2046)手机不受影响、不干扰并行测试。刷完 try/finally 恢复。
    无 sudo / 目标 serial 缺失 / 非 Linux 时跳过并记录原因（单设备 host 无感）。
    背景实验结论（2026-08-24/25，详见 Agent Note
    docs/notes/feature/2026-08-25-mtk-flash-fleet-automation.md）：
    SPFT Linux console 版 `-p` 打开层损坏不可用；工具对启动前已存在的设备
    失明、只附着 BROM(PID 2000)；无-p 模式下"唯一可见设备"由门控保证。
  - **重试环**：整链路按 max_attempts 循环（重启+刷写各算一次尝试），
    attempt>1 打 stage="retry" 戳并退避。SPFT 的 USB 搜索窗口只有 120s 且
    对 remove@ uevent 敏感，偶发抓取超时/下载中死亡靠重试承接；
    PlanStep 层重试粒度太粗（整步重来会重复路由/预检），故内建。
  - 兼容性：v1.2.0 的参数、输出契约（success/skipped/metrics.*）、PROGRESS
    戳语义全部保留；metrics 新增 env_precheck / gating / attempts /
    attempt_count 字段，原字段含义不变（exit_code/stdout_tail 等取最后一次尝试）。

v1.2.0 相对 v1.1.0（Honor 刷机自动化 · 方向 A）：
  - **固件指纹路由**（ADR-0029 v2「执行差异归脚本路由」先例）：firmware_dir
    缺省时按 `getprop ro.product.model` 路由到
    `{firmware_root}/{family}/{version}/`，family 由 `_MODEL_FAMILY_ROUTES`
    映射（MLD_LX2/LX3→MLD，ELA_LX2/LX3→ELA），version 取
    `STP_FLASH_FIRMWARE_VERSION` env 或 `{family}/latest.json` 指针文件。
    路由决策（decided_by/model/family/version/manifest 版本）全部写进
    metrics.route，step_trace 可审计；机型不在路由表 → fail-fast。
  - **manifest.json 固件清单**：每版本目录一份（family/version/version_prop/
    scatter_file/da_file/models），路由模式必读，显式 firmware_dir 模式存在
    则用于补缺 da/scatter 与提供比对版本。
  - **刷前版本比对**：目标版本与 `getprop {version_prop}`（默认
    ro.build.version.incremental）相同 → `skipped:true` 不刷。adb 不可达
    （设备已在 BROM 等）不阻断，记录 version_check 后照刷。
  - **刷后版本核验**：刷完等设备回 adb（get-state == device），回读版本与
    manifest 不一致 → 失败（重试可由 PlanStep.retry 承接）。verify 关闭时
    保持 v1.1.0 语义：枚举慢只记录不判败。
  - 参数解析对齐 MTBF P0 先例：`STP_STEP_PARAMS > STP_FLASH_* env > 代码默认`
    （平台 default_params 恒空、逐计划参数通道不存在，ADR-0029 D1 挂起）。

v1.1.0 相对 v1.0.1：**flash 阶段打 PROGRESS 戳**（#115 阶段 2 / #134）。
停滞判据只认 PROGRESS 戳（普通输出不算活），flash 是长耗时且时长不可预估
的步骤，必须自己打戳：
  - adb reboot 进入 flash 模式 → 一戳
  - flash_tool 运行期：解析其 stdout/stderr，识别到新阶段关键字
    （DA handshake / download / format / erase / verify 等）→ seq+1 打戳；
    识别到百分比 → 打 percent 字段戳（粒度更细）
  - 阶段内无输出 = seq 不涨 = **诚实的停滞信号**——阶段静默容忍在
    PlanStep 上配大 stall_seconds（如 600s），不靠重复打印制造"活着"的假象
  - flash_tool 退出后轮询 adb devices 等设备重新枚举（每 5s 一戳），
    最后打 done 戳

注：flash_tool 输出格式以 SP Flash Tool 为准；解析不到任何阶段关键字时，
脚本只在 reboot / done 打戳，长静默阶段依赖 PlanStep 的 stall_seconds 兜底。
"""

import json
import os
import platform
import re
import subprocess
import sys
import threading
import time


_PASS_TOKENS = (
    "All command exec done",
    "All commands are executed successfully",
)
_FAIL_TOKENS = (
    "S_DA_HANDSHAKE_FAILED",
    "S_FT_DOWNLOAD_FAIL",
    "S_NOT_ENOUGH_STORAGE_SPACE",
    "S_FT_FORMAT_FAIL",
    "S_FT_GET_DEV_INFO_FAIL",
    "FAIL",
    "ERROR",
)

_LOCK_PATH = "/tmp/stp-flash-firmware.lock"

# ── v1.3.0 门控 / 环境预检 ───────────────────────────────────────────
#
# sysfs 基址做成参数：单测注入 tmp 树，生产用默认值。
_SYSFS_USB_BASE = "/sys/bus/usb/devices"
_MTK_VENDOR_ID = "0e8d"
# 处于这些 pid 的 MTK 设备被视为"刷机态"——门控只隐藏它们：
#   0003/2000/2001 = MTK BROM/preloader 经典态
#   201c/2026      = HONOR DA / download-mode（本机型实测）
#   3000           = 部分 DA 变体
# 普通态(2046, audio 复合接口)不隐藏——flash_tool 本来就不理会它们，
# 隐藏反而会打断同 host 其它手机的测试任务。
_FLASH_STAGE_PIDS = {"0003", "2000", "2001", "201c", "2026", "3000"}

_sudo_available_cache: "list[bool | None]" = [None]


def _is_linux() -> bool:
    return platform.system() == "Linux"


def _sysfs_read(path: str) -> "str | None":
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read().strip()
    except (OSError, ValueError):
        return None


def _list_mtk_ports(base: str = _SYSFS_USB_BASE) -> "dict[str, dict]":
    """枚举 base 下所有 MTK USB 设备（跳过 :1.x 接口目录与根 hub）。

    返回 {port_name: {"pid": str, "authorized": str}}；读不到的属性留空串。
    """
    ports: dict[str, dict] = {}
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return ports
    for name in names:
        # 只跳过接口目录(含 ':')与根 hub(usbN，无 '-')。带点的名字
        # (如 1-5.2.3)是 hub 树下的合法设备实例，必须参与门控 —— .87 的
        # RTS5411 拓扑里目标与干扰源几乎全挂在 hub 后面。
        if ":" in name or "-" not in name:
            continue
        dev_dir = os.path.join(base, name)
        if _sysfs_read(os.path.join(dev_dir, "idVendor")) != _MTK_VENDOR_ID:
            continue
        ports[name] = {
            "pid": (_sysfs_read(os.path.join(dev_dir, "idProduct")) or "").lower(),
            "authorized": _sysfs_read(os.path.join(dev_dir, "authorized")) or "",
        }
    return ports


def _port_for_serial(serial: str, base: str = _SYSFS_USB_BASE) -> "str | None":
    """按序列号反查 sysfs 端口名。必须在 adb reboot **之前**调用：
    BROM/preloader 态手机 serial 为空，刷机中途查不到。"""
    if not serial:
        return None
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        if ":" in name or not "-" in name:
            continue
        value = _sysfs_read(os.path.join(base, name, "serial"))
        if value == serial:
            return name
    return None


def _sudo_available() -> bool:
    if _sudo_available_cache[0] is None:
        try:
            proc = subprocess.run(
                ["sudo", "-n", "true"], capture_output=True, timeout=10,
            )
            _sudo_available_cache[0] = proc.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _sudo_available_cache[0] = False
    return _sudo_available_cache[0]


def _set_authorized(port: str, value: str, base: str = _SYSFS_USB_BASE) -> "tuple[bool, str]":
    """写 authorized；直写失败回落 sudo -n。返回 (ok, how)。"""
    path = os.path.join(base, port, "authorized")
    try:
        with open(path, "w") as handle:
            handle.write(value)
        return True, "direct"
    except (OSError, ValueError):
        pass
    if not _sudo_available():
        return False, "no-sudo"
    try:
        proc = subprocess.run(
            ["sudo", "-n", "sh", "-c",
             f"echo {value} > {_shlex_quote(path)}"],
            capture_output=True, timeout=10,
        )
        return proc.returncode == 0, "sudo"
    except (OSError, subprocess.TimeoutExpired):
        return False, "sudo-failed"


def _shlex_quote(path: str) -> str:
    import shlex
    return shlex.quote(path)


def _gate_other_mtk(target_port: "str | None", base: str = _SYSFS_USB_BASE) -> dict:
    """隐藏除 target_port 外所有刷机态 MTK 口。返回报告（永不 raise）。"""
    report: dict = {"hidden": [], "errors": {}, "skipped_reason": None,
                    "target_port": target_port}
    if not _is_linux():
        report["skipped_reason"] = "non-linux host"
        return report
    if not target_port:
        report["skipped_reason"] = (
            "target port unknown (serial not found in sysfs or STP_DEVICE_SERIAL "
            "empty); gating skipped to avoid hiding the target itself")
        return report

    ports = _list_mtk_ports(base)
    others = {
        name: info for name, info in ports.items()
        if name != target_port and info["pid"] in _FLASH_STAGE_PIDS
    }
    if not others:
        report["skipped_reason"] = "no other flash-stage MTK devices visible"
        return report

    # 直写能成（root 跑 agent）就不必探测 sudo；探测留给首次失败后。
    for name in sorted(others):
        ok, how = _set_authorized(name, "0", base)
        if ok:
            report["hidden"].append(name)
        else:
            report["errors"][name] = f"authorize-0 failed via {how}"
    if not report["hidden"]:
        report["skipped_reason"] = (
            "cannot write authorized (need root/dialout+udev or passwordless sudo)"
        )
    return report


def _restore_gated(gated: "dict | None", base: str = _SYSFS_USB_BASE) -> dict:
    """把门控隐藏过的口全部恢复 authorized=1。永不 raise。"""
    restored: list = []
    errors: dict = {}
    if not gated:
        return {"restored": [], "errors": {}}
    for name in gated.get("hidden", []):
        ok, how = _set_authorized(name, "1", base)
        if ok:
            restored.append(name)
        else:
            errors[name] = f"authorize-1 failed via {how}"
    return {"restored": restored, "errors": errors}


def _user_in_dialout() -> bool:
    """当前进程的有效 gid 集合是否覆盖 dialout（ttyACM 默认组）。"""
    try:
        import grp
        entry = grp.getgrnam("dialout")
        return entry.gr_gid in os.getgroups()
    except (KeyError, OSError, ValueError):
        return False


def _udev_has_mtk_0666_rule(rules_dir: str = "/etc/udev/rules.d") -> bool:
    """rules_dir 里是否有给 MTK ttyACM 放 0666 的规则。

    逐行匹配：一行同时含 0666 且命中 (ATTR|ATTRS){idVendor}=="0e8d"
    或 KERNEL=="ttyACM* 即算。生产安装的规则形如
    KERNEL=="ttyACM*", ATTRS{idVendor}=="0e8d", MODE="0666"。
    rules_dir 可注入（单测），与 sysfs base 同一约定。
    """
    try:
        files = sorted(
            os.path.join(rules_dir, n) for n in os.listdir(rules_dir)
            if n.endswith(".rules")
        )
    except OSError:
        return False
    for path in files:
        try:
            with open(path, encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            continue
        for line in content.splitlines():
            low = line.lower().replace(" ", "")
            if "0666" not in low:
                continue
            if 'idvendor}=="0e8d"' in low or 'kernel=="ttyacm' in low:
                return True
    return False


def _ttyacm_writable_now() -> "bool | None":
    """/dev/ttyACM* 存在时任一可写 → True；存在但全不可写 → False；
    一个都没有（手机都在正常态）→ None（无法判断，不算失败）。"""
    try:
        nodes = [n for n in os.listdir("/dev") if n.startswith("ttyACM")]
    except OSError:
        return None
    if not nodes:
        return None
    return any(os.access(f"/dev/{n}", os.W_OK) for n in nodes)


def _ldd_missing_libs(exe_path: str, env: dict) -> "list[str]":
    """ldd 扫描缺库清单；非 Linux / 无 ldd 时返回空表（无法判断≠缺失）。"""
    if not _is_linux():
        return []
    try:
        proc = subprocess.run(
            ["ldd", exe_path], capture_output=True, text=True, timeout=30,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    missing = set()
    for line in (proc.stdout or "").splitlines():
        if "not found" in line:
            missing.add(line.strip().split()[0])
    return sorted(missing)


def _precheck_environment(
    flash_tool_exe: str, adb_path: str, need_adb: bool, strict: bool,
) -> "tuple[bool, dict]":
    """环境预检。返回 (hard_ok, report)；WARNING 只记录，strict 时升级。

    - 可执行位缺失 / 缺动态库 → 硬失败（工具必然起不来）
    - adb 缺失且本流程需要 adb → 硬失败
    - ttyACM 写入路径不明 → WARNING（87 上 crw-rw-rw- 也能跑），strict 升级
    """
    items: list[dict] = []

    exe_ok = os.access(flash_tool_exe, os.X_OK)
    items.append({
        "check": "flash-tool-executable",
        "ok": exe_ok,
        "detail": "" if exe_ok else (
            f"not executable: {flash_tool_exe}; fix: chmod +x"),
    })

    env = _build_subprocess_env(os.path.dirname(flash_tool_exe))
    missing = _ldd_missing_libs(flash_tool_exe, env)
    items.append({
        "check": "shared-libs",
        "ok": not missing,
        "detail": "" if not missing else (
            "missing: " + ", ".join(missing)),
    })

    adb_found = bool(adb_path) and (
        os.path.isabs(adb_path) and os.access(adb_path, os.X_OK)
        or _shutil_which(adb_path) is not None
    )
    adb_item = {
        "check": "adb-present",
        "ok": adb_found,
        "detail": "" if adb_found else f"adb not found on PATH: {adb_path}",
        "needed": need_adb,
    }
    items.append(adb_item)

    writable = _ttyacm_writable_now()
    if writable is True:
        tty_detail, tty_ok = "", True
    elif writable is False:
        tty_detail = "/dev/ttyACM* present but not writable by current user"
        tty_ok = False
    elif _user_in_dialout():
        tty_detail, tty_ok = "user in dialout group", True
    elif _udev_has_mtk_0666_rule():
        tty_detail, tty_ok = "udev 0666 rule installed", True
    else:
        tty_detail = (
            "no /dev/ttyACM* yet to probe; user NOT in dialout and no udev "
            "MODE=0666 rule — BROM handshake may fail with EACCES; fix: "
            "sudo usermod -aG dialout <user> or install udev rule")
        tty_ok = False
    items.append({"check": "ttyacm-write-path", "ok": tty_ok,
                  "detail": tty_detail, "probed_live": writable is not None})

    # 可执行位/动态库必然致命；adb 缺失仅在流程需要 adb 时致命；
    # ttyACM 写入路径不明默认只记 WARNING（87 实测 0666 也能跑），
    # strict_env_check 把全部 WARNING 升级为硬失败。
    non_fatal = {"adb-present", "ttyacm-write-path"}
    failed = [it for it in items if not it["ok"]]
    hard_fail = [
        it for it in failed
        if it["check"] not in non_fatal
        or (it["check"] == "adb-present" and it.get("needed"))
        or strict
    ]
    warnings = [it["check"] for it in failed if it not in hard_fail]
    return not hard_fail, {"items": items, "warnings": warnings}


def _shutil_which(cmd: str) -> "str | None":
    import shutil
    return shutil.which(cmd)


# ── v1.2.0 固件指纹路由 ─────────────────────────────────────────────
#
# getprop ro.product.model → 机型族（= firmware/ 下的一级目录）。
# 新机型接入 = 往这张表加一行 + NFS 放固件目录；表是权威源，
# 未列机型 fail-fast 而不是猜。ELA 先留路由项，固件包到位即可用。
_MODEL_FAMILY_ROUTES = {
    "MLD_LX2": "MLD",
    "MLD_LX3": "MLD",
    "ELA_LX2": "ELA",
    "ELA_LX3": "ELA",
    # 真机实测（2026-08-25 .66 回归）：getprop 返回连字符拼写；
    # adb devices 的 model 字段是下划线，两处来源不同。两套键都收。
    "MLD-LX2": "MLD",
    "MLD-LX3": "MLD",
    "ELA-LX2": "ELA",
    "ELA-LX3": "ELA",
}

# 每版本固件目录里的清单文件；latest.json 是族级版本指针
# （CIFS 上 symlink 不可靠，用指针文件）。
_MANIFEST_NAME = "manifest.json"
_LATEST_POINTER_NAME = "latest.json"
_DEFAULT_VERSION_PROP = "ro.build.version.incremental"

# ── PROGRESS 打戳（#115 阶段 2 / #134）──────────────────────────────
#
# 停滞判据只认 PROGRESS 戳（普通输出不算活）。flash 是长耗时且时长不可预估
# 的步骤，阶段推进时打戳；阶段内无输出 = seq 不涨 = 诚实的停滞信号 ——
# 阶段静默容忍在 PlanStep 上配大 stall_seconds（如 600s），不靠重复打印制造
# "活着"的假象。
#
# 阶段来源：解析 flash_tool 的 stdout/stderr（SP Flash Tool 输出），
# 识别到新阶段关键字 → seq+1 打戳；识别到百分比 → 附加 percent 字段打戳
# （粒度更细）。识别不到任何阶段输出时，只在 reboot 完成与设备重新枚举
# 时打戳。
_PHASE_TOKENS = (
    "S_DA_HANDSHAKE",
    "DOWNLOAD",
    "FORMAT",
    "ERASE",
    "VERIFY",
    "RECOVERY",
    "PRELOADER",
    "BOOTING",
)
_PROGRESS_PREFIX = "PROGRESS "


def _progress_stamp(seq: int, **fields) -> str:
    payload = {"seq": seq, "step": "flash", **fields}
    return _PROGRESS_PREFIX + json.dumps(payload, ensure_ascii=False)


def _emit_progress(seq: "list[int]", **fields) -> None:
    seq[0] += 1
    sys.stderr.write(_progress_stamp(seq[0], **fields) + "\n")
    sys.stderr.flush()


def _run_flash_tool_with_progress(
    cmd: list,
    cwd: str,
    env: dict,
    timeout: int,
    on_stage: "callable",
    on_percent: "callable",
    on_running: "callable | None" = None,
) -> "tuple[str, int]":
    """Popen + 双 reader 线程跑 flash_tool，逐行喂给阶段/进度解析。

    不能用 subprocess.run(capture_output)：flash_tool 可能运行几十分钟，
    期间必须持续打戳让停滞判据满意；readline 阻塞问题用 reader 线程解决
    （与引擎 _pump_process 同构）。

    on_running：进程与 reader 线程就绪后、进入轮询前回调一次——v1.3.3 起
    调用方在此发 adb reboot（工具须先于 reboot 进入 USB 扫描，BROM 窗口
    才有观测者；顺序颠倒则窗口空过，真机实证见模块 docstring v1.3.3 节）。
    """
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    if on_running is not None:
        try:
            on_running()
        except Exception:
            pass
    collected: list[str] = []
    seen_phases: set[str] = set()

    def _reader(stream) -> None:
        try:
            for line in stream:
                collected.append(line)
                upper = line.upper()
                matched = False
                for tok in _PHASE_TOKENS:
                    if tok in upper and tok not in seen_phases:
                        seen_phases.add(tok)
                        try:
                            on_stage(tok)
                        except Exception:
                            pass
                        matched = True
                        break
                if matched:
                    continue
                m = re.search(r"(\d{1,3})%", line)
                if m:
                    try:
                        on_percent(int(m.group(1)))
                    except Exception:
                        pass
        except (ValueError, OSError):
            pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    threads = [
        threading.Thread(target=_reader, args=(proc.stdout,), daemon=True),
        threading.Thread(target=_reader, args=(proc.stderr,), daemon=True),
    ]
    for th in threads:
        th.start()
    deadline = time.monotonic() + timeout
    try:
        while proc.poll() is None:
            if time.monotonic() >= deadline:
                try:
                    os.killpg(os.getpgid(proc.pid), 15)  # SIGTERM,杀整树
                except ProcessLookupError:
                    pass
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                proc.wait(timeout=10)
                raise subprocess.TimeoutExpired([str(cmd)], timeout)
            time.sleep(1)
        proc.wait(timeout=10)
    finally:
        for th in threads:
            th.join(timeout=2)
    return "".join(collected), proc.returncode


def _wait_device_back(
    serial: str, adb_path: str, timeout: int, on_tick: "callable",
) -> bool:
    """flash_tool 退出后轮询 adb devices，等设备重新枚举（打戳阶段）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            proc = subprocess.run(
                [adb_path, "devices"], capture_output=True, text=True, timeout=10,
            )
            if serial and serial in (proc.stdout or ""):
                return True
        except Exception:
            pass
        try:
            on_tick()
        except Exception:
            pass
        time.sleep(5)
    return False


_DEFAULT_REL_FLASH_TOOL = (
    "..", "..", "..", "resources", "flashtool",
    "SP_Flash_Tool_Selector_exe_Linux_v1.2444.00.100",
)


def _step_params() -> dict:
    raw = os.environ.get("STP_STEP_PARAMS", "{}")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _output(success: bool, **kwargs) -> None:
    payload = {"success": success, "skipped": False, **kwargs}
    print(json.dumps(payload, ensure_ascii=False))


def _resolve_under(root: str, candidate: str) -> str:
    if os.path.isabs(candidate):
        return candidate
    return os.path.normpath(os.path.join(root, candidate))


def _resolve_firmware_dir(rel: str) -> str:
    if os.path.isabs(rel):
        return rel
    nfs_root = os.environ.get("STP_NFS_ROOT", "")
    if nfs_root:
        return os.path.normpath(os.path.join(nfs_root, rel))
    return rel


def _locate_flash_tool_dir(params_override) -> str:
    if params_override:
        return params_override
    env_override = os.environ.get("STP_FLASH_TOOL_DIR", "")
    if env_override:
        return env_override
    script_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(script_dir, *_DEFAULT_REL_FLASH_TOOL))


def _pick_flash_tool_exe(tool_dir: str):
    names = ("flash_tool", "flash_tool.exe")
    search_roots = [tool_dir, os.path.join(tool_dir, "SP_Flash_Tool_V5")]
    for root in search_roots:
        for name in names:
            path = os.path.join(root, name)
            if os.path.isfile(path):
                return path
    return None


def _scan_output_for_verdict(stdout: str, stderr: str):
    combined = "\n".join(s for s in (stdout, stderr) if s)
    upper = combined.upper()
    for tok in _FAIL_TOKENS:
        if tok.upper() in upper:
            return False, f"fail token hit: {tok}"
    for tok in _PASS_TOKENS:
        if tok in combined:
            return True, f"pass token hit: {tok}"
    return False, "no pass token found"


def _acquire_host_lock(on_wait_tick: "callable | None" = None):
    if platform.system() == "Windows":
        return None
    import fcntl

    lock_fd = open(_LOCK_PATH, "w")
    # **轮询式等待**（#142 review）：flock(LOCK_EX) 阻塞期间不打任何戳，
    # permit cap=5 下同一 host 多个设备进 flash 时，等待中的设备会被停滞钟
    # 误杀。改 LOCK_NB 轮询 + 每 5s 打一次 stage="lock-wait" 戳——等待本身
    # 也是可见的进度。
    waited = 0
    while True:
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except (IOError, OSError):
            waited += 5
            if on_wait_tick is not None:
                try:
                    on_wait_tick(waited)
                except Exception:
                    pass
            time.sleep(5)
    return lock_fd


def _release_host_lock(lock_fd) -> None:
    if lock_fd is None:
        return
    try:
        lock_fd.close()
    except OSError:
        pass


def _build_subprocess_env(tool_dir: str) -> dict:
    """Mirror flash_tool.sh: prepend tool_dir and tool_dir/lib to LD_LIBRARY_PATH on Linux.

    Without this, flash_tool fails to dlopen libflashtool.so / libQt5Core.so under lib/.
    No-op on Windows (Qt DLLs resolved via PATH or co-located).
    """
    env = os.environ.copy()
    if platform.system() == "Windows":
        return env
    import posixpath
    lib_dir = posixpath.join(tool_dir, "lib")
    prefix = f"{tool_dir}:{lib_dir}"
    existing = env.get("LD_LIBRARY_PATH", "")
    env["LD_LIBRARY_PATH"] = f"{prefix}:{existing}" if existing else prefix
    return env


def _adb_device_state(serial: str, adb_path: str) -> str:
    """Probe ADB get-state; returns 'device', 'offline', 'unauthorized', 'no-device', or 'unknown'."""
    if not serial or not adb_path:
        return "no-device"
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "get-state"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return "unknown"
    if proc.returncode != 0:
        # adb returns non-zero when device isn't visible; stderr usually contains 'not found' / 'no device'
        return "no-device"
    return (proc.stdout or "").strip() or "unknown"


def _adb_getprop(prop: str, adb_path: str, serial: str, timeout: int = 10) -> "str | None":
    """读设备 prop；任何失败返回 None（调用方决定是否阻断）。"""
    if not serial or not adb_path:
        return None
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial, "shell", "getprop", prop],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if proc.returncode != 0:
        return None
    value = (proc.stdout or "").strip()
    return value or None


def _reboot_into_flash_mode(serial: str, target: str, adb_path: str, wait_seconds: int) -> dict:
    """Best-effort: ask the device to reboot via ADB.

    target="normal"（或缺省/空串）发不带参数的 `adb reboot`：完整上电流程
    流经 BROM(2000) 窗口，等待中的 flash_tool 才能抓中。"bootloader" /
    "fastboot" 是显式选项——热重启直达专用模式会跳过 BROM（v1.3.2 真机实证，
    见模块 docstring）。

    Returns a metrics dict; never raises. flash_tool will USB-poll for the device
    regardless of this outcome — if the device is already in preloader/BROM, the
    adb call simply no-ops (no device visible) and flash_tool takes over.
    """
    normalized = str(target or "").strip().lower()
    if normalized in ("", "normal"):
        effective = "normal"
        argv_tail = ["reboot"]
    else:
        effective = normalized
        argv_tail = ["reboot", normalized]
    result: dict = {"attempted": False, "target": effective}
    if not serial:
        result["skip_reason"] = "STP_DEVICE_SERIAL not set"
        return result
    if not adb_path:
        result["skip_reason"] = "STP_ADB_PATH not set"
        return result

    pre_state = _adb_device_state(serial, adb_path)
    result["pre_state"] = pre_state
    # Only "device" means the ADB channel is fully usable. offline / unauthorized /
    # no-device / unknown all mean reboot via adb would either be rejected, hang the
    # 15s best-effort timeout, or produce confusing stderr — let flash_tool USB-poll instead.
    if pre_state != "device":
        result["skip_reason"] = f"device not ready for adb reboot (state={pre_state}); flash_tool will wait on USB"
        return result

    result["attempted"] = True
    try:
        proc = subprocess.run(
            [adb_path, "-s", serial] + argv_tail,
            capture_output=True, text=True, timeout=15,
        )
        result["exit_code"] = proc.returncode
        if proc.returncode != 0:
            result["stderr_tail"] = (proc.stderr or "")[-300:]
    except subprocess.TimeoutExpired:
        result["error"] = "adb reboot timed out after 15s"
    except FileNotFoundError as exc:
        result["error"] = f"adb not found: {exc}"
    except Exception as exc:
        result["error"] = f"adb reboot failed: {exc}"

    if wait_seconds > 0:
        time.sleep(wait_seconds)
        result["waited_seconds"] = wait_seconds
    return result


# ── v1.2.0：参数/env 解析 + 固件路由 ─────────────────────────────────


def _param_or_env(cfg: dict, key: str, env_key: str, default):
    """STP_STEP_PARAMS > STP_FLASH_* env > 代码默认（MTBF P0 先例同款）。

    平台 scan 注册的脚本 default_params 恒为空、逐计划参数通道不存在
    （ADR-0029 D1 挂起）；部署级配置经 hot-update 同步的 env 注入。
    """
    value = cfg.get(key)
    if value is not None and str(value) != "":
        return value
    raw = os.environ.get(env_key, "")
    if raw != "":
        return raw
    return default


def _as_bool(value, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _load_json_file(path: str):
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _load_manifest(firmware_dir: str) -> "tuple[dict | None, str | None]":
    """读版本目录的 manifest.json；(manifest, error) 二选一。"""
    path = os.path.join(firmware_dir, _MANIFEST_NAME)
    if not os.path.isfile(path):
        return None, None
    data = _load_json_file(path)
    if data is None:
        return None, f"manifest.json is missing or malformed: {path}"
    scatter = data.get("scatter_file") or ""
    da = data.get("da_file") or ""
    if not scatter or not da:
        return None, (
            f"manifest.json must define scatter_file and da_file: {path}"
        )
    return data, None


def _resolve_route(args: dict) -> "tuple[dict | None, str | None]":
    """解析固件目标：显式 firmware_dir 优先，缺省按设备指纹路由。

    返回 (route, error)。route 字段：
      decided_by: "params"（显式 firmware_dir）| "fingerprint"（指纹路由）
      model / family / version / version_prop / firmware_dir / da_file /
      scatter_file / manifest_path
    version 为 None 表示无比对基准（显式目录且无 manifest）。
    """
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    adb_path = os.environ.get("STP_ADB_PATH", "adb")

    firmware_dir_raw = str(args.get("firmware_dir") or "").strip()
    if firmware_dir_raw:
        return _resolve_explicit_dir(args, firmware_dir_raw)
    return _resolve_by_fingerprint(args, serial, adb_path)


def _resolve_explicit_dir(args: dict, firmware_dir_raw: str) -> "tuple[dict | None, str | None]":
    firmware_dir = _resolve_firmware_dir(firmware_dir_raw)
    if not os.path.isdir(firmware_dir):
        return None, f"firmware_dir not found: {firmware_dir}"

    manifest, err = _load_manifest(firmware_dir)
    if err:
        return None, err

    da_raw = str(args.get("da_file") or "").strip()
    scatter_raw = str(args.get("scatter_file") or "").strip()
    da_name = da_raw or (manifest or {}).get("da_file") or ""
    scatter_name = scatter_raw or (manifest or {}).get("scatter_file") or ""
    if not da_name:
        return None, "da_file is required (param or manifest.json)"
    if not scatter_name:
        return None, "scatter_file is required (param or manifest.json)"

    da_file = _resolve_under(firmware_dir, da_name)
    scatter_file = _resolve_under(firmware_dir, scatter_name)
    version = (manifest or {}).get("version")
    version_prop = (manifest or {}).get("version_prop") or _DEFAULT_VERSION_PROP

    route = {
        "decided_by": "params",
        "model": None,
        "family": (manifest or {}).get("family"),
        "version": version,
        "version_prop": version_prop,
        "firmware_dir": firmware_dir,
        "da_file": da_file,
        "scatter_file": scatter_file,
        "manifest_path": (
            os.path.join(firmware_dir, _MANIFEST_NAME) if manifest else None
        ),
    }
    return route, None


def _read_latest_version(pointer: "dict | None", model: str) -> "str | None":
    """族级指针解析目标版本（v1.3.6 per-model 映射）。

    两种形态（向后兼容）：
      {"version": "..."}                         → 全族单版本（旧行为）
      {"versions": {"MLD_LX2": "...", ...}}      → 按机型取版本
    机型键匹配支持下划线/连字符互转（getprop 与 adb 拼写差异，2026-08-26
    现场教训）：model=MLD-LX2 时 versions 键可写 MLD_LX2 或 MLD-LX2。
    本机型无键 → None（调用方 fail-fast，错误信息指引补 versions）。
    """
    if not pointer:
        return None
    versions = pointer.get("versions")
    if isinstance(versions, dict):
        for key in (model, model.replace("_", "-"), model.replace("-", "_")):
            value = versions.get(key)
            if value:
                return str(value).strip() or None
        return None
    version = pointer.get("version")
    return str(version).strip() or None if version else None


def _resolve_by_fingerprint(
    args: dict, serial: str, adb_path: str,
) -> "tuple[dict | None, str | None]":
    model = _adb_getprop("ro.product.model", adb_path, serial)
    if not model:
        return None, (
            "fingerprint routing failed: cannot read ro.product.model via adb "
            "(device not reachable?); set firmware_dir explicitly or wait for "
            "the device to come back to Android"
        )

    family = str(args.get("family") or "").strip()
    if not family:
        family = _MODEL_FAMILY_ROUTES.get(model)
        if not family:
            known = ", ".join(sorted(_MODEL_FAMILY_ROUTES))
            return None, (
                f"no firmware family route for model {model}; "
                f"known models: {known}"
            )

    firmware_root = str(
        _param_or_env(args, "firmware_root", "STP_FLASH_FIRMWARE_ROOT", "")
    ).strip()
    if not firmware_root:
        nfs_root = os.environ.get("STP_NFS_ROOT", "").strip()
        if not nfs_root:
            return None, (
                "firmware root unknown: set STP_FLASH_FIRMWARE_ROOT or STP_NFS_ROOT"
            )
        firmware_root = os.path.join(nfs_root, "firmware")

    version = str(
        _param_or_env(args, "version", "STP_FLASH_FIRMWARE_VERSION", "")
    ).strip()
    if not version:
        pointer_path = os.path.join(firmware_root, family, _LATEST_POINTER_NAME)
        pointer = _load_json_file(pointer_path)
        version = _read_latest_version(pointer, model)
        if not version:
            return None, (
                f"no target version for model {model}: set "
                f"STP_FLASH_FIRMWARE_VERSION, or write "
                f'{{"version": "..."}} / {{"versions": {{"{model}": "..."}}}} '
                f"to {pointer_path}"
            )

    firmware_dir = os.path.join(firmware_root, family, version)
    if not os.path.isdir(firmware_dir):
        return None, f"firmware_dir not found: {firmware_dir}"

    manifest, err = _load_manifest(firmware_dir)
    if err:
        return None, err
    if manifest is None:
        return None, (
            f"fingerprint routing requires manifest.json in {firmware_dir}"
        )

    allowed_models = manifest.get("models")
    if isinstance(allowed_models, list) and allowed_models \
            and model not in [str(m) for m in allowed_models]:
        return None, (
            f"model {model} not in manifest models {allowed_models} "
            f"of {firmware_dir}"
        )

    manifest_version = str(manifest.get("version") or "")
    if manifest_version != version:
        return None, (
            f"manifest version {manifest_version} != resolved dir version "
            f"{version} under {firmware_dir}"
        )

    route = {
        "decided_by": "fingerprint",
        "model": model,
        "family": family,
        "version": manifest_version,
        "version_prop": manifest.get("version_prop") or _DEFAULT_VERSION_PROP,
        "firmware_dir": firmware_dir,
        "da_file": _resolve_under(firmware_dir, manifest["da_file"]),
        "scatter_file": _resolve_under(firmware_dir, manifest["scatter_file"]),
        "manifest_path": os.path.join(firmware_dir, _MANIFEST_NAME),
    }
    return route, None


def _precheck_version(route: dict, serial: str, adb_path: str) -> dict:
    """刷前版本比对。返回 {"skip": bool, ...}；adb 不可达不阻断。"""
    target = route.get("version")
    if not target:
        return {"checked": False, "reason": "no target version (no manifest)"}
    current = _adb_getprop(route.get("version_prop") or _DEFAULT_VERSION_PROP,
                           adb_path, serial)
    if current is None:
        return {
            "checked": False,
            "reason": "adb getprop unavailable; proceeding with flash",
            "target": target,
        }
    return {
        "checked": True,
        "current": current,
        "target": target,
        "skip": current == target,
    }


def _wait_device_ready(
    serial: str, adb_path: str, timeout: int, on_tick: "callable",
) -> bool:
    """等设备回到完全可用的 adb 状态（get-state == device）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _adb_device_state(serial, adb_path) == "device":
            return True
        try:
            on_tick()
        except Exception:
            pass
        time.sleep(5)
    return False


def _verify_after_flash(
    route: dict, serial: str, adb_path: str, wait_seconds: int,
    on_tick: "callable",
) -> "tuple[bool, dict]":
    """刷后核验：设备回 adb + 回读版本与 manifest 比对。"""
    report: dict = {"wait_seconds": wait_seconds}
    target = route.get("version")
    if not target:
        report["skipped_reason"] = "no target version (no manifest)"
        return True, report

    if not _wait_device_ready(serial, adb_path, wait_seconds, on_tick):
        report["error"] = (
            f"device did not become adb-ready within {wait_seconds}s after flash"
        )
        return False, report

    actual = _adb_getprop(
        route.get("version_prop") or _DEFAULT_VERSION_PROP, adb_path, serial,
    )
    report["current"] = actual
    report["target"] = target
    if actual is None:
        report["error"] = "post-flash version readback failed (getprop)"
        return False, report
    if actual != target:
        report["error"] = f"post-flash version mismatch: expected {target}, got {actual}"
        return False, report
    return True, report


_BOOT_COMPLETED_PROP = "sys.boot_completed"


def _usb_topology_fingerprint(base: str = _SYSFS_USB_BASE) -> str:
    """USB 拓扑快照：可见设备目录的 `port:vid:pid` 排序拼接。

    设备 reboot（BROM↔系统）必然产生枚举变化 → 指纹变化。连续多次指纹
    相同 = 当前没有设备在重启——下一任持锁者的工具扫描窗口不会撞上
    可捕获态（v1.3.7，实证见模块 docstring v1.3.7 节）。
    跳过规则与 _list_mtk_ports 一致：接口目录(含 ':')与根 hub(usbN 无 '-'）。
    """
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return ""
    entries: list[str] = []
    for name in names:
        if ":" in name or "-" not in name:
            continue
        dev_dir = os.path.join(base, name)
        vid = _sysfs_read(os.path.join(dev_dir, "idVendor")) or "-"
        pid = _sysfs_read(os.path.join(dev_dir, "idProduct")) or "-"
        entries.append(f"{name}:{vid}:{pid}")
    return "|".join(entries)


def _wait_boot_stable(
    serial: str, adb_path: str,
    stable_seconds: int = 20, max_wait: int = 120,
    on_tick: "callable | None" = None,
    usb_base: str = _SYSFS_USB_BASE,
    poll_interval: float = 5.0,
) -> dict:
    """首刷二次重启窗口守卫：boot_completed=1 且 USB 拓扑稳定后才算稳定。

    返回 report dict（永不抛异常）；ok=False 不代表失败——调用方按
    v1.3.4「adb 已回归或确认卡死」语义决定是否放行（卡死设备不会重启,
    无窗口可撞）。拓扑指纹为空（瞬时读失败）按不稳定处理,不计入稳定窗口。
    """
    report: dict = {
        "stable_seconds": stable_seconds,
        "max_wait": max_wait,
    }
    deadline = time.monotonic() + max_wait
    last_fp = ""
    stable_since: "float | None" = None
    booted_seen = False
    while time.monotonic() < deadline:
        booted = _adb_getprop(_BOOT_COMPLETED_PROP, adb_path, serial)
        fp = _usb_topology_fingerprint(usb_base)
        if booted == "1" and fp and fp == last_fp:
            if stable_since is None:
                stable_since = time.monotonic()
            if time.monotonic() - stable_since >= stable_seconds:
                report.update({
                    "ok": True,
                    "boot_completed": True,
                    "stable_seconds_elapsed": round(
                        time.monotonic() - stable_since, 1),
                })
                return report
        else:
            stable_since = None
        last_fp = fp
        booted_seen = booted_seen or booted == "1"
        if on_tick is not None:
            try:
                on_tick()
            except Exception:
                pass
        time.sleep(poll_interval)
    report.update({
        "ok": False,
        "boot_completed": booted_seen,
        "reason": f"device not boot-stable within {max_wait}s",
    })
    return report


def main() -> None:
    args = _step_params()
    serial = os.environ.get("STP_DEVICE_SERIAL", "")
    adb_path = os.environ.get("STP_ADB_PATH", "adb")

    # ── v1.2.0：固件目标解析（显式 firmware_dir / 指纹路由）────────
    route, route_error = _resolve_route(args)
    if route_error is not None or route is None:
        _output(False, error_message=route_error or "route resolution failed")
        return

    skip_if_current = _as_bool(
        _param_or_env(args, "skip_if_current", "STP_FLASH_SKIP_IF_CURRENT", ""),
        default=True,
    )
    verify_version = _as_bool(
        _param_or_env(args, "verify_version", "STP_FLASH_VERIFY_VERSION", ""),
        default=True,
    )

    started_at = time.time()
    # seq 必须先于锁等待定义：锁被占用时第一次 tick 就要打戳，
    # 定义晚了会 NameError(被 _acquire_host_lock 的 except 吞掉 → 不打戳)。
    seq: list[int] = [0]

    # ── v1.2.0：刷前版本比对（同版本 → skipped 收场）────────────────
    # 必须先于 da/scatter 与 flash_tool 校验：skipped 语义是"无事可做"，
    # 不应因刷机工具未部署（flashtool 二进制不进 git，CI/新 worktree 无）
    # 或包内个别文件缺失而失败——真要刷的设备才需要完整的包与工具。
    version_check = _precheck_version(route, serial, adb_path)
    _emit_progress(seq, stage="version-check", result=json.dumps(
        version_check, ensure_ascii=False))
    if skip_if_current and version_check.get("skip"):
        _output(True, skipped=True, metrics={
            "route": route,
            "version_check": version_check,
            "duration_seconds": round(time.time() - started_at, 2),
        })
        return

    da_file = route["da_file"]
    scatter_file = route["scatter_file"]
    firmware_dir = route["firmware_dir"]
    if not os.path.isfile(da_file):
        _output(False, error_message=f"da_file not found: {da_file}",
                metrics={"route": route, "version_check": version_check})
        return
    if not os.path.isfile(scatter_file):
        _output(False, error_message=f"scatter_file not found: {scatter_file}",
                metrics={"route": route, "version_check": version_check})
        return

    tool_dir = _locate_flash_tool_dir(args.get("flash_tool_dir"))
    if not os.path.isdir(tool_dir):
        _output(False, error_message=f"flash_tool_dir not found: {tool_dir}",
                metrics={"route": route, "version_check": version_check})
        return
    flash_tool_exe = _pick_flash_tool_exe(tool_dir)
    if not flash_tool_exe:
        _output(False, error_message=f"flash_tool executable not found under {tool_dir}",
                metrics={"route": route, "version_check": version_check})
        return

    command = args.get("command") or "firmware-upgrade"
    boot_mode = args.get("boot_mode") or "auto"
    try:
        timeout = int(args.get("timeout_seconds", 1200))
    except (TypeError, ValueError):
        timeout = 1200
    try:
        verify_wait = int(args.get("verify_wait_seconds", 300))
    except (TypeError, ValueError):
        verify_wait = 300
    try:
        boot_stabilize_seconds = int(args.get("boot_stabilize_seconds", 20))
    except (TypeError, ValueError):
        boot_stabilize_seconds = 20
    try:
        boot_stabilize_max_wait = int(args.get("boot_stabilize_max_wait", 120))
    except (TypeError, ValueError):
        boot_stabilize_max_wait = 120

    # ── v1.3.0：重试 / 门控 / 预检参数 ──────────────────────────────
    try:
        max_attempts = int(
            _param_or_env(args, "max_attempts", "STP_FLASH_MAX_ATTEMPTS", 2))
    except (TypeError, ValueError):
        max_attempts = 2
    max_attempts = max(1, min(4, max_attempts))  # 上限 4：防手滑打满 host
    try:
        retry_backoff = int(_param_or_env(
            args, "retry_backoff_seconds", "STP_FLASH_RETRY_BACKOFF", 10))
    except (TypeError, ValueError):
        retry_backoff = 10
    retry_backoff = max(0, retry_backoff)
    try:
        pre_reboot_wait = int(args.get("pre_reboot_wait_seconds", 5) or 0)
    except (TypeError, ValueError):
        pre_reboot_wait = 5
    gate_other_mtk = _as_bool(
        _param_or_env(args, "gate_other_mtk", "STP_FLASH_GATE_OTHER_MTK", ""),
        default=True,
    )
    strict_env_check = _as_bool(
        _param_or_env(args, "strict_env_check", "STP_FLASH_STRICT_ENV_CHECK", ""),
        default=False,
    )
    # v1.3.2：缺省/"normal"/空串 → 普通 adb reboot（流经 BROM 窗口）；
    # "bootloader"/"fastboot" 显式选择热重启直达模式。
    reboot_target = str(args.get("reboot_target") or "").strip().lower() or "normal"

    cmd = [flash_tool_exe, "-c", command, "-d", da_file, "-s", scatter_file, "-b", boot_mode]
    subprocess_env = _build_subprocess_env(os.path.dirname(flash_tool_exe))

    # ── v1.3.0：环境预检（拿锁前短路，硬失败信息直指修复动作）────────
    need_adb = bool(serial) and (
        bool(args.get("reboot_to_flash", True)) or verify_version)
    env_ok, env_precheck = _precheck_environment(
        flash_tool_exe, adb_path if adb_path else "adb",
        need_adb=need_adb, strict=strict_env_check,
    )
    _emit_progress(seq, stage="precheck", ok=env_ok, result=json.dumps(
        {"warnings": env_precheck["warnings"]}, ensure_ascii=False))
    if not env_ok:
        failed = [it for it in env_precheck["items"] if not it["ok"]]
        _output(False,
                error_message=(
                    "environment precheck failed: "
                    + "; ".join(
                        f"{it['check']}: {it['detail']}" for it in failed)),
                metrics={"route": route, "version_check": version_check,
                         "env_precheck": env_precheck})
        return

    try:
        lock_fd = _acquire_host_lock(
            on_wait_tick=lambda waited: _emit_progress(
                seq, stage="lock-wait", waited_seconds=waited,
            )
        )
    except OSError as exc:
        _output(False, error_message=f"lock setup failed: {exc}",
                metrics={"route": route, "version_check": version_check})
        return
    lock_acquired_at = time.time()

    # ── v1.3.0：门控（必须在 adb reboot 之前反查目标口——BROM 态无 serial）
    target_port = None
    if gate_other_mtk and _is_linux():
        target_port = _port_for_serial(serial)
    if gate_other_mtk:
        gating = _gate_other_mtk(target_port)
    else:
        gating = {"hidden": [], "errors": {},
                  "skipped_reason": "disabled by params",
                  "target_port": target_port}
    _emit_progress(seq, stage="gating", result=json.dumps(
        {"hidden": gating.get("hidden", []),
         "skipped_reason": gating.get("skipped_reason"),
         "target_port": gating.get("target_port")},
        ensure_ascii=False))
    hidden_ports: list = list(gating.get("hidden", []))

    attempts_report: list = []
    final_output, final_rc = "", None
    final_error: "tuple[str, str] | None" = ("exhausted", "no attempt made")
    verdict_ok = False
    verdict_evidence = ""
    pre_reboot: dict = {"attempted": False, "skip_reason": "not attempted"}

    # ── v1.3.4：锁结算幂等化 + 持锁穿过核验 ────────────────────────────
    # 场景 2 实证（2026-08-26 .66 双机并发）：旧顺序在工具退出后立即释放
    # 锁，而本机手机的看门狗重启发生在锁外——下一任持锁者的工具扫描窗会
    # 撞上这个可捕获态，把新固件刷进错误的手机。改为：成功路径持锁穿过
    # re-enumerate 与 verify（自己的手机稳定后——adb 已回归或确认卡死——
    # 才交出锁）；失败与异常路径立即结算，保持原兜底语义。
    lock_settled = False

    def _settle_lock() -> None:
        nonlocal lock_settled
        if lock_settled:
            return
        lock_settled = True
        gating["restore"] = _restore_gated({"hidden": hidden_ports})
        _release_host_lock(lock_fd)
        _emit_progress(seq, stage="lock-released")

    try:
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                _emit_progress(seq, stage="retry", attempt=attempt,
                               total=max_attempts)
                if retry_backoff > 0:
                    time.sleep(retry_backoff)
                # 防御性重画门控：上次尝试期间可能有别的手机重启进刷机态
                # （authorized=0 只对当前实例生效，见 note §Decision.3）
                regate = _gate_other_mtk(target_port)
                for name in regate.get("hidden", []):
                    if name not in hidden_ports:
                        hidden_ports.append(name)

            # v1.3.3 时序：工具先启动进入 USB 扫描，on_running 回调里再发
            # adb reboot——BROM 窗口只存在于上电最初几秒，观测者必须就位在先
            # （Agent Note Decision 流程图的原序；v1.2.0 遗留的先 reboot 后起
            # 工具顺序真机证伪：工具只能看到窗口过后的普通态枚举）。重试轮里
            # 即使 params 关了 reboot 也照发（设备多半已在 BROM/DA 态，adb
            # 调用会 no-op）。
            do_reboot = bool(args.get("reboot_to_flash", True)) or attempt > 1

            # ── v1.3.8：门控保持 ──────────────────────────────────────
            # 实证（2026-08-30 run 258/259/260，.68）：BROM 幽灵设备
            # （1-7.4.4，serial 空 pid 2000）周期性重枚举——新 USB 实例
            # authorized 回到 1（门控只对当前实例生效），一次性门控被
            # 逃逸；工具扫描/下载期间抓到它，3.3G 固件写进幽灵（BROM
            # 态不变），目标设备空等 → verify mismatch。dmesg 佐证：
            # run 260 目标 BROM 窗口仅 3s（正常重启流程快速经过），而
            # 幽灵 2000 常驻。修复：工具运行期间周期重写非目标刷机态口
            # authorized=0——on_running（reboot 前）一次 + on_percent
            # 每 10s 节流一次，直到下载完成。
            regate_count = 0
            # -inf：首次调用必触发（on_running 就在定义后 ~15s 内到达,
            # 若用当前时刻会把 reboot 前那次关键压制节流掉）
            last_regate_at = float("-inf")

            def _regate() -> None:
                nonlocal regate_count, last_regate_at, hidden_ports
                now = time.monotonic()
                if now - last_regate_at < 10:
                    return
                last_regate_at = now
                regate = _gate_other_mtk(target_port)
                for name in regate.get("hidden", []):
                    if name not in hidden_ports:
                        hidden_ports.append(name)
                regate_count += 1

            def _fire_reboot_when_tool_ready() -> None:
                nonlocal pre_reboot
                _regate()  # v1.3.8：reboot 前再压制一轮（堵重枚举逃逸）
                if not do_reboot:
                    return
                time.sleep(pre_reboot_wait)  # 给工具进入扫描的提前量
                pre_reboot = _reboot_into_flash_mode(
                    serial=serial,
                    target=reboot_target,
                    adb_path=adb_path,
                    wait_seconds=0,
                )
                if pre_reboot.get("attempted"):
                    # reboot 进入 flash 模式本身是一个阶段（#134）
                    _emit_progress(seq, stage="reboot", target=reboot_target)

            attempt_record: dict = {"attempt": attempt}
            try:
                output, flash_rc = _run_flash_tool_with_progress(
                    cmd,
                    cwd=os.path.dirname(flash_tool_exe),
                    env=subprocess_env,
                    timeout=timeout,
                    on_stage=lambda tok: _emit_progress(seq, stage=tok),
                    on_percent=lambda pct: (
                        _emit_progress(seq, percent=pct),
                        _regate(),
                    ),
                    on_running=_fire_reboot_when_tool_ready,
                )
            except subprocess.TimeoutExpired:
                attempt_record.update({"outcome": "timeout",
                                       "timeout_seconds": timeout})
                attempts_report.append(attempt_record)
                final_error = ("timeout",
                               f"flash_tool timed out after {timeout}s")
                continue
            except FileNotFoundError as exc:
                attempts_report.append(
                    {"attempt": attempt, "outcome": "launch-failed"})
                final_error = (
                    "launch",
                    f"flash_tool not executable ({exc}); chmod +x or check libs")
                break  # 环境性问题，重试无意义
            except Exception as exc:
                attempts_report.append(
                    {"attempt": attempt, "outcome": "launch-failed"})
                final_error = ("launch", f"flash_tool launch failed: {exc}")
                break

            v_ok, v_evidence = _scan_output_for_verdict(output, "")
            attempt_record.update({
                "outcome": "ok" if v_ok else "verdict-failed",
                "exit_code": flash_rc,
                "caught_brom": "BROM connected" in output,
                "verdict": v_evidence,
            })
            attempts_report.append(attempt_record)
            final_output, final_rc = output, flash_rc
            verdict_ok, verdict_evidence = v_ok, v_evidence
            final_error = None if v_ok else ("verdict", v_evidence)
            if v_ok:
                break

    except Exception:
        _settle_lock()  # 异常兜底：不能把邻机留在 authorized=0 的隐藏态
        raise

    if not verdict_ok:
        kind, detail = final_error
        _settle_lock()
        _output(False,
                error_message=(
                    f"flash failed after {len(attempts_report)} attempt(s): "
                    f"{detail}"),
                metrics={"command_argv": cmd,
                         "route": route,
                         "version_check": version_check,
                         "env_precheck": env_precheck,
                         "gating": gating,
                         "target_port": target_port,
                         "attempts": attempts_report,
                         "attempt_count": len(attempts_report),
                         "pre_reboot": pre_reboot,
                         "stdout_tail": final_output[-1500:],
                         "duration_seconds":
                             round(time.time() - started_at, 2)})
        return

    # flash_tool 退出后：等设备重新枚举（最长 60s），期间打戳。
    # 设备没回来**不判失败**——flash 成功但枚举慢是常态，记录字段供诊断。
    reenumerated = _wait_device_back(
        serial=serial,
        adb_path=adb_path,
        timeout=60,
        on_tick=lambda: _emit_progress(seq, stage="re-enumerate"),
    )

    # ── v1.2.0：刷后版本核验（verify 开启时不一致/等不到设备 → 失败）──
    if verify_version:
        verify_ok, verify_report = _verify_after_flash(
            route, serial, adb_path, verify_wait,
            on_tick=lambda: _emit_progress(seq, stage="verify-wait"),
        )
        _emit_progress(seq, stage="verify", ok=verify_ok)
    else:
        verify_ok, verify_report = True, {"skipped_reason": "verify_version disabled"}
    _emit_progress(seq, stage="done")

    # ── v1.3.7：锁内 boot 稳定等待 ────────────────────────────────────
    # 实证（2026-08-30 run 258，.68 串行 14 台）：verify 通过（adb 回归 +
    # 版本一致）≠ 设备完成首次开机——HONOR 首刷后 boot 完成后会再重启一次
    # （初始化），重启窗口期以 BROM/preloader 可捕获态暴露（新 USB 实例
    # authorized=1，门控失效），下一任持锁者的工具扫描把新固件刷进上一台。
    # v1.3.4 持锁穿过 verify 只覆盖 verify 期间；这里把「锁内稳定」延伸
    # 到 boot_completed=1 且 USB 拓扑稳定（设备不再重启）才交出锁。
    boot_stable_report: dict = {"skipped_reason": "not reached"}
    if verify_ok:
        boot_stable_report = _wait_boot_stable(
            serial=serial, adb_path=adb_path,
            stable_seconds=boot_stabilize_seconds,
            max_wait=boot_stabilize_max_wait,
            on_tick=lambda: _emit_progress(seq, stage="boot-stabilize"),
        )
    # 失败路径不等待：设备状态已知异常，立即结算（v1.3.4 兜底语义）。
    # boot 稳定超时（ok=False）不判失败——卡死设备不会重启,无窗口可撞。

    # v1.3.8：门控保持计数并入 gating（诊断：重枚举逃逸被压制的轮次）
    gating["regate_count"] = regate_count

    # v1.3.4 核心改动点：核验完成后才结算锁。此刻自己的手机已稳定
    # （adb 回归或确认卡死），下一任持锁者的门控/扫描不会撞上可捕获态。
    _settle_lock()

    stdout = final_output
    stderr = ""
    duration = round(time.time() - started_at, 2)
    lock_wait = round(lock_acquired_at - started_at, 2)

    metrics = {
        "duration_seconds": duration,
        "lock_wait_seconds": lock_wait,
        "device_reenumerated": reenumerated,
        "exit_code": final_rc,
        "command_argv": cmd,
        "da_file": da_file,
        "scatter_file": scatter_file,
        "firmware_dir": firmware_dir,
        "route": route,
        "version_check": version_check,
        "post_flash_verify": verify_report,
        "pre_reboot": pre_reboot,
        "stdout_tail": stdout[-1500:],
        "stderr_tail": stderr[-500:],
        # ── v1.3.0 ──
        "env_precheck": env_precheck,
        "gating": gating,
        "target_port": target_port,
        "attempts": attempts_report,
        "attempt_count": len(attempts_report),
        # ── v1.3.7 ──
        "boot_stable": boot_stable_report,
    }

    if final_rc != 0:
        _output(False,
                error_message=f"flash_tool exited {final_rc}: {(stderr or stdout)[:1500]}",
                metrics=metrics)
        return

    # 判决已在尝试环内得出（verdict_ok / verdict_evidence）；此处仅落账。
    if not verdict_ok:
        _output(False, error_message=f"verdict failed: {verdict_evidence}",
                metrics=metrics)
        return

    if not verify_ok:
        _output(False,
                error_message=f"post-flash verify failed: {verify_report.get('error')}",
                metrics=metrics)
        return

    metrics["verdict"] = verdict_evidence
    _output(True, metrics=metrics)


if __name__ == "__main__":
    main()
