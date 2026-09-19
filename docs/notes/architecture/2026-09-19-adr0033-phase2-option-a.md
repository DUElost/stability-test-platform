# ADR-0033 Phase 2 选项 A：B5 `DedupMergeEngine` 包现态 merge CLI

Status: implemented
Class: architecture

## Decision

**选定选项 A**，解除 v1.4 阻塞：第一个控制面 `DedupMergeEngine` 包 **B5** 现态
`start_log_scan -merge_files_list`（`STP_BACKEND_DEDUP_SCAN_*`），mtk/unisoc 同一引擎
（ADR-0032 D3）。**Scan-Result-GT 继续只做 Agent B2**（`scan_result.py -d`），不插进
merge 循环。ADR-0033 升 **v1.5**：纠正「unisoc GT = DedupMergeEngine」措辞；落地薄适配器
`backend/services/dedup/`；`dedup_scan.build_merge_argv` 经引擎门面，编排位置不变。

### 接缝（正确）

| 阶段 | 工具 | 宿主 | 本轮 |
|---|---|---|---|
| B2 主机汇总 | UNISOC: Scan-Result-GT `-d`；MTK: `-dedup_org` | Agent | **不改** |
| B5 多 host merge | 两平台：`start_log_scan -merge_files_list` | 控制面 | **薄 ACL** |

### 明确不做

- 包存储 / Phase 3 / 设备端 Tool Contract 样板
- 修订 D3 换独立 unisoc merge 工具（选项 B）
- 把 Phase 2 主样板改成 Agent GT Adapter（选项 C）
- 新增工具私有 env 键；开 auto-merge

## Alternatives

| 选项 | 为何不选（本轮） |
|---|---|
| B | 须修订 Accepted D3；GT 仍无多文件 merge argv |
| C | 兑现「GT 适配器」字面，但不兑现「插在 per-platform merge 循环内」 |
| 继续阻塞 | Owner 已拍 A |

## Verification

- 单元：`backend/tests/services/test_dedup_merge_engine.py`（两平台同一引擎；argv ≡ `-merge_files_list`；源码不含 GT）
- 回归：既有 `test_dedup_scan_merge.py`（门面 API 保持）
- `python3 scripts/run_gates.py check:quick`（本 PR 范围）
- ADR-0033 v1.5 §1.1 / §4 / §5.5；阻塞笔记标注 Resolved→A

## Revisit

- 选项 B 仅当上游提供多文件 merge 契约且愿意修订 D3
- 选项 C 可作为**额外** Agent 侧 ACL，不替代本 B5 样板
- 包存储仍按 §5.4 三条触发条件
- merge 执行位置迁移（ADR-0027 B1）不在本 PR
