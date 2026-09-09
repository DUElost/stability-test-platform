# 首次上传后 prune 的完整性证据补强（#1083）

Status: implemented
Class: bug-fix

## Decision

`EventUploader._upload_one` 与 `_patch_state` 补强「prune 前中心副本完整性
证据」两条：

1. **源↔副本哈希对比**：fresh-copy 分支原来只读回 `_dir_sha256(dst)` 就写
   REMOTE 并 prune；现在拷贝后同时算 `_dir_sha256(src)` 并对比，不一致则
   `rmtree(dst)` 丢弃坏副本、`raise OSError` 走既有退避重试（不 REMOTE、
   不 prune）。与已有 `dst.exists()` 分支的对比逻辑（:333-341）同构。
2. **prune 以中心持久确认为门禁**：`_patch_state` 由 fire-and-forget 改为
   返回 `bool`（2xx=True；≥400/网络异常=False）。`_upload_one` 两路上
   REMOTE 确认失败时只告警、保留本地副本（服务端 state 停在 UPLOADING，
   由既有 `_retry_failed_loop` 稍后重补 REMOTE），不再无条件
   `_maybe_prune_local`。

`_maybe_prune_local` 内部语义不变（先 `rmtree` 成功再回写 PRUNED，PRUNED
恒表示本地已删）。HddSpill 强制 prune（`prune_after_upload=True`）在证据
齐备时行为不变；证据不足时宁可保留本地等重试，也不删唯一副本。

涉及：`backend/agent/event_uploader.py`、`backend/agent/tests/test_event_uploader.py`。

## Alternatives

- 仅靠读回哈希就 prune：NFS/CIFS 自读回不构成独立证据（#1083 现象原文）。
- REMOTE 失败仍 prune：中心未持久登记时删本地唯一副本，extract 永不
  可见；不可接受（HddSpill 溢出时正是最危险场景）。
- 重试语义改为丢事件终态（UPLOAD_FAILED 即弃）：丢数据，不做。

## Verification

- 新增：`test_fresh_copy_remote_ack_failure_keeps_local` /
  `test_existing_remote_ack_failure_keeps_local` / 拷贝不一致丢坏副本重试 /
  ack 2xx 后完整路径 prune 本地
- 既有 15 例全部保持通过（含 #1073 同 basename 隔离、missing-local 恢复）
- `TESTING=1 JWT_SECRET_KEY=test-secret DATABASE_URL=sqlite:///:memory:
  python3 -m pytest backend/agent/tests/test_event_uploader.py -q` → 19 passed

## Revisit

- REMOTE patch 多次失败时事件停留在 UPLOADING，靠 600s 慢速恢复循环重试；
  未加新的重试上限护栏（沿用既有 attempt 上限语义，落点是 UPLOAD_FAILED
  而非无限积压）。
- 中心响应未回显「实际存储的 checksum」，agent 端只以 2xx 为持久化证据；
  若要更强（防中心写错路径），可后续让 `/device-log-events` 响应回显
  `checksum` 并比对——属可选增强，未纳入本单。
