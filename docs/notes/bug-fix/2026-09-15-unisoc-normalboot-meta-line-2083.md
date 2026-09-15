# 展锐 uniview：normalboot-only 目录被 emit 成假异常（#2083）

Status: implemented
Class: bug-fix

## Decision

- **事件行判据收紧为「只认发生键」**：`collectors/unisoc.py` 的 `_absorb` 原先按
  `元数据键 ∪ 发生键` 认定「事件行」，于是真机 `Reboot.103000002` 的独立 meta 行
  （`event_name:"Boot Category"`）被当作发生行 → `fold_unievent_info` 非空 →
  目录被 emit 成假异常，并在 #1956 后入库即 `UPLOAD_PENDING` 自动上送。改为只认
  `kick_datetime` / `event_time` / `timestamp` / `reboot_reason`（toolkit
  `_handle_uniview` 同口径：只取发生键行）；随之失去唯一用途的 `_EVENT_LINE_KEYS`
  删除，模块与 `fold_unievent_info` 的语义注释同步。
- **「确定性不可上报」与「瞬时失败」分流**：`_emit_event` 由布尔改为三态
  （`emitted` / `not_reportable` / `failed`）——`parse_metadata` 抛 `CollectorError`
  属**确定性不可上报**（normalboot-only / 空文件 / 截断），调用方对它与 `emitted`
  同样落 `_processed[key] = signature`；其它 parse 异常与 emit 阶段失败保持
  `failed`、不落签名。否则判据收紧后该目录每拍重拉（`adb pull` + 占 host 提取
  信号量，即 #2083 描述的重拉面）。

## Alternatives

- **保留 meta 行进 occurrences、仅按 `reboot_reason` 过滤**：现状即此，假阳性已
  真机复现（`job_log_signal` 5745 / 5748：subtype `Boot Category`、`aee_ts=None`），
  不可行。
- **对 `Reboot.*` 目录整体跳过**：会连 `reboot_reason=kernel_crash` 等真异常重启
  一起丢（toolkit 台账：sysrq panic 有聚合包 tar 与 uniview 记录），不可行。
- **`CollectorError` 也按失败处理（不落签名）**：不误吞瞬时错误，但 normalboot
  目录每拍重拉；故按三态分流而非一刀切。
- **加目录名白名单/正则特判**：判据面外特判，且新类型目录会再踩；不做。

## Verification

真机语料（2026-09-15 只读复核；3 台 Z2581/Z2582 × 6 类事件，13 条 `unievent_info`）：

| 类型 | 修复前 | 修复后 |
|---|---|---|
| `JE`(Java Crash) / `ANR` / `NE`(Native Crash) / `Assert`(CP2 Assert) / `WCN`(WCN Assert)，10 条 | EMIT | EMIT（subtype / pkg / raw 逐字一致） |
| `Reboot.*`，3 条（独立 meta 行 + 发生行全部 normalboot） | EMIT `Boot Category` | DROP |

- 反例实证（双侧）：① 判据改回「元数据键 ∪ 发生键」→
  `test_unisoc_reboot_normalboot_only_is_not_reportable` 与
  `test_normalboot_only_dir_records_signature_and_stops_repull` 转红；
  ② `CollectorError` 归 `failed`（不落签名）→ 后者以「确定性不可上报必须落签名」
  转红。恢复后全绿。
- `scripts/run_pytest.sh backend/agent/tests -q` → **2050 passed**（含新增 5 条：
  真机形状解析、normalboot DROP、kernel_crash EMIT、旧文件名兼容、不可上报落签名
  后次拍不重拉、瞬时 parse 失败不落签名并重试）。
- `check:quick` → 10 gates 全绿。
- 夹具同步：5 个合成 meta-only 夹具改为真机形状（A 设备头 + B 元数据 + C 发生行）。
- 生产侧（只读）：该假阳性修复前两次进库/上送（signal 5745、5748，后者 DLE
  `REMOTE`），修复后下一个 Job 起该目录不再产生信号。

## Revisit

- **签名强度盲区（#2010 Revisit 延伸）**：签名 = 目录 size+mtime；`unievent_info`
  仅追加行不会改变目录 mtime → 若异常重启只追加行、不落新 tar，则不会被发现。
  normalboot 追加被忽略是期望行为；异常 reason 是否总有 tar 需补真机样本确认。
- **子事件粒度**：#2010 Revisit 未变——同一目录内多次异常只产生一条信号
  （目录粒度），如需「每子事件一行」需另立单。
- **同批在办边界**：本单只改「判据 + 落签名」；#2079（拉取失败推进签名）、
  #2080（消费侧 `nfs:{dir}` 去重并条）、#2040（先 emit 后落 processed 顺序）不在
  本 PR 范围。
