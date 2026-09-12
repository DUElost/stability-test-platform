# Agent Note: 派发判定弱化两处收口（#782）

Status: implemented
Class: bug-fix
Issue: #782

## Decision

**1. 预览与派发共用同一 lifecycle 校验**（`backend/services/plan_dispatcher_sync.py`）

`preview_plan_dispatch_sync` 在 `_build_lifecycle_from_steps(...)` 之后直接
`return _build_preview(...)`，从不跑 `validate_pipeline_def`；而派发路径（同文件
`dispatch_plan_sync`）有——预览因此会把**派发时必被拒**的生命周期展示成可执行
（例：仅 patrol、无 init）。现补齐同款校验，错误消息与派发路径同源
（`Plan {id} generated invalid lifecycle: …`）。

> **对 issue 指位的事实更正（重要）**：issue 指的是
> `backend/services/plan_dispatcher.py:66-100` 的 **async** `preview_plan_dispatch`。
> 实测该函数**是死代码**——全仓无调用、无导入（`grep -rn "preview_plan_dispatch("
> backend/` 只命中 ai_assistant 的工具名**字符串**，不是调用）；真正生效的是
> `backend/api/routes/plans.py:1184` 调用的 **sync** 变体。**sync 变体同样缺校验**，
> 故本次修的是生效路径——按 issue 原文修 async 版等于修一段没人执行的代码。

**2. abort reaper 的 cast 失败 fallback 不再用 ISO 文本比较**
（`backend/scheduler/device_lease_reconciler.py`）

原 fallback 是 `abort_at_text < grace_deadline.isoformat()`——**字典序比时间**。
形态混用即序错乱：非零偏移、`Z` 与 `+00:00` 混用、小数秒有无都会改变字典序，
且方向与真实时间无关。钉住的错例：

```text
raw      = 2026-09-12T19:59:59+08:00   # 真实时刻 11:59:59Z，早于 deadline
deadline = 2026-09-12T12:00:00+00:00
raw < deadline.isoformat()  → False    # 旧口径：判定「不早」→ 该回收的不回收
解析后比较                  → True
```

现改为：SQL 只按 `RUNNING + abort_requested` 取回候选（不再在 SQL 里比时间），
在 Python 侧用 `_parse_abort_at` 解析后比较。解析失败（坏值/非字符串/时区缺失以外的
异常形态）返回 `None` → 判为**不满足回收条件**：宁可漏回收，也不误杀在跑作业。
候选面被双重条件限定，行数有界。

## Alternatives

- **按 issue 原文只修 async `preview_plan_dispatch`**：否决。该函数无任何调用方，
  修了不生效；真正生效的 sync 变体才是缺陷所在（见上）。
- **顺手删掉死掉的 async 预览（或合并两个预览实现）**：否决（本次）。死代码清理属
  另一条轨（#1520 God-module / #1519 分层穿透家族），混进本 PR 会扩大 diff 与评审面；
  已记 Revisit。
- **fallback 继续用 SQL 文本比较，只把格式「归一」（如截断到秒）**：否决。非零偏移
  （`+08:00`）与 UTC 的字典序差异无法靠截断消除——方向本身就错。
- **fallback 改成「cast 失败即跳过本轮 + 告警」**：否决。会让 abort 回收在坏值出现时
  长期停摆（正是本链要消灭的「不收敛」形态）；解析可容错，没必要放弃整轮。

## Verification

- `python -m pytest backend/tests/services/test_plan_dispatcher.py
  backend/tests/scheduler/test_device_lease_reconciler.py -q` → **57 passed**
  （新增：预览拒绝非法 lifecycle + 拒绝理由来自共用校验器；7 例 ISO 形态参数化 +
  非零偏移「文本比较会判反」的回归 + 坏值永不触发回收）。
- `python -m pytest backend/tests/services/ backend/tests/scheduler/ -q` → **849 passed**
  （受影响两目录全量，含派发/预检/租约回收既有用例）。
- 均使用 testcontainers 隔离库，未连生产库。
- **未验证（诚实标注）**：fallback 分支本身只在 PG `timestamptz` cast 失败时才可达，
  现有集成用例没有构造「坏 `at` 值 + cast 失败」来真实走到那一支；本次覆盖的是
  该分支的**判据纯函数**与 SQL 取数形态（候选面收敛），不是端到端触发。要端到端
  覆盖需故意写入不可 cast 的 `at` 值，属测试夹具设计问题，记 Revisit。

## Revisit

- **端到端覆盖 fallback 分支**：构造一个 `run_context.abort_requested.at` 为不可 cast
  值的 fixture，断言 reaper 仍按解析比较正常回收（而非回滚后整轮失效）。
- **写入侧归一**：若把 `at` 的写入统一为单一格式（如一律 `…Z` 或一律带偏移），
  cast 路径几乎不会失败，fallback 可退化为「告警 + 解析比较」；
  那时也应重新评估该分支是否还值得保留。
- **async `preview_plan_dispatch` 死代码**：`backend/services/plan_dispatcher.py:66-100`
  与 sync 变体重复且无调用方；清理或合并应落在 God-module/分层治理那条轨上。
