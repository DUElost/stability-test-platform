# #2694 audit_logs facets 有界化 + 两个支撑索引（附：issue 前提的生产数据更正）

Status: implemented
Class: bug-fix

## Decision

两项落地（issue 建议 1 与 2）：

1. **facets 每维有界**（`backend/api/routes/audit.py`）：`_facet_values(column, limit)`
   加 `LIMIT`，`_FACET_LIMIT = 50`；
2. **补两个索引**（`backend/models/audit.py` + 迁移 `a1b2c3d4e5f7`）：
   `ix_audit_action_ts(action, timestamp)` 与 `ix_audit_ts(timestamp)`。

**未做**（issue 建议 3/4/5）：保留策略、UI 分组、停写 `refresh` —— 理由见下，
其中建议 3（保留策略）涉及「会话类是否与业务审计同表同生命周期」的**裁决**，
issue 自述「我不代为决定」。

## ⚠️ 重要：issue 的**核心前提在生产数据上不成立**（我实测更正）

issue 基于 **dev 隔离栈**（299 行）判定「audit_logs 以**会话事件为主体**(62%)」。
我在**生产库**只读实测，结论相反：

| 指标 | issue（dev） | **生产实测** |
|---|---|---|
| 总行数 | 299 | **266,882** |
| 写入速率 | 11 行/小时 | **29 行/小时**（近 30 天）/ 387 行/小时（含历史爆发） |
| 首位 action | `refresh`（会话） | **`job_terminalized`（业务，76%）** |
| 会话类占比 | **62%** | **3%**（845 / 21,205） |

**近 30 天 action 构成**：`job_terminalized` 16,207(76%) / `hot_update_result` 1,448(6%)
/ `hot_update` 976(4%) / `assign_project` 728(3%) / `token_issued` 638(3%) / …

**另一处量级差异**：全表 266,882 行中 **237,971 行（89%）集中在 8/3–8/4 两天**
（`terminal_payload_conflict` 爆发）。即**历史总量主要由一次事件性爆发贡献**，
不是会话噪声的稳态累积。

### 这对方案的影响（我据此**调整了做什么**）

- **建议 4（UI 折叠会话类）的前提不成立**：会话类仅占 3%，折叠它收益极小；
  真正占满下拉的是 `job_terminalized` 这类**业务高频项**。故未做；
- **建议 1（facets 有界）仍然成立且更有必要**：86+ 种 action 里 76% 是同一项，
  下拉会被单一高频项**挤满**——这正是 issue 观察到的「把真正的操作挤到后面」，
  只是成因从「会话噪声」换成了「业务高频项」。有界化 + 保持按条数倒序后，
  下拉的可读性由「取 top-N」保证；
- **建议 2（索引）成立**：`action` 无索引、`timestamp` 无单列索引，这两条与
  构成无关，是纯事实——且生产 266k 行 / 91 MB 下每次开 `/audit` 的全表聚合
  已经是真实代价；
- **建议 3/5（保留策略、停写 refresh）**：均**未做**。停写 `refresh` 会削弱安全
  事件链（issue 自己列在「不建议」）；保留策略需先裁决，且**按生产实测的 29 行/小时
  ≈ 254k 行/年**（表翻倍需 ~1 年），紧迫度低于 issue 基于「50–300 行/小时」的推算。

> 说明：我**不否认** dev 观测本身；差异来源是 dev 近乎无业务量而登录态活跃。
> 但「以会话事件为主体」是**规模结论**，必须以生产为准。

## Verification

- `./scripts/run_pytest.sh backend/tests/api/test_audit.py -q` → **16 passed**
  （既有 14 + 新增 2）；
- `./scripts/run_pytest.sh backend/tests/migration/ -q` → **17 passed**（含新增 3 例）；
- **迁移真跑往返**（`test_audit_facet_indexes_roundtrip`，docker postgres:16）：
  `upgrade head` → 两索引在场 → `downgrade` 到**本迁移的父 revision** → 撤净且
  **既有两索引未被误撤** → 再 `upgrade head` 恢复；
- **红绿双向**：移除 `LIMIT` → `test_facets_are_bounded_per_dimension` **红灯**；
  恢复后通过。（`test_facets_bounded_still_returns_top_by_count` 两种情况都通过——
  它断言**排序语义**而非截断，属预期。）
- **离线 ORM↔迁移对偶**：`test_orm_declares_the_same_indexes_offline` 断言索引名与
  **列序**两侧一致（`check_schema_sync` 的轻量对偶，无需 DB）；
- `ruff` 通过；`check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Alternatives

- **facets 全量返回 + 前端截断** → 否决：传输与聚合结果仍无界，且把「哪些值值得展示」
  的判断放在前端（与 #2629「值域以真实写入为准」同源，但**有界**是该源的必要配套）。
- **给 facets 加时间窗**（issue 建议 1 的选项）→ 本版未采纳：会**改变语义**
  （facets 从「表里有哪些值」变成「近 N 天有哪些值」），可能让管理员找不到久未使用
  但仍需筛的 action。取 top-N 不改语义、只加界。若将来需要，应作为独立裁决。
- **`CREATE INDEX CONCURRENTLY`** → 否决：迁移内不能用（需 `autocommit`，
  alembic 事务外执行），且当前 266k 行 / 74 MB 下普通 `CREATE INDEX` 是亚秒级。
  已在迁移 docstring 写明「若表显著增大应改用 CONCURRENTLY 的独立迁移」。
- **只加 `(action)` 单列索引** → 否决：复合 `(action, timestamp)` 同时服务
  「按 action 筛 + 时间倒序」这一最常见组合（免排序），成本相同。
- **顺手加保留策略**（issue 建议 3）→ 否决：需先裁决「会话类是否与业务审计同表同
  生命周期」，且本 PR 已把真实速率（29 行/小时）留给该裁决作输入。

## Revisit

- **建议 3（保留策略）仍待裁决**：本 PR 未做。裁决时可参考本 PR 提供的生产实测
  速率（29 行/小时 ≈ 254k 行/年，表翻倍约需 1 年）与「89% 来自单次爆发」的
  构成事实——后者的含义是「按时间裁剪对事故期数据有信息损失，需一并考虑」。
- **`_FACET_LIMIT = 50` 的取值依据**：按「下拉可用性饱和」定，非实测拐点。
  若将来 action 种类远超 86 且长尾有实际筛选需求，应改成分页/搜索式候选
  而非调大常数。
- **`terminal_payload_conflict` 的爆发**（237,971 行 / 两天）不在本单范围，
  但它是这张表**历史总量的主要构成**——若那个写入路径仍可能在异常时放大，
  值得独立评估（本单只读观察到该形态，未追其代码路径）。
