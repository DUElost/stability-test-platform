# check 判死离线豁免 + gpu_check protobuf 字段级解析（#814 #746）

Status: implemented
Class: bug-fix

## Decision

新增两个脚本版本（已发布版本不可原地改）：

1. **#814-2 判死缺就绪/在线豁免（sleep_check v1.0.3、gpu_check v1.0.8）**
   `_lib.py` 新增 `device_online()`（`adb get-state == device`）。`_run` 的
   dead_streak 累计改为三分支：alive→清零；**设备离线/adb 拥塞→不累计、不判死**；
   仅在线且服务不存活才 +1。原先设备离线时服务探测必然为空 → dead_streak
   累积，一次意外重启 / 慢 boot / USB 节能断连超一个 patrol 周期即假死。
   （#814-1「powercycle_check 缺 boot_completed 就绪判定」经核查在 v1.0.8 已
   含 offline→online 不累计 + `stat -c %Y`，不重复修改。）

2. **#746 gpu_check protobuf `test_result` 字段级解析（v1.0.8）**
   `_last_protobuf_test_result` 由「`rfind` 后 64B 窗口 `b"true" in window` 子串
   扫描」改为**按 protobuf 长度前缀取值**：定位字段名后跳到 wire tag `\x12`，
   读长度字节，按长度截取并精确比较 `true`/`false`。原先最后一条
   `test_result=false` 的后续 64B 内若出现 `true` 文本（stacktrace 等）会误判
   成功——危险方向已被消除；找不到合法字段返回 None（仍走既有 no-tests 判定）。

纯行为变更，无 `default_params`/`param_schema` 变化 → 无需 seed 迁移。

影响面：`sleep_check/v1.0.3`、`gpu_check/v1.0.8`（含各自 `_lib.py`）+
`tests/test_check_exemption_protobuf_814_746.py`。

## Alternatives

- #746：用 8B/16B 窗口缩小子串匹配——仍依赖字节巧合，未解决「窗口内 true 子串
  优先」；字段级长度前缀是 protobuf wire format 的正确解析。
- #814：只在脚本侧「离线不判死」而不加在线判定——离线/服务死无法区分；必须
  以 `get-state` 先判在线。
- 原地改旧版本——违反不可变门禁并使在途 Plan precheck `script_verify_failed`。

## Verification

- `tests/test_check_exemption_protobuf_814_746.py`：最后 false 后续含 true 文本
  → 仍判 False；length-prefixed false 后跟无关 true 字节 → False；无字段→None；
  sleep/gpu 均含 device_online 门禁；新旧版本并存。7 passed。
- `check-script-version-immutability.py --base origin/main` 通过。
- 脚本目录由 `ruff.toml` extend-exclude 排除（与既有脚本一致）。

## Revisit

- #814-1 的 powercycle_check 已在 v1.0.8 处理；#761（powercycle v1.0.6 判死边界
  tech-debt）为文档性条目，未纳入本 PR。
- #809 monkey 看门狗链（aimwd is_file、monkey_check 重启后 ps 验证、monkey_test
  推送 rc 计错）仍开放，另开 PR。
