# #213 Track A dead code 清理与 stale 文档收口

Status: implemented
Class: simplification

## Decision

#311（#213 Track A 收口）确认与清理：

- **生产代码**：`backend/`（非测试）已无 `upload_events` / `upload_event_dirs`
  残留（grep 全量确认）；`UploadManager` 仅保留 `upload_scan_report`；
  `collect_upload_event_dir_names` **保留**——它是 upload_task 的 scan xls
  过滤模型（ADR-0028 方案 A）唯一来源，不属于死代码。
- **文档分级处理**：
  - 设计文档（implementation-spec §4.1）把「改前/改后」对照表改为「职责
    （现状）」单列表，不再出现 `upload_events`；
  - Sprint 4 验收矩阵与真机模板（历史验收记录）在头部加 #213 Track A
    banner，相关 AC/步骤标注为历史追溯，不改正文；
  - 审查文档（DEVICE_LOG_FLOW_REVIEW_2026-08-09）顶部 banner 补充
    #213/#300/#302 落地状态，链路图中 `upload_events` 分支标注「已删，
    历史快照」；
  - signoff §2 remaining-work 行更新为 #311 完成状态；
  - `.env.example` 注释去掉「已删」叙事，只写现状（EventUploader/DLE
    过滤模型）。
- **弃用 env 别名不删**：`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR`
  仍是未设主键 `STP_AEE_NFS_ROOT` 时的回落路径，fleet 迁移（#172）未完成前
  删除会让仅配别名的主机静默失去中心存储；保留代码与
  `docs/design/2026-storage-roles-and-aliases.md` 的「计划删除」标注。

## Alternatives

- 直接删除弃用 env 别名支持：fleet 未迁移完，风险不可控，未采用。
- 把历史验收/审查文档归档到 `docs/archive/`：相关 issue 与 signoff 有大量
  入链，归档会断链；保留原位 + banner 更可追溯，未采用。
- 逐行改写历史文档为现状：破坏审查快照的「当时证据」语义，只对设计文档
  改现状、对历史文档加 banner，未采用逐行改写。

## Verification

- `grep -rn "upload_events|upload_event_dirs" backend --include='*.py'`
  （排除测试）零残留；测试中的 `count_pending_upload_events` 是 upload_task
  过滤模型的一部分，保留。
- 文档 grep：`upload_events` 仅出现在历史验收/审查文档的 banner 或
  「历史快照」标注中，非 archive 生产路径不再描述为现状。

## Revisit

#172 fleet 迁移全部切到 `STP_AEE_NFS_ROOT` 后，删除
`STP_AEE_CIFS_ROOT` / `STP_WATCHER_NFS_BASE_DIR` 回落逻辑并同步 AGENTS.md、
storage-roles 文档与本 note。
