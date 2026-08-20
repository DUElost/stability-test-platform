# inotifyd-only DLE 创建/上送验收覆盖

Status: implemented
Class: testing

## Decision

#310：为「关 Reconciler 只走 inotifyd」兜底路径补自动化验收覆盖，真机 E2E
保留为残余。

- 新增 `backend/agent/tests/test_device_watcher_dle.py`，直接驱动
  `DeviceLogWatcher._on_pull_done` 覆盖 `_maybe_register_device_log_event`
  三个场景：
  - pull 成功 → `DeviceLogEventClient.create_local_event`（local_path 可用、
    link_signal_seq_no 非空）+ `EventUploader.enqueue_local_event`；
  - pull 失败（空 enrichment）→ `create_pull_failed_event`，信号仍落 outbox；
  - reconciler 激活 → inotifyd 路径不建 DLE、不落信号（由 reconciler 独占）。
- 外部依赖全 mock（DeviceLogEventClient / EventUploader / 平台探测 /
  collector），不依赖真实 HTTP/ADB。
- signoff §4 的 inotifyd 行更新为「自动化覆盖见 #310；真机 E2E 待实验室」。

## Alternatives

- 直接真机验收：当前实验室/排期不可用，且无自动化会丢失回归保护，未采用。
- 只补文档不补测试：inotifyd → DLE 注册路径（ADR-0028 D1）没有任何断言，
  后续改动可能静默破坏，未采用。

## Verification

- `pytest backend/agent/tests/test_device_watcher_dle.py` → 3 passed；
- ruff check 通过。

## Revisit

实验室可执行时按 #310 程序做真机 E2E（Reconciler off/suppressed → inotifyd
触发 pull → DLE `UPLOAD_PENDING → REMOTE` → `job_log_signal.device_log_event_id`
关联），通过后关闭 #310 并更新 signoff §4。
