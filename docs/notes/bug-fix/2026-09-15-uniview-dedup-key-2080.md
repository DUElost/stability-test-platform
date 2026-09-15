# #2080 UNIVIEW 去重键补事件身份（消费侧 #2010 残留）

Status: implemented
Class: bug-fix

## Decision

`backend/api/routes/plan_runs.py` 的 `_aee_event_dedup_key` 拆分 UNIVIEW 分支：
UNIVIEW 走**新函数** `_uniview_dedup_key(nfs_path, extra)`，键为
`nfs:{dir}#{event_subtype}#{aee_ts}`；AEE / VENDOR_AEE 保持原目录键 `nfs:{dir}` 不动。
补 2 例测试（1 正 1 负向对照）。

## 缺陷确认（回源码，非仅采信 issue）

issue 的两条证据经复核**均属实**：

| 证据 | 位置 | 复核 |
|---|---|---|
| 控制面按目录去重 | `plan_runs.py:2811`（原）`if category in {"AEE","VENDOR_AEE","UNIVIEW"} and nfs_path: return f"nfs:{nfs_path}"` | ✅ 原文一致 |
| Agent 侧写的是**事件目录** | `backend/agent/aee/unisoc_reconciler.py:513` `"nfs_path": str(event_dir)` | ✅ 原文一致 |

**关键补充（issue 未明说、但决定修复方向）**：Agent 侧经 **#2010** 已改为
「**签名变化就再发射一条**」（`unisoc_reconciler.py:249-253`：`prev == signature` 才 skip）——
即**同一目录本可产出多条信号**，而消费侧按目录去重把它们**并回一条**。
故这是 **#2010 在消费侧的对侧残留**，不是独立新缺陷。

**为何 AEE/MTK 不受影响**：其 `nfs_path` 指向**单事件产物**，目录键**等价于**事件键；
UNIVIEW 的目录是**容器**，键因此不唯一。

## 修复取向

**键补事件身份，不补集合语义**：

- 事件身份取 `event_subtype` + `aee_ts`——二者由 `unisoc_reconciler._emit_event` 一并写进
  `extra`（`aee_ts` 为设备时钟原文，#785），**无需新增字段或改 Agent**；
- **两项皆缺失时退化为纯目录键**（与 #1956 一致）——避免因字段缺失把同一条事件重复计数；
  该退化路径已在测试中钉住（负向对照即走此路径之外的完全相同身份）。

**修复覆盖面**：`_aee_event_dedup_key` 被 `_load_deduped_aee_events` 使用，后者有**两个**
消费者——`crash-details`（`:2600`）与 `_aggregate_aee_dashboard_sections`（`:2706`，
喂 watcher-summary）。**一处改动即覆盖 issue 所列的全部受影响面**（仪表盘 / watcher-summary /
crash-details）。

## Alternatives

- **改 Agent 侧 `nfs_path` 为具体异常文件路径** → 否决：`nfs_path` 的语义是「事件目录」，
  被多处消费（上送/归档/审计）；为去重而改其语义会波及无关面，且需改 Agent 与兼容存量。
- **键改用 `signal_id`（放弃去重）** → 否决：那会**破坏 #1956**——同一条异常被多次 run
  拉取时会重复计数（#1956 正是为此引入 nfs_path 去重）。
- **键改用 `event_subtype` 单字段** → 否决：同一目录内**同 subtype 的不同时刻**异常
  （如两次 Java Crash）仍会被并成一条；`aee_ts` 是其区分依据。
- **AEE/VENDOR_AEE 一并加事件身份** → 否决：其目录键**本已唯一**（单事件产物），
  加了只会增加键长度与字段依赖，无收益且有「字段缺失导致去重退化」的风险。
- **在 `_prefer_deduped_event` 上改保留策略** → 否决：那是**同键**之间的择优（entry_origin /
  package_name / detected_at），与「是否同键」正交；本缺陷的根因在键的构造，不在择优。

## Verification

- `python -m pytest backend/tests/api/test_plan_run_aggregation_endpoints.py -q`
  → **64 passed**（含新增 2 例）；
- **红绿双向（本单核心）**：把 `_aee_event_dedup_key` 临时还原为 #1956 的合并分支
  （`if category in {"AEE","VENDOR_AEE","UNIVIEW"}`）→
  `test_crash_details_uniview_same_dir_distinct_events_kept` **失败**（两条被并为一条），
  而负向对照仍通过；修复后 **2 passed**；
- **正向**：同目录、`event_subtype` 不同（Java Crash / Native Crash）→ 两组分别保留
  （断言 `{subtype} == {"Java Crash","Native Crash"}`）；
- **负向对照**：完全相同的 `event_subtype` + `aee_ts` → 仍去重为 1 条（#1956 语义未被破坏）；
- `ruff check` 两文件 → All checks passed；
- `python tools/dev/check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Revisit

- **退化路径的精确条件**：仅在 `event_subtype` **与** `aee_ts` **两者皆空**时退化为纯目录键。
  实测 `unisoc_reconciler.py:508-512`：`aee_ts = meta.device_timestamp_raw or (…isoformat() if … else None)`
  ——**可能为 `None`**；但 `event_subtype`（`:506`）来自 `parse_metadata`，缺失时事件本身
  不可上报（`_EMIT_RESULT_NOT_REPORTABLE`）。故**正常路径下至少 `event_subtype` 非空**、
  复合键生效；退化仅覆盖「历史/异常数据两者皆空」的窄面。若需回溯修正存量，
  应独立评估（本单不做数据迁移）。
- **键语义的文档化**：`nfs:{dir}#{subtype}#{ts}` 是**去重键**而非对外契约，未写入文档；
  若后续有第三方消费该键（如导出），需先固化格式。
- **同目录同 subtype 同 `aee_ts` 的不同异常**：理论上仍会被并（键相同）。当前 Agent 侧
  签名机制（#2010）以内容签名判定「是否新异常」，与 (`subtype`,`aee_ts`) 不完全等价；
  若出现该组合的实测反例，应改为「带内容签名」的键——触发条件：真实 UNIVIEW run 中
  观察到同键不同异常。
