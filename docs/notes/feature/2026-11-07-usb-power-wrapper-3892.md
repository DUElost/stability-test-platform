# USB 切电提权窄面 `usb-power`（#3892 U2）

Status: implemented
Class: feature

## Decision

规格是 [ADR-0063](../../adr/ADR-0063-vbus-cycle-flash-without-adb.md) 的 D1、D2，对应 ADR-0037 v0.7 草案 D8。

- `backend/agent/stp_agent_priv.py` 新增 `usb-power --serial <s> --expect-slot <loc>:<port> --action {on|off|cycle} [--delay 1..30]`。
  wrapper 按 serial 查 `/etc/stp-agent/usb-power-slots.json` 得到 `(hub_location, port)`；调用方不能指定 Hub 位置或端口。
  `--expect-slot` 必填，只用于比对：与查表结果不一致时 wrapper 在调用 uhubctl 之前拒绝（exit 2，`STP_USB_POWER_SLOT_MISMATCH`），
  切电目标仍只来自映射。这样映射在脚本与 wrapper 两次读取之间被改写时，不会先切错一次（复核 #3899 comment 6708089815）。
- 映射文件整份校验：
  - `schema=1`，每个条目只有 `serial`、`hub_location`、`port` 三个字段；
  - `hub_location` 沿用 `usb-authorized` 的端口名正则，`port` 取 1–16；
  - serial 重复或槽位重复时，整份文件拒绝。
- 文件、父目录、`/usr/sbin/uhubctl` 都必须 root 属主，且不得 group/other 可写。文件以 `O_NOFOLLOW` 打开，大小上限 64 KiB。
- Hub 位置必须解析到 `/sys/devices` 下的同名真实目录，判定沿用 `is_kernel_usb_device_path`。
- argv 固定为 `uhubctl -l -p -a [-d]`，不经 shell；环境只留 `LC_ALL` 和 `PATH`；超时 60s。
- 执行失败和超时都 exit 3，并带 `STP_USB_POWER_*` 标记；参数被拒 exit 2。`capabilities` 与 selftest 契约表都已加入 `usb-power`。
- 新增 `tools/ansible/playbooks/ensure_usb_power_slots.yml`：
  - 从 host_vars `stp_usb_power_slots` 写映射文件（root:root 0644），并安装 `uhubctl`；
  - 校验已装的 wrapper 具备 `usb-power`。wrapper 本体仍由 `update_agent.yml` 下发。
- `tools/dev/stp_agent_priv_smoke.sh` 新增第 11 段，覆盖拒绝面。

## Alternatives

- `--location` / `--port` 参数（#3892 原写法）：没有采用。sudoers 不约束 wrapper 参数（ADR-0037 D6），
  这样写等于允许 Agent 用户断开任意 Hub 口。
- 映射文件放在安装目录或 `.env` 里：没有采用。这两处 Agent 用户可写。
- `cycle` 不带 `--delay` 时强制报错：没有采用。缺省交给 uhubctl 的默认值；刷机脚本切电走 `off` 加脚本侧等待，不依赖 `cycle`。

## Verification

- `python3 -m pytest tests/test_agent_priv_usb_power.py -q`：60 passed。覆盖：
  - 解析层拒绝面；
  - 纯函数校验与映射文件整份拒绝；
  - symlink、可写文件和目录、属主不符、可写 uhubctl、Hub 位置不在内核树；
  - 固定 argv、环境清空、非零退出与超时；
  - playbook 路径和形态与 wrapper 同源。
- `python3 -m pytest tests/test_agent_priv_*.py tests/test_remote_script_privilege_paths.py -q`：
  203 passed，11 skipped，1 failed。失败的是 `test_wrapper_sync_env_emits_control_plane_sentinels`：
  云端环境缺 `sqlalchemy`（`ModuleNotFoundError`），与本改动无关，以 CI 为准。
- `ruff check` 通过。
- 加 `--expect-slot` 后：`pytest tests/test_agent_priv_usb_power.py -q` 88 passed，`pytest tests/ -k agent_priv -q` 235 passed、2 skipped；
  去掉比对后 `test_slot_mismatch_is_rejected_before_any_power_action` 两条变红。
- 复核 #3900（comment 6706287225）后补：
  - 映射文件顶层只接受 `schema`、`slots` 两个键，`schema` 必须是整数 1（`true`、`1.0` 被拒）；Hub 位置解析结果还必须是目录；
  - 用例单独锁住 uhubctl 属主、映射文件属主、父目录属主、64 KiB 边界（65536 字节接受，65537 拒绝）、
    `timeout=60` 与环境精确键集、非 root 拒绝；
  - 变异检查：分别去掉 uhubctl 校验、文件属主、目录属主、`timeout=`、`_require_root()`、两处大小检查，各有用例变红；
  - smoke 第 11 段对每个坏参数断言 `rc -eq 2`；
  - `pytest tests/test_agent_priv_usb_power.py -q`：70 passed；`pytest tests/ -k agent_priv -q`：217 passed，2 skipped。
- playbook 的 capabilities 检查保持只报告不阻断：映射文件可以先于 wrapper 下发，使用侧（ADR-0063 D3 第 9 条）在取锁前 fail-closed。
- 真机 uhubctl 切电：pending，需要现场 Hub（ADR-0063 §8 第 1 项）。

## Revisit

- ADR-0063 转 Accepted 时，必须在 ADR-0037 §4 不变量第 2、3 条补上本子命令和测试文件。
- 第二期槽位入库后，重审映射来源（ADR-0063 §7 第 1 条）。
