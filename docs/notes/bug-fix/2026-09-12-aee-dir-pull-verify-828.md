# LogPuller AEE 目录「创建即拉」伪成功（#828 / R10-F03）

Status: implemented
Class: bug-fix

## Decision

inotify 非递归监听下，AEE/VENDOR_AEE 目录型 artifact 在设备侧目录刚创建时即触发 pull，
此时 `.dbg` 等关键文件可能尚未落盘；`adb pull` 返回 0 且本地目录存在即被当作成功上送，
导致空目录或半成品现场被标记为 pulls_ok。

**修复**：`LogPuller._pull_aee_directory_verified` 在首次 pull 后复用
`aee.processor._verify_pulled_aee_log_strict`（非空 + 至少一个 `.dbg` + 总大小 > 0）；
未通过则删除本地目录、有限次重拉（`STP_WATCHER_AEE_PULL_VERIFY_ATTEMPTS` 默认 3，
`STP_WATCHER_AEE_PULL_VERIFY_DELAY_SECONDS` 默认 2.0），全部失败则 `pulls_failed` 并
`on_done(event, {})`。非 AEE 目录型 artifact 与单文件 pull 行为不变。

## Alternatives

- **仅延迟 pull、不做 verify**：无法覆盖「目录已存在但内容仍不完整」；
- **在 watcher 层递归 inotify**：改动面大，与 #828 最小修复目标不符；
- **新建独立 verify 函数**：与 processor 已有 strict 规则重复，直接复用保持一致。

## Verification

- `python -m pytest backend/agent/tests/test_puller.py -q`
- 新增：`test_directory_pull_retries_until_aee_dbg_present`、
  `test_directory_pull_fails_when_aee_dir_stays_incomplete`
- 既有目录/quota 用例补 `.dbg` fixture 以通过新 verify 闸门

## Revisit

- 若 inotify 升级为递归或 reconciler 侧保证完整性，可评估是否收窄重拉为仅首次 create 事件；
- 与 #829–#831 同属「失败不显性/伪成功」族，长期可考虑类级 contract test。
