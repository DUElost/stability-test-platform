# 短操作脚本 PROGRESS 打戳评估（#1748）

Status: implemented
Class: bug-fix

## Decision

对 #872 / #1690 台账剩余 9 个短操作脚本：**维持现状，不补 `progress_stamps`
声明、不单独 bump 版本**。

判据（issue 正文）：仅当慢设备/异常网络下实测可 **> 停滞钟建议值（≥120s）**
才 fork 新版本打戳；否则虚假声明会打开 `stall_seconds` 门禁，制造误杀面。

| 脚本 | 墙钟上界（代码读出） | 相对 120s | 结论 |
|---|---|---|---|
| `noop/v1.0.0` | 瞬时 | ≪ | 不打戳 |
| `check_device/v1.0.0` | 单次 adb ~10s | ≪ | 不打戳 |
| `ensure_root/v1.0.0` | 短重试 + sleep 秒级 | ≪ | 不打戳 |
| `connect_wifi/v1.0.2` | 默认 `timeout_seconds=30` + 10s 连通轮询 | < | 不打戳 |
| `aee_signal_trigger/v1.0.1` | 默认 `poll_timeout_seconds=30` | < | 不打戳 |
| `stop_aimonkey/v1.0.1` | kill/force-stop 秒级 | ≪ | 不打戳 |
| `monkey_teardown/v1.0.1` | pull timeout 可达 120s，无长轮询主路径 | ≤边界 | 不打戳（无实测超阈） |
| `monkey_check/v2.0.3` | 重启看门狗窗口默认 **60s** | < | 不打戳 |
| `monkey_test/v1.2.2` | 媒体 push 单文件 timeout **300s**（理论） | 理论可超 | **仍不打戳**：未做真机慢网实测；声明 stamps 会让 Plan 可开 stall，反而扩大误杀面。若后续专项实测 push 常 >120s，与该脚本其它变更合并出新版本再声明 |

协议扫描方法：以字面量 `PROGRESS `（或 `capabilities.json` 含
`progress_stamps`）为准——勿仅用 `progress_stamp|heartbeat|tick` 符号名 grep
（会漏自研 `_emit_progress`，见 issue 方法备注）。上述 9 目录均无
`capabilities.json`、无 `PROGRESS ` 协议串。

对照：长耗时批次已声明的脚本（如 `install_apk/v1.1.0`）为
`{"capabilities": ["progress_stamps"]}`。

## Alternatives

- **一律补戳「预防」**：否决——秒级脚本开 stall 无收益、有误杀。
- **只给 monkey_test 补戳**：否决——缺慢网实测；且媒体 push 失败路径已有
  timeout/错误返回，不依赖停滞钟。

## Verification

- 仓库静态读：九目录墙钟上界与 capabilities / `PROGRESS ` 扫描（本 Note 表）
- 无代码行为变更；`python scripts/run_gates.py check:quick`

## Revisit

若任一脚本在现场日志中出现墙钟 ≥120s 的成功/失败样本，或与该脚本专项
新版本需求合并时，按 #1690 模式补 `progress_heartbeat`/`progress_tick` +
`capabilities.json`。`monkey_test` 媒体 push 优先观察。
