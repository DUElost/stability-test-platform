# #2033 retention「行在、目录已删」窗口：改为可观测 + 消除一次 UnboundLocalError 风险

Status: implemented
Class: bug-fix

## Decision

`run_retention_cleanup`（`backend/scheduler/cron_scheduler.py`）两处改动：

1. **记录本批「NFS 已删」的 run 集合**（`purged_run_ids`，在 `try` **之前**初始化）；
2. **回滚分支显式报告该窗口**：`db.rollback()` 后若 `purged_run_ids` 非空 →
   `logger.error("retention_rollback_after_nfs_purge …")`。

**未改删除次序**（详见「Alternatives」）——次序本身有充分理由，且该窗口**已知可自愈**。

## 缺陷确认与一处重要收敛

issue 的判断**成立**：`purge_run_storage_dirs`（`:733`）在 `db.commit()`（`:793`）**之前**
执行 `rmtree`，故 DB 侧任一失败（FK 删除抛错 / commit 失败）经 `db.rollback()`（`:815`）
会留下「PlanRun 行仍在、NFS 目录已物理删除」的状态。

### 我的增量：该窗口**可自愈**，且此前**不可观测**

**（a）可自愈**——`purge_run_storage_dirs` 内部：

```python
if target.is_dir():
    shutil.rmtree(target)
```

`is_dir()` 守卫使「目录不存在」成为 **no-op**（**不**计入 `failed`）。故回滚后下一轮
retention 会照常删行并成功——不一致最长约**一个保留周期**（默认 3 天），**非永久**。
这与 issue 的定级（P3、「瞬态不一致」）一致，且给出了**具体机制**。

**（b）此前不可观测**——这正是本单实际修掉的东西：修复前从日志只能看到一句泛化的
`retention_cleanup failed`，**看不出目录已删**。运维无法判断该次失败是否留下了
「行在目录无」的状态。

## 实施中的两处自我纠正（留痕）

### 纠正 1（**流程，较严重**）：我在**共享主检出**上直接改文件

本轮我漏建隔离 worktree，直接在 `/home/debian13/stability-test-platform`（当时位于
**他人分支** `refactor/1520-agent-recovery-service`）上编辑。随后队列把 main 合入该检出，
**我的未提交改动被覆盖丢失**。

**未造成损害**：改动从未提交、从未 push，共享检出保持 clean（0 项改动），
未污染他人分支。

**处置**：重新 `declare` 到隔离 worktree `/home/debian13/stp-033` 后重做。

> 教训：本仓是多会话并发环境，**共享检出不是工作区**——即使改动「看起来只是改一个函数」，
> 也必须先建隔离 worktree。此前每个任务我都这么做了，本轮因「上一步的 worktree 被拒」
> 而顺手续用当前目录，属流程疏漏。

### 纠正 2：把 `UnboundLocalError` 写成了 `NameError`

我在注释与用例 docstring 里写「`except` 引用会让 `except` 自身 **NameError**」。
实测对照版（移除 pre-try 初始化）后确认实际抛的是：

```
UnboundLocalError: cannot access local variable 'purged_run_ids'
                     where it is not associated with a value
```

原因是**函数体内已存在对该名的赋值**，Python 因此视其为**局部变量**（而非全局查找失败）。
两者是不同异常。已同时更正注释与 docstring，并保留对照实验作为证据。

## Alternatives

- **选项 1（issue 建议）：把 `rmtree` 后置到 commit 之后** → **本版未采纳**。
  该次序的理由是硬约束（`purge_run_storage_dirs` docstring 自述）：
  **「DB 行是『哪些目录属于此 run』的唯一索引」**——先删行会让目录**永不可回溯**，
  即 R-01 盘满链的成因。后置到 commit 之后等于**重新引入 R-01**，除非先持久化
  「待清理目录清单」（新增状态）。**对 P3 而言改动面与风险都不成比例**。
- **选项 2（issue 建议）：为中间态加重试标记/告警** → **本版采纳其「告警」部分**，
  但**不引入重试标记**：已证该状态无需重试即可自愈（下一轮照常删行），
  额外标记会与既有 `purge_failed` 语义混淆（那才是真正需要重试的方向）。
  故只加**可观测的报告**，成本最小且不引入新状态。
- **把 `purged_run_ids` 在 try 内定义** → 否决：会让 `except` 分支在「赋值前失败」时
  抛 `UnboundLocalError`（见纠正 2）。改为与同函数既有 `lock_t0` **同一惯用法**
  （`lock_t0: float | None = None` 也在 try 前初始化，注释明写「失败路径可能在取锁前就抛，
  上报点须先判非 None」）——本改动是**沿用本文件既有模式**，非新造。
- **改用 `locals().get(...)` 兜底** → 我先写后弃：能工作但非本仓惯用法，
  且把「变量作用域」问题伪装成「动态查名」，可读性更差。

## Verification

- `./scripts/run_pytest.sh backend/tests/scheduler/ -q` → **121 passed**
  （含新增 3 例：`test_rollback_after_nfs_purge_reports_window` /
  `test_no_rollback_log_when_cleanup_succeeds` /
  `test_early_failure_before_purge_does_not_raise_nameerror`）；
- **红绿双向（窗口报告）**：移除回滚分支的新增日志 → 正向用例**红灯**、负向对照仍绿；
  恢复后 2 passed；
- **红绿双向（UnboundLocalError 守卫）**：移除 pre-try 初始化 → 早期失败用例**红灯**，
  实测异常为 `UnboundLocalError`（已据此更正措辞）；恢复后通过；
- **负向对照** `test_no_rollback_log_when_cleanup_succeeds`：成功路径**不得**出现该
  ERROR 日志（避免噪声）；
- `ruff check` 两文件 → All checks passed；
- `check_governance_surface.py --check` → S1–S14、S5x 全绿。

## Revisit

- **次序本身未改**：若将来出现「DB 删除稳定性显著低于文件删除」的实测证据，
  才值得投入选项 1（pre-commit 持久化待清理清单 + 后置 rmtree）。
  触发条件：`retention_rollback_after_nfs_purge` 在生产日志中**反复出现**——
  本单新增的日志正是该触发条件的观测入口。
- **未加指标**：本版只加日志（ERROR）。若要告警，应挂到既有 retention 指标族
  （`plan_run_retention_*`）而非新建计数器——需与告警规则同批，超出本单范围。
- **`purged_run_ids` 未持久化**：回滚后不跨进程保留，故「窗口已发生」只在当次日志留痕。
  这是有意的：该状态**无需**跨轮跟踪（下一轮自然收敛）。
