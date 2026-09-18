# ADR-0032 D10：展锐 DLE 终态语义与 MTK 同构（#463 裁决）

Status: implemented
Class: architecture

## Decision

#463（方向 4：展锐日志监测与汇总去重合入）在 2026-09-14 评论中遗留的归档语义
A/B 取舍，按 owner 确认的方向（**展锐日志链逻辑与 MTK 平台保持一致**）裁决并入
ADR-0032 v1.0 §D10：

- **DLE 终态判定两平台同义同判**：`ARCHIVED` 统一为「事件目录已由 run 级 extract
  复制进 `jira/{plan_run_id}/` 产物束」，触发与判据全仓唯一
  （`run_extract_sync` → `mark_events_archived`），不引入任何平台分支。
- **否决 A「上送即归档」**：会让 `ARCHIVED` 在两平台含义分叉，与一致性方向冲突。
- **B 的目标（状态机语义一致）按已落地形态确认**：达成路径是「给展锐补齐等价输入
  链」（D4c UnisocScanRunner → scan_result.py → 同一 merge 工具 →
  `merge_result_xls`），而非 B 原设想的「归档层开无 xls 分支」——归档层零改动。
- **实现层残余差异**（scan 输入来源、上送触发源、表头归一）登记于
  `2026-scan-upload-merge-contract.md`「DLE 归档」节，明确不构成状态机差异；
  同时更正该节「UNIVIEW 平台永不归档」的过期表述（展锐 GT 工具链配置后已不成立）。
- #463 随裁决关闭；残余项（Watcher 冒烟 / 实时性 inotifyd 评估）收窄跟踪于
  #1998「P2 实时性」。

## Alternatives

- **A「上送即归档」**（#463 09-14 评论选项 A）：实现最省，但 `ARCHIVED` 语义分叉，
  运维口径与报表都要带平台特例；与本轮「与 MTK 一致」的方向相反，否决。
- **B 原设想「saq_tasks 归档层加无 `_org.xls` 分支」**：需要改归档链并回归 MTK
  既有行为；事实上 #1946/#1957 + D4c 已让展锐产出等价 scan 输入，归档层无需分支，
  该方案失去必要性（保留其目标、采纳其达成态）。
- **维持「永不归档」现状并只写文档**：会把「工具链未配置的 run」误升格为平台终态
  语义，与 09-18 实测（25 条 `ARCHIVED`）矛盾，否决。

## Verification

- 代码事实（origin/main `63e28842`）：`backend/services/dedup_extract.py`
  `run_extract_sync` 查本轮 `merge_result_xls`（平台无关）→ 复制事件目录 + merge xls
  进 jira bundle → `mark_events_archived`（`backend/services/device_log_event.py`），
  全仓唯一 `REMOTE → ARCHIVED` 写入点；Agent 侧 `unisoc_scan_runner.py` 产出
  `platform_subdir="unisoc"` scan 产物，merge 路由按 `merge/{platform}/` 分区。
- 生产证据（引用既有核对，不重复实测）：#2253（run 400/399/397 发布路径三判据，
  DB+盘上双重确认）、#1998 2026-09-18 回写（UNISOC `REMOTE` 660 / `ARCHIVED` 25，
  推进 7–22s）。
- 门禁：`python3 scripts/run_gates.py check:quick`；
  `python3 tools/dev/check_governance_surface.py --check`（本 Agent Note 与 ADR/README
  结构）。

## Revisit

- **Watcher 冒烟 / 实时性**：展锐启用 `InotifydSource`（传 uniview 根目录）的设备
  支持度与写盘时序需真机实测（目标：事件可见延迟 < 5s 且与 reconciler 不重复建行）
  ——跟踪 #1998「P2 实时性」。
- **表头命名长期风险**（工具改版可能「列数同、语义错位」）：维持 ADR-0032 §Revisit
  既有触发条件（工具升级/新增平台时以「输出表头比对」重跑 spike），无新增待办。
