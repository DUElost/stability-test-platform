# flash_firmware v1.3.20：V6 刷机必须绑定并核验目标 USB 口（run 921）

Status: implemented
Class: bug-fix
- **Requirement**: [#3688](https://github.com/DUElost/stability-test-platform/issues/3688)
  （535 / MLD_NX2 V6 适配）；关联
  [#3690](https://github.com/DUElost/stability-test-platform/issues/3690)
  （`201c` 门控判据）、run 921 / job 185452 / plan 80

## Decision

V6（`SPFlashToolV6`）在 Linux 上默认启用四道判据，V5 分支逐字保持原行为：

1. **目标身份 fail-closed**：起工具前必须确定"这次操作的是哪个物理 USB 口"。按
   `STP_DEVICE_SERIAL` 反查 sysfs 查不到口，且 adb 状态不是 `device`、且该口当前不在
   可刷态 → 判失败，且在**抢 host 刷机锁之前**返回。
2. **命令行钉口**：V6 追加 `-p <sysfs 口>`（实测形态与 `symbolic_link` 末段一致）。
3. **逐行核验绑定口**：以工具自己回显的 `scan device succeed, symbolic_link: …@<口>`
   为准，绑到非目标口 → SIGTERM→SIGKILL 整棵工具树，终止尝试环（不重试），
   以 `FlashToolAborted` 记账。
4. **门控口径按代际分档**：V6 初始门控与重试重画只用 `_BROM_GATE_PIDS`
   （`0003/2000/2001/3000/2026`），不再压制 `201c`；V5 沿用历史全量集合。

参数：`require_port_binding` / `require_adb_ready`（默认仅 v6+Linux 为 true）、
`gate_pid_scope`（`brom` / `full`）、`flash_port`（救砖显式指定口）。留痕字段：
`metrics.identity` / `metrics.port_binding` / `metrics.aborted`。

## Alternatives

- **只加 `-p`，不加回显核验**：否。`-p` 是否真生效只能靠探针间接推断；一次误绑就是
  往别人手机里写固件，必须有独立判据。
- **只加回显核验，不加 `-p`**：否。从 `scan device succeed` 到 `enter 1st DA mode` 只有
  几秒，靠轮询粒度 1s 的事后判读有真实下发窗口。
- **`201c` 全局从 `_FLASH_STAGE_PIDS` 移除**：暂否。这会让在用的 552 计划行为变化，
  而"发布顺序红线"要求 V5 零变化；本单先按代际分档，全局裁决留给 #3690。
- **`no-device` 时改成"等 adb 恢复再刷"**：否。设备卡在 preloader 时 adb 永远不会回来，
  等待只把失败点后移；救砖路径改由显式 `flash_port` 承担（要人说清是哪台）。
- **失败后自动重试**：否（针对绑错口这一类）。重试等于让工具再去找一个非目标口。

## Verification

- 判据来源（NX2 host `172.21.x.x`，末段 `.85`，包内二进制，2026-10-13 两次**零写入**探针）：
  - `-p 1-99` + 现场存在 2 个可捕获 preloader 口 → 回显 `com port clue: COM://1-99.`，
    45s 内不绑任何设备 ⇒ `-p` 真按端口过滤；
  - 不给 `-p` → 回显空线索 `com port clue: COM://.`，当场绑到当时在 preloader 的口。
- 新增 `backend/agent/tests/test_flash_firmware_v1320.py`（18 例）：绑错口必须杀进程并上抛、
  Windows 回显不得误判、run 921 形态必须拒绝、V6 命令行含 `-p` 而 V5 逐字不变、
  V6 门控不得碰 `201c` 而 V5 照旧、main 在不可控目标上不抢锁不起工具。
- `scripts/run_pytest.py backend/agent/tests`：2570 passed（含 v1.3.10/11/13/19 既有回归）。
- `check_script_packages`（族树 sha == 1.3.20 登记）与 `check_tool_manifest --base origin/main`
  （append-only）通过。
- **真机已验证（2026-10-13 20:02–20:08）**：plan 80 / run 929 / job 188321，设备 id 953
  （`AHFFNX69****0170`）在 host 末段 `.86` 的直连口 `1-6`，目标 `P535AA-HONOR-NX2-16-261013V21`。
  `command_argv` 带 `-p 1-6`；`identity` 为 `adb_state=device` / `port_source=serial` /
  `gate_pid_scope=brom`；`gating.hidden=["1-5.2.2"]`（唯一那台 `0e8d:0003` 异常机），
  **同 host 9 台 `201c` 邻机未被压制**；`aborted=null`；`attempts=1` `outcome=ok`
  `exit_code=0`；`post_flash_verify.current == target`；`boot_stable.ok=true`；
  刷机步 302.46s；五步链 `preflight→flash→oobe_skip→ensure_root→check_device` 全绿，run `SUCCESS`。
- **批量已验证（run 932 / plan 81，同 host 9 台，41m22s 全绿）**：8 台真刷 + 1 台 `skipped`；
  其中 **5 台是 hub 嵌套口（`1-5.x.y`）**，逐台 `bound_port == 目标口`、`aborted=null`；9 个 job
  的门控都是 `gate_pid_scope=brom`，7 个 `hidden=[]`，另 2 个只压了现场异常机
  （`1-5.2.2` / `1-8`）；批量后该 host 10 个 MTK 口全部 `201c auth=1` 且 serial 可读。
  ⇒ 原先记在这里的「hub 形态未取证」一项**已关闭**。
- **仍未做的验证**：① 混跑窗口（同 host 有其它专项在跑）下的 V6 表现，两轮派发时该 host 上
  都没有其它作业；
  ③ run 921 受害的两台 `AHFFNX69****0169` / `****0224` 仍滞留 `0e8d:2000`，等现场长按电源/重插
  （本轮无新增伤害：这次邻机没有被压制）。

## Revisit

- 真机 canary 通过后，把 run 921 的失败样例补进 §4 参数表的"预期"文案；若 `-p` 在
  hub 嵌套口（`1-5.2.3` 形态）上的匹配与 sysfs 不一致，会出现"合法目标被误拒"——
  届时按 `metrics.identity` 判断是否改用工具侧拓扑口名，而不是放宽绑口核验。
- #3690 裁决 `201c` 全局移除后，删除 `gate_pid_scope` 的 `full` 档（过渡开关，不是终态）。
- 若救砖成为常规需求，`flash_port` 应改为平台侧可见的"按口指定"流程并留审计，而不是
  让运维在 Plan 参数里裸填口名。
