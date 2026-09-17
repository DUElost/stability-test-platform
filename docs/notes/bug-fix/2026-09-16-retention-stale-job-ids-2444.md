# #2444 retention 推迟 run 的子行不再同批删除：子查询改为使用点重建

Status: implemented
Class: bug-fix

## Decision

`backend/scheduler/cron_scheduler.py`：把**构造一次**的 `stale_job_ids` 子查询改为
**函数** `_stale_job_ids()`，在**每个使用点**用当前 `safe_run_ids` 重建：

```python
def _stale_job_ids():
    return select(JobInstance.id).where(
        JobInstance.plan_run_id.in_(list(safe_run_ids))
    )
```

调用点 7 处（`StepTrace` / `DeviceLease` / `ResourceAllocation` / `JobArtifact` /
`JobLogSignal` / `DeviceLogEvent`×2）与 `_collect_unassigned_dirs(...)` 的传参
（该处在收缩前，语义等价）一并改为调用。

## 缺陷确认（代码 + **实测探针**）

issue 的判断**成立**，且我复现了其核心机制：

| 场景 | 子查询编译结果 |
|---|---|
| 构造时 `in_([1,2,3])` | `IN (1, 2, 3)` |
| **重绑定** `safe_run_ids = [1]` | `IN (1, 2, 3)` ❌ **仍是旧值** |

**根因**：`in_()` 在**构造时**就把列表**复制**进参数——实测 SQLAlchemy 持有的列表对象
与传入者**不是同一个**（`id` 不同）。

### ⚠️ 我对 issue 的一处**更正**：原地修改**同样无效**

issue 的探针写「`--in-place mutation-- IN (1)`」，即认为原地改列表会让子查询生效。
我实测**不成立**：

```
传入对象 id:            139684651404608
SQLAlchemy 持有的 id:   139684589153792     ← 不同一（被复制）
原地修改后编译:          IN (1, 2, 3)        ← 仍未反映
```

因为列表已被复制，**改原对象也影响不到子查询**。故 issue 建议里「或改成用当前的
`safe_run_ids` 直接表达」这一句是对的，但把它与「原地改」并列会误导——
**唯一有效修法是「使用点重建」**（实测重建版能正确得到 `IN (1)`）。

## 实施中的一次重要自我纠正：首版测试**不能复现缺陷**

我第一版用例只放**一个** run（即被推迟的那个），跑出来「红灯」了，看似有效。
但在做 red 验证时发现：**把修复还原后，用例仍然通过** —— 说明它**没有复现缺陷**。

用探针查明原因：

```
PROBE purge 收到的批次: [1]
```

**批次只含被推迟的那一个 run** → 收缩后 `safe_run_ids` 为空 → 代码走
`if not safe_run_ids: … return` **整段短路**，DB 删除根本没执行，
缺陷自然不可见。

**修复**：用例改为**两个 run 同批**（一个 NFS 失败被推迟、一个正常），
使收缩后批次仍非空、DB 删除真正执行。改后 red 验证成立
（还原修复 → 用例失败；恢复 → 通过）。

> 教训：**「测试变红」不等于「复现了缺陷」**——必须确认**移除修复后测试确实失败**
> （本次正是靠这一步才发现首版用例是空的）。且当缺陷依赖「批次收缩后仍非空」时，
> 单元素批次会被早退分支掩盖。

## Alternatives

- **改用 `in_(list(safe_run_ids))` 就地重建一次**（在收缩后重建一次、之后复用）→
  可行但脆弱：未来若新增第三次收缩，又会漏。**函数形式对新增收缩点免疫**，故选它。
- **改成显式传 id 列表而非子查询**（如先 `select` 出 id 再 `in_(ids)`）→ 否决：
  多一次往返，且同样要保证「在收缩后取」，未消除根本问题。
- **直接删 `safe_run_ids` 的中间收缩**（把两次收缩合并到最前）→ 否决：
  两次收缩的**触发条件不同**（unassigned 目录清理失败 vs run 目录 purge 失败），
  且 purge 必须在「知道哪些 run 的目录删失败」之后才能收敛——合并会改变既有语义。
- **不动，靠 `purge_failed` 的整批推迟保证一致性** → 否决：正是本缺陷——
  推迟只保住了 run/job 两行，保不住子行，留下「有 run、无子行」的壳。

## Verification

- `./scripts/run_pytest.sh backend/tests/scheduler/ -q` → **123 passed**
  （新增 1 例主用例 + 1 例负向对照）；
- **红绿双向（关键）**：还原为「构造一次的子查询」→
  `test_deferred_run_keeps_its_job_child_rows` **红灯**；恢复后通过；
- **负向对照** `test_non_deferred_sibling_still_has_child_rows_deleted`：
  未被推迟的 run 其子行**仍应照常删除**（修复不得漏删）；
- **探针取证**：`in_()` 复制列表（对象 id 不同）；重绑定与原地改**均无效**；
  使用点重建有效；
- `ruff check` 两文件 → All checks passed。

## Revisit

- **同类「捕获列表对象」的模式**：本文件的 `plan_run_id.in_(safe_run_ids)` 还有其它
  出现处（如 `DeviceLogEvent.plan_run_id.in_(safe_run_ids)` 在 DLE 删除谓词里）。
  我在本单**只改了 `stale_job_ids` 相关**；那些直接使用 `safe_run_ids` 的谓词位于
  **收缩之后**，每次执行时按当时的名字绑定取值，**不受本缺陷影响**。
  若将来有谓词在收缩前构造、收缩后使用，同样需改为使用点重建。
- **未加门禁**：本类问题（构造期捕获可变列表）可用静态检查（ruff/flake 规则）捕捉，
  但现有规则集无此项。若再出现一次同类实例，值得评估加一条自定义门禁。
- **未改 issue 的探针结论**：issue 正文的「原地修改有效」有误（见上），
  我已在本单 Agent Note 与 PR 中更正，**未回改 issue 正文**（保留原证据）。
