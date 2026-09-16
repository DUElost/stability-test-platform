# #2055 两个已合入 seed revision 的原地改写：补重放迁移 + 门禁存量清单补登（#2322）

Status: implemented
Class: bug-fix

- 日期：2026-09-16
- 相关：`#2055`（违约提交 `02941d3c`）、`#2258`/`#2046`（不可变门禁与其清单）、`#942`（停用前引用核对）、
  `#1717`（重放先例 `f6a5b4c3d2e1`）

## Decision

### 1. 选修法 1（补重放迁移），不选只读核对

issue 给了二选一。选重放迁移的决定性理由**不是**「更彻底」，而是：**重放对健康库是 0 行
写入的 no-op**（`WHERE … 命中才写`），因此「不知道哪些库受损」不构成障碍——不需要先取得
生产库的只读访问、也不需要逐库核对。只读核对路线的成本（凭据、授权、逐库结论）换来的
只是「知道谁受损」，而重放路线**顺带**把受损库治好了。issue 说「不可判定部分据实标注」，
本单的标注是：**不主张生产已受损**，但部署即自愈。

同时把修法 2 的「登记」那一半也做了（见 §3）——两件事不互斥。

### 2. 自愈的范围刻意比修正后的 revision **更窄**

`d4e5f6a7b8c9`（`down_revision = p6q7r8s9t0u1`，即当前 head）：

- **monkey_launch v5.0.2**：仅当该脚本**没有任何 active 版本**时才置回 `is_active = true`。
  修正后的 `z1a2b3c4d5e6` 是无条件置 active，但那是**迁移当时**的语义；本迁移是在数天后
  重放，无条件置 active 会覆盖管理员在这期间的停用决策。窄化后的判据恰好等于旧代码会
  留下的缺陷态（「旧版本已停用、新版本也不是 active」），且对健康库 0 行写入。
- **gpu_setup 1.0.9**：重跑 `plan_step` 引用核对，仍被引用即 **raise**（与修正后的
  `y0z1a2b3c4d5` 同判据、同措辞）——把旧代码的「静默停用」变成部署期可见的失败。

**不重放** `z1a2b3c4d5e6` 的 `downgrade` 语义改写（DELETE → 翻转 `is_active`）：downgrade
只影响由 head 向下走的库，与「已 upgrade 的库漏跑修复」无关。本迁移自身 `downgrade`
是 **no-op**（自愈没有逆操作；把 5.0.2 翻回 inactive 只会重新制造空档），与 #1717 同形态。

### 3. 门禁文档补登第二例

`check_alembic_revision_immutability.py` 的背景段原先只记 2026-09-13 的 rechain 窗口，
读起来像「这类事只发生过一次」。补记 2026-09-15 的函数体改写例（本单），并**写明该门禁
只对比当次 diff、对历史改写无回溯能力**——所以存量清单必须逐例登记。两例的重放迁移
（`dd44ee55ff66` ← #1717、`d4e5f6a7b8c9` ← 本单）一并登记。

## Alternatives

- **只读核对 + 登记**（issue 修法 2）：见 §1。不选自成一路的唯一理由是重放无需先知道受损面；
  若本迁移会写健康库的行，这个取舍就要反过来。
- **monkey_launch 照抄「无条件置 active」**：覆盖人工决策，且与「重放」的定位不符
  （重放是补课，不是重立）。否。
- **gpu_setup 侧只 warn 不 raise**：与修正后的 revision 判据不一致，而且「静默降级」正是
  本单要消灭的形态（旧代码的问题就是停用前没核对）。否。
- **把重放 `down_revision` 指向被改写的两个 revision**：恰恰是病灶——受损库的
  `alembic_version` 已经在它们**之后**，挂在它们下面等于永不执行。必须挂在**当前 head**
  之后（`p6q7r8s9t0u1`）。否。
- **让门禁做回溯扫描（比对历史 blob）**：判据会变成「全历史 diff 扫描」，成本与误报面都
  上台阶，且它管不住「改写者是本门禁落地前的人」。本次用清单登记 + 重放收口，把工具改造
  列为 Revisit。否（现阶段）。

## Verification

- `python -m pytest backend/tests/migration/test_replay_2055_seed_amendments_2322.py -q`
  → **2 passed**（真 alembic + 一次性 PG 容器，docker 可用；用例覆盖 a 基线 → b 受损态 →
  c 自愈 → d downgrade no-op → e 幂等 → f 反向对照「另有 active 版本时不得改写」→
  g 被引用时 upgrade 必须失败、解除引用后成功）。
- **红向反证**：把自愈 UPDATE 换成 `SELECT 1 WHERE false` → `test_replay_heals_…`
  **红**（5.0.2 仍是 inactive）；还原即绿。用例确实卡在自愈逻辑上。
- `python -m pytest tests/test_alembic_heads.py tests/test_alembic_revision_immutability_gate.py backend/tests/migration/ -q`
  → **21 passed**（含单 head 契约：新 revision 成为 head，未破坏链）。
- `python scripts/run_gates.py check:quick` → **10 gates 绿**；`ruff check` 改动文件全绿。

## Revisit

- **未核对生产/试点库是否命中**：本单不主张生产已受损（无逐库依据）。部署后若要确认：
  `SELECT is_active FROM script WHERE name = 'monkey_launch' AND version = '5.0.2'` 与
  `SELECT COUNT(*) FROM plan_step WHERE script_name = 'gpu_setup' AND script_version = '1.0.9'`
  ——注意生产直连走 [`production-diagnostics.md`](../../operations/production-diagnostics.md)
  的只读姿势，不在本机业务库上试跑迁移。
- **门禁仍无回溯能力**：本次是人工审计发现的（不是门禁抓到的）。若出现第三例，考虑给门禁
  加一条基线内容快照判据（比对 base blob 的哈希 vs 历史的更强形态）；当前不做的理由见
  Alternatives。
- **`z1a2b3c4d5e6` 的 `downgrade` 改写未重放**：由 head 向下的库才会走到，不在本单范围；
  若将来出现「downgrade 也是旧形态」的实际受损，另立单。
