# 批量刷机「Download Succeeded 但设备未变」修复链（v1.3.7→1.3.9）

Status: implemented
Class: bug-fix

## Decision

2026-08-30 LX2 V62→V71 批量（15 台）暴露「工具报 Download Succeeded 但目标
设备仍是 V62」系列故障。三轮真机迭代定位三类根因，修复形态为三个版本：

| 版本 | 根因 | 修复 | 证据 |
|------|------|------|------|
| v1.3.7 | verify 通过（adb 回归+版本一致）≠ 设备完成首次开机——HONOR 首刷后 boot 完成会再重启一次（初始化），重启窗口期以 BROM/preloader 可捕获态暴露 | verify 通过后**持锁**等 `sys.boot_completed=1` 且 USB 拓扑指纹稳定 20s 才释放锁 | 236/238 设备 uptime 1:10（verify 通过 ≈01:40，05:10 才稳定启动） |
| v1.3.8 | BROM 幽灵设备（serial 空 pid 2000）周期性重枚举逃逸一次性门控——新 USB 实例回到 `authorized=1`，工具扫描/下载期间抓到它，3.3G 固件写进幽灵（BROM 态不变） | 工具运行期间周期重写非目标刷机态口 `authorized=0`（on_running reboot 前 + on_percent 每 10s 节流） | run 260 dmesg：目标 BROM 窗口仅 3s（正常重启流程快速经过），幽灵 2000 常驻；手动禁用 10 分钟后随门控重枚举回归 |
| v1.3.9 | v1.3.8 的全量周期 toggle（含 201c 正常态设备）制造 udev 风暴，工具 120s 扫描被事件淹没 S_TIMEOUT(1042)，连目标 3 秒 BROM 窗口都抓不到 | 保持阶段收窄为只压制 BROM/preloader 态（0003/2000/2001/3000）——201c 不在工具 BROM 扫描目标内，周期 toggle 纯噪音 | run 261 stdout：扫描期间 1-8/1-9 反复 unbind/remove/add |

关键事实（均为实测，非推测）：

1. **目标设备的 BROM 窗口只有 ~3 秒**——正常 reboot 流程快速经过 BROM，
   工具必须在窗口内完成枚举+连接（工具先启动 15s 再 reboot 的 v1.3.3
   时序是前提）。236/238 成功证明窗口可捕获，前提是扫描环境干净。
2. **HONOR 正常 adb 态 pid=201c**（非 2046）——.68 的 14 台正常设备全部
   201c。门控把 201c 当刷机态一次性隐藏无害，但**周期 toggle 有害**。
3. **`authorized=0` 只对当前 USB 实例生效**——设备重枚举后新实例回到
   auth=1（既有 Agent Note §Decision.3 已记录，本次是其后果的完整实证）。
4. **并发指纹路由竞态**（run 264 的 285 台）：路由阶段在锁外，8 台并行
   读 model 时，第一台的初始门控（全量隐藏）把其它 job 的设备也隐藏
   了 → adb 断 → 路由失败。单次失败直接 return（launch 类不重试），
   无并发时重派即可恢复。未修：影响面小（偶发+可重派），观察后定。

落地：

- `flash_firmware` v1.3.7/1.3.8/1.3.9（`backend/agent/scripts/flash_firmware/`）
- v1.3.7 新参数 `boot_stabilize_seconds`(20)/`boot_stabilize_max_wait`(120)；
  v1.3.8 新 PROGRESS 阶段 `boot-stabilize`、metrics `boot_stable`；
  v1.3.9 `_gate_other_mtk` 新增 `hold_pids` 参数（初始门控全量，保持
  阶段 BROM-only），metrics.gating 新增 `regate_count`
- seed 迁移 s2t3u4v5w6x7（v1.3.7）/ t2u3v4w5x6y7（v1.3.8）/
  u3v4w5x6y7z8（v1.3.9），逐代 deactivate 两代前版本
- 测试 test_flash_firmware_v137/138/139.py（20 例）

## Alternatives

- **延长目标 BROM 窗口**（让设备在 BROM 驻留更久）：设备侧行为，平台无法
  控制；放弃。
- **门控保持全量 + 提高 regate 频率**：v1.3.8 已证伪——udev 风暴是
  干扰源不是解药。
- **移除 1-7.4.4 幽灵（物理）**：临时可用（authorized=0 手动禁用），
  但幽灵会随重枚举回归，且 root cause 是门控逃逸机制而非单个设备。
- **verify 判据升级为 boot_completed**：v1.3.7 已含（拓扑指纹 + boot
  双条件）；单靠 boot_completed 抓不住「boot 完成后的二次重启」。

## Verification

- 单测：v1.3.7（指纹/稳定窗口/超时/main 集成 12 例）、v1.3.8（节流/
  合并/main 集成 3 例）、v1.3.9（hold_pids 过滤/main 集成 4 例）；
  全量 agent 测试 1264 passed
- 真机矩阵（.68，V62→V71）：run 262 单台 ✓ → run 263 双台串行 ✓✓ →
  run 264 批量 8 台 7✓（1 台竞态）→ run 265 重派 ✓——11 台设备
  getprop 实测 V71；含历史最难的「串行第二台」（v1.3.6 时代 11/14 失败）
- 取证命令：`dmesg`（BROM 窗口/重枚举）、`/sys/bus/usb/devices/*/authorized`
  （门控状态）、step_trace metrics（lock_wait/regate_count/boot_stable）

## Revisit

- **幽灵设备 1-7.4.4 归属**：serial 空无法识别；疑似 250（6491，run 258
  后消失）。人工插拔恢复后应确认为哪台，若 250 复活需补刷 V71。
- **并发路由竞态**（run 264 的 285 模式）：批量 >host 槽位时可能复现；
  若批量事故（非单台重派可救）再考虑路由阶段持读锁/门控避让。
- **4 台 OFFLINE V62**（250/237/246/247，.68）：人工检查 hub 口后按需
  重刷；237/246/247 不在当前 USB 枚举（物理缺失，非平台问题）。
- 未来若换非 root 测试固件（persist.sys.usb.config 需求重提），BROM
  窗口时长与 201c 语义需重新实测。
