# ADR-0025 log-flow-sequence §4.3/§4.4 对齐方案 A

- **状态**：已实施
- **类别**：process
- **日期**：2026-08-20
- **关联**：#304、ADR-0028 方案 A

## 决定了什么

`docs/design/2026-adr-0025-log-flow-sequence.md` §4.3（决策表）与 §4.4（Mermaid
时序图）仍描述「Scan 直接 copytree 事件目录」的重构前路径，与 §2/§4.1/§4.5 及
生产代码矛盾。本次把两节改为方案 A 路径：

- 通道 A：`scan_task → upload_task（LOCAL→UPLOAD_PENDING）→ EventUploader
  （30s 轮询 copytree → REMOTE）→ extract（按 DLE remote_path）→ ARCHIVED`。
- 通道 B（spill）：`list_events(state=LOCAL) → enqueue_local_event(force=True)
  → EventUploader copytree → REMOTE → PRUNE_LOCAL`，不再是 HddSpill 直接
  copytree/rmtree。

`docs/adr/README.md` 的 ADR-0028 摘要同步为「upload_task=筛选者、
EventUploader=执行者、CONTINUOUS=0/1 语义」。

## 放弃的备选

- §4.3/§4.4 保留历史叙事并加 banner：两节都是「当前实现」语义而非历史快照，
  同文件内自相矛盾比「过期文档」更误导，直接对齐。

## 如何验证

- §2 与 §4.3/§4.4 的 Mermaid/决策表逐行比对（upload_task + EventUploader
  pull 模型，Spill 仅 enqueue）。
- `rg 'Scan->>CIFS: copytree|仅追踪不上传' docs/design/2026-adr-0025-log-flow-sequence.md docs/adr/README.md`
  无命中。

## 何时重议

- scan/upload/merge 链再次变更时（如 EventUploader 改 push 模型），§2/§4 必须
  同 PR 同步更新。
