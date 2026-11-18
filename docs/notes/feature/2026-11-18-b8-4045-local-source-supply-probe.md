# B8 #4045 阶段二前置：本地原件供给缺口与 G6 复用边界（探针取证）

Status: proposed
Class: feature

日期：2026-11-18　基线：`origin/main@e7325af0372a9b202a283b0893c040e4415b877c`　批次：#3800 / #4045
交付形态：仅取证探针（不含实现），供给接口待 Owner 最终签字
判据来源：#3800 §2.1 Owner 裁定（评论 6975368043）第 2 条、#4045「真实入口与验收」第 1 条前半

## Decision

本单只交付**取证探针**，不交付供给实现；实现等 Owner 对 §Revisit 列出的接口签字后再做。

1. 探针落在 `backend/tests/services/test_b8_4045_supply_probe.py`，四条判据都必须由真实
   入口或真实服务函数产生，不得用 mock 替代复制/校验：
   - **P1 基线缺源**：仅 Agent 持有 I4 期内原件（DLE 非 `PRUNED`、`remote_path` 为空、
     中心无 `VERIFIED` 发布、无 HddSpill 副本）时，真实路由
     `POST /api/v1/plan-runs/{run_id}/dedup/compat-rerun` 只能把这些发生登记为
     `missing_after_prune_or_unavailable`，`covered_events=0`、`is_partial=true`，
     `compat_reruns/{run}/{platform}/r{id}/events/` 下零副本，XLS 的 `Path` 为空。
   - **P2 差异只在入口**：同一份数据在服务级显式给出 `local_source_roots` 后，同一发生
     即按 `agent_local_original` + `i4_local_protected` 取得并落隔离目录。缺的是**入口
     接线与授权来源**，不是复制算法。
   - **P3 G6 通路不可原样复用**：`ensure_partition_publications` 只登记 S7 已选代表
     （实测 `created==1`，Agent 待办里没有非代表发生）；强行替非代表登记发布记录后，
     真实 `record_agent_receipt` 仍按 `EVIDENCE_NOT_REPRESENTATIVE` 拒绝；代表走完整
     上送+校验后重跑补跑，只能救回代表，非代表仍是缺源。
   - **P4 可复用内核与族无关**：`copytree_no_symlinks` + `dir_sha256_and_size` 的
     「复制后按字节重算哈希复核」对 `compat_reruns/` 隔离目录同样成立；越根目的、
     含 symlink 的原件一律拒绝且不留半成品；`parse_evidence_rel_path` /
     `resolve_evidence_event_path` 只接受 `evidence/` 前缀，供给副本借用该契约必红。
2. 结论写进 #4045 评论交 Owner 签字：供给必须是「绑定单次 CompatRerun 的异步上送」，
   目的地为本次 `compat_reruns/{run}/{platform}/r{id}/events/`，**不得**写入 `evidence/`
   主链，**不得**新增按任意路径读远端 Agent 磁盘的通用拉取接口。

## Alternatives

- **控制面直读 Agent 磁盘（通用 pull）**：Owner 已明确不批准；且需要新凭据面与路径白名单，
  风险大于收益。
- **扩展现成 G6 `EvidencePublication`**：被 P3 否证——发布记录身份是「分区 × 代表 DLE」，
  目的地契约是 `evidence/`，VERIFIED 还会触发归档与交付重算；补跑需要交付版本范围内全部
  发生，且副本必须留在可独立回收的派生族里。
- **只把 `local_source_roots` 从路由透传（配置一个 host→本地根映射）**：P2/P4 显示复制与校验
  本来就通，但生产控制面与 Agent 不同机、本地根不可达（15.4 只挂中心侧），透传等于假设
  「中心能看见 Agent 盘」，与不批准的 pull 同形态。
- **同步供给（在 trigger 请求内复制）**：原件可达数百 MB，同步会把 Owner 的一次点击变成
  长事务与长持锁窗口，且失败没有尝试/退避台账；改为异步供给 + 同 `request_key` 幂等重放。

## Verification

- 环境：隔离 PG16（testcontainers）+ 临时文件根；每条探针另用独立会话核对台账。
  `./scripts/project_python.sh scripts/run_pytest.py backend/tests/services/test_b8_4045_supply_probe.py -q`
  → `4 passed`（2026-11-18，基线 `e7325af03`，未改任何被测源码）。
- 判据引用的调用点（逐条实读，非推断）：`backend/services/compat_rerun.py`
  `_resolve_dle_compat_source` 第 4 步与 `trigger_compat_rerun` 签名；
  `backend/api/routes/dedup.py::trigger_partition_compat_rerun`（未传 `local_source_roots`）；
  `backend/services/evidence_publication.py` `ensure_partition_publications` /
  `list_agent_tasks` / `record_agent_receipt`；`backend/agent/evidence_uploader.py`
  `_copy_to_destination`；`backend/agent/contracts/evidence_layout.py`。
- run 级留存锚点对拍：现行清理候选是 `PlanRun.status ∈ {SUCCESS, FAILED, PARTIAL_SUCCESS}`
  且 `started_at < cutoff`（`backend/scheduler/cron_scheduler.py:455`），派生族判定必须用同一
  锚点；按 run 的无条件清理只遍历 `RUN_FAMILIES`（`cron_scheduler.py:315`），因此
  `compat_reruns/` 归 `DERIVED_FAMILIES` 即天然被排除。
- 未执行（不得当作已完成）：获批供给入口后的正例（等接口签字）；生产实机供给与 Agent 侧
  真机复制；试点清理与四路对拍（候选目录 ↔ 原交付 ↔ 证据 ↔ 共享源）；`check:quick` 与
  六项 required CI 的结论以本 PR 的 CI 为准。

## Revisit

等 Owner 就以下四点签字后再实施，实施 PR 必须复用本探针并补「获批方案经真实供给入口取得
同一发生」的正例：

1. 供给记录形态：新增 `compat_rerun_supply` 表（建议：领取需 `FOR UPDATE` + attempts 退避）
   vs 复用 `CompatRerun.supply_records` JSONB。
2. 触发形态：显式供给端点 + 同 `request_key` 重放（建议）vs trigger 内自动登记并异步等待。
3. Agent 侧通道：沿用 G6 姿势的 Agent 轮询待办（建议）vs 新增控制面→Agent 命令推送。
4. 单次供给上限与超时（事件数/字节数/退避），以及两个留存口径待裁决：`FAILED` 补跑产物
   是否可回收；已回收后的台账行在 Dry-run 中是否需要「已回收」标记，避免
   `artifact_absent_or_unsafe` 噪音掩盖真实缺口。
