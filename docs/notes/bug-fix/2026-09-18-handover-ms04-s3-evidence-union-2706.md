# handover MS-04 假 BLOCKED：S3 证据槽位改候选集（#2706）

Status: implemented
Class: bug-fix

- 日期：2026-09-18
- 关联：`#2706`（本单）、`#2404`（同类第一处：MS-01 已改候选集，**漏了 MS-04**）、
  `#2283`（BLOCKED 是正常验收结果）、`#2546`（「同一事实多处口径」的评审面）

## Decision

`tools/site_config/handover.py` 的 `MS-04.stage_checks` 里，S3 槽位由**固定** `"install.s3.db"`
改为**候选集** `("install.s3.db", "install.s3.migrate")`——与 `MS-01` 同口径（`handover.py:48-50`）。

**为什么是同一类缺陷的第二处**：S3 阶段在两条**互斥**路径上发出不同 ID
（`stages.py`：数据库已在 head → `install.s3.db`/`schema_at_head`；本次应用了迁移 →
`install.s3.migrate`/`migration_applied`，见 `:956`/`:963`/`:975`）。固定要求其中任一个，
都会让另一条路径上的站点**永远缺证据**。`#2404` 把 MS-01 改成候选集时没有同步 MS-04，
于是 238 city-b 的「对齐 main」升级（**真的带迁移**）把 MS-04 打成了假 BLOCKED——
而「在 head 的幂等重跑」看不出问题，这正是上一次漏掉它的原因。

副作用面：`CheckSlot` 早已支持 `str | tuple[str, ...]`，缺失文案会渲染成
`install.s3.db or install.s3.migrate`（`handover.py:183-192`），故这是一行改动。

## Alternatives

- **让 S3 两条路径都发两个 ID（改 emitter）**：否决——两个 ID 如实描述两种**不同的现实**
  （"本来就是 head" vs "这次真的迁了"），把它们混成一条会让「本次是否应用了迁移」这一事实
  在证据面上消失。
- **把 MS-04 的 S3 槽位删掉（不要求该证据）**：否决——那是放弃判据本身；本单要的是让它
  在两条路径上都能命中。
- **只改 MS-04、不加用例**：否决——这个缺陷的形态正是「另一条路径没人测」，补两条互斥
  路径的用例才是防复发的那一半（已加 2 条，见 Verification）。
- **加一条「所有发出的 ID 都必须被某个 AcceptanceItem 要求」的门禁**：**本轮不做**。
  现有守卫是单向的「映射 ⊆ 发出」（`TestAcceptanceMappingGuard`，防陈旧映射）；反向守卫
  要把 emitter 里的**失败路径 ID**（`db_unmanaged`/`db_driver`/`db_unreachable` 等，它们与
  成功 ID 同槽位、本就不该被要求）逐条豁免——清单会长期漂。记入 Revisit。

## Verification

- `python -m pytest tests/test_site_handover.py -q` → **26 passed**（新增 2 条）；
- **定向变异**：把 MS-04 的 S3 槽位退回固定 `"install.s3.db"` → 新增的两条用例
  **2 failed**（`test_ms04_passes_on_migration_applied_evidence` 正是 issue 给出的判据）；
- `python scripts/run_gates.py check:quick` → **12 gates 绿**；
- **同族扫面（本单范围外，只做初判）**：按「同一 stage 多发 ID」交叉 `handover.py` 的固定
  槽位，除已修的 S3 外未发现第二处互斥形态——但该扫法是启发式（stage 内多数 ID 是**并列
  的不同面**，不是互斥候选），不能当证明；真判据见 Revisit。

## Revisit

- **反向门禁（发出但无人要求）**：若要机械化防这一类，需要先把 emitter 的失败路径 ID
  显式标注成「同槽位失败变体」（例如统一加 `_fail` 后缀或登记豁免表），否则清单不可维护。
  在有这份标注之前，同类缺陷仍可能以「新 ID + 旧槽位」的形态复发。
- **issue 的关联观察（未纳入本单）**：安装记录只保留**最近一次运行**的 stage 集合——
  任何 `install.sh --yes`（S0–S4，文档化的升级路径）都会丢掉上一次的 S5 证据，导致 MS-01
  假 BLOCKED。本轮为保住证据链是全量 `--through-agents` 跑的（26 分钟）。是否改成
  「证据按发布物累积」属安装记录模型的方向级取舍，由 owner 裁决。
- **MS-05/M-13 等其余槽位**：本轮只按 issue 范围改了 MS-04；若将来 S4/S1 的阶段也出现
  「同一事实两条路径不同 ID」，应同样用候选集表达并在**同一个 PR** 里同步所有引用它的
  AcceptanceItem（MS-01/MS-04 这次就是漏了这个同步）。
