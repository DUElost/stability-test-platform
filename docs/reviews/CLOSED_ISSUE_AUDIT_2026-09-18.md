# 已关闭 issue 只读审查与审计（2026-09-17 ~ 09-18 窗口）

> **状态**：只读审计交付。不构成裁决，不修改任何被审对象，不产生代码变更。
> **审计时点**：2026-09-18（观测：`origin/main` 审计开始时 `ca2a872c` → 收尾时 `63e28842`，
> 期间主干持续前移；全部被审 merge sha 均复核为 `origin/main` 祖先）
> **审计范围**：2026-09-17T10:00Z ~ 2026-09-18T10:18Z 之间关闭的 **48 个实质 issue**
> （已排除 `ci/queue-blocked` 自动告警单）
> **方法**：GitHub timeline/`closingIssuesReferences` 取权威关闭链 → merge sha 祖先核验 →
> **逐条读当前 `origin/main` 代码**（不采信 PR 描述与 commit message）→ 关键项做
> **守卫红绿对拍与缺陷复现**。运行过：后端 `pytest` 9 个文件**合并一次运行 229 passed**
> （分组值 47/2/61/93/21 存在重叠，以合并值 229 为准）+ 前端 `vitest` 1 组（19 passed）
> + **6 项守卫红绿对拍**（§5.2，#2635/#2629/#2631/#2638/#2641/#2551，全部确认非恒绿）
> + **2 个缺陷复现探针**（#2649、#2636）。
> **未**运行：全量套件、任何迁移或破坏性诊断；未触碰生产库。
> **收尾更新**：初稿列为「未跟踪」的 #2694 保留策略缺口，已于收尾前由 #2741 + ADR-0049
> + PR #2761 闭环，本审计独立复核并记录（§4.1）。
> **结论时效性复核**：审计期间 `origin/main` 持续前移（`ca2a872c` → `63e28842`，跨越
> 数十次合并）。三项 P1 发现在最后一个观测点**逐项重放仍成立**：
> `runs.py` 三处 `Query(None)` 哨兵仍在、`_FATAL_DISPATCH_REASONS` 仍无 `serial_conflict`、
> `cron_scheduler.py:630` 的 cursor 仍是函数局部变量。

---

## 0. 结论摘要

| 判定 | 数量 | 说明 |
|---|---|---|
| LANDED（修复真落地且对症） | 39 | 含守卫充分者 |
| PARTIAL（部分落地 / 未触及根因 / 作用域收缩） | 5 | #2649 / #2636 / #2637 / #2601 / #2600，见 §2 |
| 关闭即重复单（合理，无残留缺陷） | 2 | #2559 / #2617，正文属实 |
| 无可核验修复但缺陷确实已消失 | 1 | #2568（被 #1520 重构顺带修掉，非专项修复） |
| **未落地且无人承接** | 1 | #2647（转单 #2639 后落入缝隙，见 §2.7） |
| **合计** | **48** | 与审计范围一致 |

**核心判断**：本窗口的修复质量整体为**高**。**44** 个 issue 有权威 closing PR，全部 merge sha
确认在 `origin/main` 上（无一「关而未合」）；守卫普遍不是「文本子串」式假守卫，多处明确
写了「旧守卫为何恒绿」并附红绿实测。

**其中 2 项已由本审计可执行复现为真实缺陷**（不止于读码推断）：

- **#2636**：中段可清行被两个超预算跳过块夹住时，**4 轮 purged=0** —— 直接证伪
  `cron_scheduler.py:607` docstring 的「任意数量…不再造成永久空转」（§2.2）；
- **#2649**：MANUAL run 在准入期遇 `serial_conflict` 实测返回
  `_RetryableAdmission("DEVICE_BUSY")` —— 无限重试而非按设备拒绝（§2.1）。

---

## 1. 方法与本审计的边界

### 1.1 权威关闭链的取法

`gh pr list --search "<N> in:body"` **不可用**：同一 issue 常被多个 PR 正文互相引用（例如
#2639 被 15 个 PR 提及），会得出错误的归属。本审计改用：

1. `gh pr list --state merged --json closingIssuesReferences` —— 取 GitHub 原生「关闭」关系；
2. 对无原生关系的，用 `GET /issues/{n}/timeline` 的 `cross-referenced` 事件交叉验证；
3. 每个 merge sha 用 `git merge-base --is-ancestor <sha> origin/main` 复核。

**48 项中 44 项**有权威 closing PR，**全部**确认在主干上（无一「关而未合」）。
其余 4 项（#2559 / #2568 / #2617 / #2647）无原生 closing 关系，逐一单列取证（§2.6–§2.8）。

### 1.2 判定口径

- **LANDED**：当前主干代码确实消除了 issue 所述缺陷，且守卫在修复被移除时会红。
- **PARTIAL**：修复了部分面向，但 issue 点名的根因或主场景仍不成立。
- 判定以**当前代码**为准；PR 描述中的「未采纳」「复验不成立」等声明一律独立复核。

### 1.3 本审计不覆盖

- 生产运行时行为（只读代码与库表结构，未在生产验证）；
- 前端几何/命中类结论（jsdom 无布局引擎，见 §2.5 与 §2.6）；
- 04 小时窗口之外的历史已关闭 issue。

---

## 2. 发现（按严重度）

### 2.1 【实质残留缺陷】#2649 占位符序列号设备：MANUAL 路径仍无限重试

**判定：PARTIAL。** 标题为「占位符序列号设备被多 host 争抢：整窗派发失败与执行漂移；
需按设备拒绝并标注原因」。

**已落地的一半**（真实、有测试）：

- `backend/core/device_serial.py:31` `is_placeholder_serial`；
- `backend/services/plan_dispatcher_sync.py:166-173` 产出逐设备 `reason: "serial_conflict"`；
- `:557-576` prepare 期从 `device_ids` 剔除并写入 `dispatch_rejected_devices`；
- `admission_pump.py:808-809` → `materialize_serial_rejected_jobs`（`plan_dispatcher_sync.py:683-719`）
  物化 FAILED JobInstance 并在 `status_reason` 标注「序列号冲突」；
- 前端 `SerialConflictBanner.tsx` + `DispatchCockpit.tsx:111-114` + `planExecuteReadiness.ts`。

**残留缺口**（已在当前代码复核）：

```python
# backend/services/plan_dispatcher_sync.py:73
_FATAL_DISPATCH_REASONS = ("not_found", "no_host", "host_retired")   # ← 无 serial_conflict

# backend/services/admission_pump.py:724
if pr.run_type in _ADMISSION_SHRINK_RUN_TYPES:    # ← ("SCHEDULE", "CHAIN")
```

即：`serial_conflict` 既不在 fatal 集合里，准入期收缩分支又只覆盖 SCHEDULE/CHAIN。
**若设备 serial 在 prepare 之后才变成占位符**（心跳驱动的 serial 改写——正是 issue 描述的
flapping 场景），MANUAL run 在准入期落到 `_RetryableAdmission("DEVICE_BUSY", ...)`，
**无限重试**而非按设备拒绝。prepare 期检测有效，准入期退化为 all-or-nothing。

**本审计已可执行复现**（不止于读码）——在既有测试文件末尾临时追加如下探针并运行，
**实测输出证实重试路径可达**（探针已删除，工作区已还原）：

```python
pr = prepare_plan_run(plan_id=f["plan"].id, device_ids=[f["device"].id],
                      triggered_by="pytest", db=db_session, run_type="MANUAL")
good = db_session.get(Device, f["device"].id)
good.serial = "0000000000000000"        # prepare 之后才变占位符
db_session.commit()
admission_transaction(db_session, pr.id, claim_queued_plan_runs(db_session)[0][1])
```

```text
PROBE-2649-MANUAL: RETRYABLE queue_reason=DEVICE_BUSY
  blockers=[{'id': 1, 'reason': 'serial_conflict', 'serial': '0000000000000000', 'host_id': 'h-disp-v'}]
```

> 复现注意事项（本审计踩过）：占位判据是 `is_placeholder_serial`
> （`backend/core/device_serial.py`）——`000000000000001` **不是**占位值（含两种字符），
> 必须用 `0000000000000000` / `0123456789abcdef` 等真占位值才能触发。另：分类读的是
> 准入时实时构建的 device 快照（`plan_dispatcher_sync.py:103-110`），故必须改**设备行**
> 而非仅改内存对象。

**测试**：`backend/tests/services/test_plan_dispatcher_device_validation.py` 覆盖
classify/prepare/materialize，其中准入侧物化用例用的是 `run_type="SCHEDULE"`
（`test_admission_materializes_failed_jobs_for_rejected`），**没有任何用例覆盖 MANUAL 的
准入期 `serial_conflict`**——恰是上述空洞。移除 prepare 侧代码会让既有用例变红；
准入侧空洞为 `NO_TEST`。

**修法提示（未实施，仅记录）**：把 `serial_conflict` 在 `admission_transaction` 中按
逐设备拒因处理（对齐 prepare 侧：剔除 + 物化 FAILED），或对 MANUAL 纳入
`_FATAL_DISPATCH_REASONS`。约 3 行。

> 附带发现：prepare 侧注释自称「**手动/定时同一流程**」（`plan_dispatcher_sync.py:553-556`），
> 而准入侧实际按 `run_type` 分流——**注释与实现不一致**，正是该空洞得以残留的原因。

### 2.2 【根因未触及】#2636 孤儿 DLE 清理：cursor 仍逐 tick 重置

**判定：PARTIAL。** 标题即根因：「cursor 逐 tick 重置 + 10×100 翻页上限」。

**已落地**（有效但非根因）：

- `cron_scheduler.py:614-620` 共享根未配置时早退（`dle_orphan_skipped_root_unset_early_return`），
  不再白扫 MAX_PAGES 页；
- `:623` `descending = (not dry_run) and _next_orphan_scan_descending()` —— 扫描方向逐 tick 交替。

**残留**：cursor 仍是**函数局部变量**，根因原样存在：

```python
# backend/scheduler/cron_scheduler.py:630
cursor: tuple | None = None     # ← 每 tick 重置，与修复前一致
```

方向交替只保证「最新端每两 tick 被检视一次」。**本审计已可执行复现残留空转**——
在 `test_retention_cleanup.py` 末尾临时追加探针（已删除，工作区已还原），构造
「可清行夹在两个**各超过单轮预算**（1500 > 10 页 × 100 = 1000）的恒跳过块之间」：

```text
  round0: purged=0
  round1: purged=0
  round2: purged=0
  round3: purged=0
PROBE-2636-MID: total_purged=0 remaining_cleanable=3/3
```

**4 轮（含两个方向各 2 次）后 3 条可清行一条未清**——升序轮的 1000 行预算全落在最老端
跳过块上，降序轮的预算全落在最新端跳过块上，中段永不可达。这**直接证伪**了
`cron_scheduler.py:605-607` docstring 的断言「**任意数量**的恒跳过行都不再造成永久空转」。

> 复现注意事项（本审计踩过两次）：`_mk_orphan_event`（`test_retention_cleanup.py:670`）
> 不设置 `updated_at`，靠列默认 `now()`（`backend/models/device_log_event.py:60-63`）——
> 于是可清行落在**最新端**，正是既有用例覆盖的有利情形。必须**显式覆写**其 `updated_at`
> 到中段时间带，才能构造对抗性布局；且跳过块大小必须 **> `MAX_PAGES × limit`**，
> 否则单轮键集翻页会越过它。

issue 自列的建议 2（持久化终态 / 把 `path_invalid` 行排除出候选谓词）**未实施**——
后者本可根治：跳过行不再进入候选集，就不会占满任何一轮的预算。

**测试**：`test_retention_cleanup.py` 的早退与方向交替两条用例通过，但只钉住「最新端有
可清行」这一有利情形（MAX_PAGES=1、120 跳过行、最新端 1 条可清行），未覆盖根因。
另注：早退与「本轮无行可清」都返回 `0`，**无 metric 可区分**，且每 tick 都告警。

### 2.3 【作用域收缩】#2637 编辑用户：静默失败已消除，但 autofill 仍需二次补填

**判定：PARTIAL。** 标题为「密码管理器自动填充后提交静默失败且错误文案不可见」，
issue 建议 1 是「让渲染条件与校验口径**同源**」。

**实际落地**（`frontend/src/pages/users/components/UserModal.tsx`）：

- `:222` 渲染条件加 `|| !!errors.confirmPassword`；
- `:122-124` 文案分流（`'请再次输入密码以确认修改'` vs `'两次输入的密码不一致'`）。

**但提交仍然失败**——`validate()` 仍返回 false，`handleSubmit` 仍提前返回，`onUpdate`
**不被调用**（`UserModal.tsx:118-126` 与 `:139-159` 现网复核）。纯 autofill 流程下用户
必须手动再补填一次确认框，**第二次**点击保存才会发出请求。

**本审计已运行其回归测试**（`cd frontend && npx vitest run src/pages/users/components/UserModal.test.tsx`
→ **19 passed**）。测试把这个两步流程写成了**显式设计意图**，用例名即
「直写新密码 → 首次保存给出可读错误 + 确认框就地出现 → **补填后提交发出**」：

```ts
managerFill(screen.getByLabelText(/新密码/), 'tPe-KLU-3Uw-3Fb');
fireEvent.click(screen.getByRole('button', { name: /保存/ }));
expect(onUpdate).not.toHaveBeenCalled();                    // 第一步：刻意不发请求
expect(screen.getByText('请再次输入密码以确认修改')).toBeInTheDocument();

fireEvent.change(confirm, { target: { value: 'tPe-KLU-3Uw-3Fb' } });
fireEvent.click(screen.getByRole('button', { name: /保存/ }));
expect(onUpdate).toHaveBeenCalledWith(expect.objectContaining({ password: 'tPe-KLU-3Uw-3Fb' }));
```

**审计判断（据此修正）**：以标题「静默失败 + 错误不可见」衡量 → **已消除**（错误可读、
确认框就地出现、补填后可提交），这正是原有缺陷的实质；以 issue 建议 1「让渲染条件与校验
口径同源（autofill 一次完成）」衡量 → **未实现**。鉴于「改密需二次确认」本身是合理的
安全语义，本项应记为**可辩护的作用域收缩**——但审计文档必须显式标注它不等于 issue 建议 1，
不应被读成「autofill 路径已完全闭环」。

### 2.4 【声称完整但未收敛】

- **#2601「主机显示名唯一入口」**：`frontend/src/utils/hostDisplay.ts:31-41` `hostLabel()`
  确为唯一入口，口径 `name > ip > hostId`（`unassigned` 特判为专属文案），当前被
  **17 个文件**引用（含 `.test`）。**本审计复核**仍有 2 处**渲染**未收口：

  | 位置 | 现状 | 与 `hostLabel` 差异 |
  |---|---|---|
  | `pages/devices/DevicesPage.tsx:100` | `host_name: host?.name \|\| host?.ip \|\| null` | 同序，但 `null` 而非 `'未知主机'` 兜底文案 |
  | `pages/runs/RunReportPage.tsx:190` | `{report.host?.name \|\| 'N/A'}` | **仅 name**，缺 ip/hostId 回退 |

  另 `pages/hosts/HostsPage.tsx:174,179` 用 `host.name || String(host.id)`（退役原因文案），
  语义与 `hostLabel` 一致，属可接受形态。今日无现网偏差（`RunReportPage` 处 `report.host`
  始终带 name），但「唯一入口」尚未 100% 收口，且**无守卫阻止第 18 处复制**。
- **#2651 的 `admission_excluded_devices`**：写入 `run_context` 且审计齐全，但
  **除测试外无消费方**（`grep frontend/src` 零命中），issue 的「run 结果可见」仅在原始
  `run_context` 层满足，UI 未呈现。

### 2.5 【守卫能力上限，非缺陷】#2614 几何判据的守护边界

`tests/test_frontend_bulk_selection_guard_2614.py` **不是**朴素文本 grep：它构建真实 import
图（`_imports`，`:62-65`），要求 `"<BulkBarSpacer"` 出现在 **JSX** 中（`:124`），并断言
`checked >= 2`。占位机制真实存在：`bulk-action-bar.tsx:25`
`BULK_BAR_SPACER_CLASS = 'h-40 shrink-0'`，两页共用同一规格。

**但**该守卫只钉住「存在 + 规格单一来源」，**从不验证 160px 是否真的清得开批量条**——
这恰是 #2700 自己的文档所承认的上限（`docs/development/testing.md:129`）。jsdom 无法做
几何验证，而**未新增浏览器层用例**。属已知且已制度化的边界，不计为缺陷，但「几何约定已
强制」与「几何充分性已证明」是两件事。

### 2.6 【关闭即重复单，判定正确】

- **#2559**：与 #2552 **同一缺陷**，已由 `0b5d121b` 修复
  （`tools/dev/memory_lint.py:98` `_FULLWIDTH_PUNCTUATION`）。关闭评论附实测复核。
- **#2617**：与 #2614 **正文逐字节相同**（独立 `diff` 复核无输出），权威单为 #2614，
  其修复（占位避让）已覆盖。关闭评论记录教训。

两者的 `stateReason` 均为 `NOT_PLANNED`，对「已被修复的重复单」而言更准确的取值是
COMPLETED/duplicate；**零工程影响**，仅记录。

### 2.7 【闭环掉地】#2647 根修方向在转单后丢失

**判定：未落地且无人承接。** 标题为「三处报告端点的 `plan_run_id` 默认值是 `Query(None)`
哨兵：#2420 归属守卫直调即误判（#2630 只改了调用点，原语未修）」。

关闭方式（`gh issue view 2647`）：以 `NOT_PLANNED` 关闭，**把根修方向转给 #2639**——

> 「本单独有的**根修方向**（把三处签名改成 `Annotated[Optional[int], Query(...)] = None`，
> 让默认值真的是 `None`…）与验收判据已并进 #2639，此单关闭。」

**但 #2639 本身已于同日 06:43 以 `COMPLETED` 关闭**，其交付内容是「源扫描型守卫抽公共
锚点助手 + AST 棘轮」（§2.9），**与该根修方向无关**。结果是：该缺陷被转单两次后
**落入缝隙，无任何 open issue 承接**。

**代码现状复核**（三处签名全部仍是哨兵，根修方向未实施）：

```python
# backend/api/routes/runs.py:108 / :133 / :158   ← 三处同款
plan_run_id: Optional[int] = Query(
    None, description="可选归属校验：给出则必须与 job 的 plan_run 配对（#2420）",
)
# 守卫：backend/api/routes/runs.py:87
if plan_run_id is None:
    return
```

由于 FastAPI 的 `Query(None)` 是**哨兵对象**而非 `None`，**直接调用**这三个函数（不经
HTTP 层）时守卫立即误判。这正是 issue 所述「原语未修」——#2630 只在测试调用点手写
`plan_run_id=None` 绕开，未修共享原语。

**这属于关闭流程缺陷，而非修复质量缺陷**：单被合理地转走，但接收方在自己不相干的
范围内关闭，转单时未建立真正的承接关系（无 "blocks"/"part of" 链接，也无 reopen 动作）。

### 2.8 【无专项修复但缺陷确实已消失】#2568

标题缺陷（`patch` 目标 `agent_api.broadcast_*` 已随 #1520 切片搬走）**确实已消失**：
`backend/tests/api/test_plan_run_abort_api.py:213,216` 现指向
`backend.services.agent_completion.broadcast_*`，实测 **21 passed**。
但修复来自 `af71d0d8`（#1520 装配层下沉重构）的**顺带效果**，而非针对 #2568 的专项修复；
该单无 closing PR。属「结果正确、归因缺失」，不构成缺陷。

### 2.9 值得记录的正向样本

> 本节 6 项均由**守卫红绿对拍**证实（明细 §5.2）——不只是「测试通过」，而是
> 「把修复拿掉后测试会红」。这是本窗口最值得制度化复制的性质。

- **#2635（锁序）**——本窗口质量最高的修复之一。`device_lease_reconciler.py:278` 在
  `on_job_terminal` 之后、下一候选取 job 锁之前显式 `await db.commit()`，使 plan_run 行锁
  释放，锁序回到 I2 基准 `job → plan_run`。守卫
  `test_reconciler_drain_lock_order_2635.py` **用 `pg_locks` 实测锁是否跨候选持有**，
  文件头明确解释旧用例为何恒绿（`_seed()` 一 job 一 PlanRun ⇒ 每候选都 `applied=True`），
  并以生产形状（1 PlanRun / N jobs）取种，已接入 CI 锁序 job。
  **红绿对拍：移除 `:278` 的 commit 后两条用例 FAILED，恢复后通过。**
- **#2629 + #2694（审计 facets）**——#2629 使筛选项真由数据驱动（`GET /facets`），
  消灭 6 个恒 0 死选项；#2694 再加 `_FACET_LIMIT = 50` 与两个 additive 索引
  （迁移 `a1b2c3d4e5f7`，本审计复核 alembic **单 head、无悬挂父 rev**）。
  #2694 的 PR **用生产实测推翻了 issue 的核心前提**（issue 依 dev 299 行判定「会话事件
  占 62%」；生产 266,882 行实测会话类仅 **3%**、首位是业务 `job_terminalized` 76%），
  并据此**显式缩小作用域**（建议 4 前提不成立 → 不做）。这是本窗口最好的
  「以证据改写问题」样本。
- **#2663（计数漂移）**——新门禁 `tests/test_alert_count_claims_are_live.py` 不是抄数字，
  而是运行时从告警 YAML / 场景 YAML / `backend/agent/scripts/` 最大版本目录**派生真值**
  再与文档对拍，并自带反空洞用例（扫描面非空、真值随文件内容变动）。
- **#2655（env 清单根因）**——修的是扫描器本身：`tools/dev/env_inventory.py` 的
  `_HELPER_ENV_RE` 由 `([^)\n]*)` 放宽为 `([^)]*)` 并新增整文件通道，使
  `HOST_HEARTBEAT_TIMEOUT_SECONDS` 这类多行 helper 调用不再结构性不可见；附带
  `tests/test_env_inventory.py::test_scan_reads_catches_multiline_helper_args` 回归用例。
- **#2718（站点证据累积）**——`install-state.json` 新增按发布物分桶的 `evidence`，
  合并规则正确（同 ID 以最新状态覆盖——历史不得掩盖刚发生的失败；本次未发的 ID 保留；
  跨发布物不继承），旧格式写侧折入，桶数有界（`EVIDENCE_RELEASES_KEPT = 5`），
  并有 93 passed 的双侧测试。
- **#2646 / #2624 / #2556 / #2641 / #2642 / #2639**——CI 与守卫类修复均落到「真实执行者」
  判据：`tests/ci_workflow_probe.py` 剥注释行、只认 `pull_request` 可达的 job；
  #2642 改用 `ast` 解析装饰器（路径换行不再是盲区）；#2639 抽出公共锚点助手 + AST 棘轮。
  定向实测 **47 passed**。

### 2.10 逐单判定台账（全 48 项）

> 每项给出**权威 closing PR / merge sha** 与判定。`sha` 均已核验为 `origin/main` 祖先。
> 未单列小节的 LANDED 项，其证据为「读当前代码确认缺陷消除 + 定向测试通过」。

**证据强度分级（避免过度解读本台账）**：

- **红绿对拍**（最强）：#2635 / #2629 / #2631 / #2638 / #2641 / #2551 —— 移除或削弱修复后
  对应守卫**实测变红**，恢复后变绿（明细见 §5.2）。这 6 项的守卫已排除「恒绿」风险。
- **缺陷复现**（强，证伪型）：#2649、#2636 —— 临时探针实测复现残留缺陷。
- **代码级核验 + 定向测试**：其余各项 —— 读当前代码确认修复在场、语义正确，并运行
  相关测试（见 §5）。这些项**未**做「移除修复看是否变红」的对拍，故个别守卫是否
  「恒绿」本台账不排除；已标注 `STATIC_ONLY` / `NO_TEST` 者除外。

> **抽样比例与代表性**：48 项中 6 项做了红绿对拍，选取原则是「守卫最容易退化为文本
> 断言」的类别（CI 结构守卫 ×2、文档/指标派生真值、API 轴口径、异常可达性）。
> 6 项**全部通过**（无恒绿），故对剩余同类项的风险判断可适度下调；但这是**便利抽样
> 而非随机抽样**，不构成对全部 39 项 LANDED 的穷尽证明。

| # | 标题要点 | PR / sha | 判定 | 备注 |
|---|---|---|---|---|
| 2551 | 全量套件仅在夜间跑 | #2580 `c6d7cc625` | LANDED | 种子守卫拆出容器文件进 PR 路径 |
| 2552 | memory_lint 全角标点误报 | #2555 `af15852d4` | LANDED | 与 #2559 同源 |
| 2554 | 租约排空未完自续轮 | #2576 `877420ca5` | LANDED | |
| 2556 | checks「从未创建」不重基 | #2573 `959aca405` | LANDED | 分出 never-created + 冷却自愈重基 |
| 2559 | memory_lint 举例被判断链 | — | 重复单 | 与 #2552 同缺陷，正文属实（§2.6） |
| 2568 | 恒红用例 patch 目标已搬走 | —（#1520 顺带） | 无专项修复 | 缺陷确已消失，21 passed（§2.8） |
| 2569 | 跨 host 改绑零日志 | #2593 `49d81a0b2` | LANDED | 补成因日志 |
| 2570 | 假 Agent serial 无 host 维度 | #2593 `49d81a0b2` | LANDED | 与 #2569 同 PR |
| 2572 | build_info 两种形态拿不到版本 | #2583 `c851d1c87` | LANDED | |
| 2577 | pr-agent-tests stall 40ms 竞争 | #2584 `f745a06ec` | LANDED | 时钟接缝 |
| 2579 | regressing_seq 时序 flake | #2584 `f745a06ec` | LANDED | 与 #2577 同 PR |
| 2595 | 裸 sleep 等异步（RunConsole） | #2596 `6f80b6bab` | LANDED | 改有界轮询 |
| 2598 | 恒红/恒缺两条用例 | #2608 `8f59fb5c9` | LANDED | |
| 2599 | 选机工作台 host 新鲜度 | #2611 `abc3a58e0` | LANDED | fail-open 已改 fail-closed |
| 2600 | 全选只覆盖当前页且无标注 | #2619 `76293728b` | PARTIAL | 分母已加；两条子指控经 base commit 复核确不成立（§2.4） |
| 2601 | 主机显示名三套口径 | #2611 `abc3a58e0` | PARTIAL | `hostLabel` 已建，2 处内联未收口（§2.4） |
| 2602 | Agent 套件 26 处裸 sleep | #2620 `9fe22c6e3` | LANDED | 改 2 处 + 余项逐条定性 |
| 2614 | 批量条压住分页控件 | #2622 `3aba61ff0` | LANDED | 占位机制真实；守卫为 STATIC_ONLY（§2.5） |
| 2617 | 同上（设备页） | — | 重复单 | 正文与 #2614 逐字节相同（§2.6） |
| 2624 | 停摆告警不看 mergeable | #2687 `292fb894b` | LANDED | CONFLICTING 队首纳入告警 |
| 2629 | 审计筛选 6 选项恒 0 | #2680 `416a6b656` | LANDED | 改由 facets 驱动，两层测试 |
| 2631 | /results 按 Plan.name 聚合 | #2678 `492a1bf59` | LANDED | 改按 specialty |
| 2635 | 回收器锁序与 I2 反 | #2645 `d2d7922f6` | LANDED | **本审计红绿对拍通过**（§2.9） |
| 2636 | 孤儿清理永久空转 | #2669 `bf9beb9b4` | **PARTIAL** | **本审计复现 4 轮 purged=0**（§2.2） |
| 2637 | autofill 提交静默失败 | #2683 `09115da3b` | PARTIAL | 静默已消除，autofill 仍需二次补填（§2.3） |
| 2638 | except 缺 HostRetiredError | #2675 `507a3723c` | LANDED | 409 分支现可达 |
| 2639 | 源扫描守卫锚点助手 | #2688 `e577e930a` | LANDED | 公共助手 + AST 棘轮 |
| 2640 | metric HELP 口径写错 | #2690 `a182cc22c` | LANDED | HELP 修正 + 三处静态对拍 |
| 2641 | CI 守卫锚在注释字面量 | #2682 `707413135` | LANDED | 改判「真实执行者」 |
| 2642 | 棘轮漏路径换行写法 | #2677 `13dd5f86d` | LANDED | 改 ast 解析 |
| 2646 | workflow scope 拒致停摆 | #2673 `f6b339103` | LANDED | 绿退 + 可区分告警 |
| 2647 | plan_run_id 哨兵未修 | — | **未落地无人承接** | 转单 #2639 后落入缝隙（§2.7） |
| 2648 | 链触发按瞬时 status 选设备 | #2652 `3920476e8` | LANDED | 改按父段 job 终态 |
| 2649 | 占位 serial 被多 host 争抢 | #2658 `302ba61b3` | **PARTIAL** | **本审计复现 MANUAL 无限重试**（§2.1） |
| 2651 | 周期回归收缩准入 | #2654 `64b02d3a4` | LANDED | 审计齐全；`admission_excluded_devices` 无 UI 消费方 |
| 2655 | 环境变量清单 0 命中 | #2668 `2dc97ee60` | LANDED | 修扫描器根因（多行 helper 调用） |
| 2656 | script-versioning 退役语义写反 | #2668 `2dc97ee60` | LANDED | |
| 2657 | /health 契约停旧三条件 | #2668 `2dc97ee60` | LANDED | 补 503 两码 + alembic 负载 |
| 2659 | 治理面文档漂移批 | #2670 `f5eb2b16a` | LANDED | S14/步数/行号/缺门禁 |
| 2660 | 03-frontend 漂移批 | #2668 `2dc97ee60` | LANDED | 附带修 routeTitles 真实代码缺陷 |
| 2661 | ADR 状态漂移 | #2686 `7f5de0291` | LANDED | P2 回填 + 已删键守卫 |
| 2662 | site.yaml 缺 ssh_port | #2686 `7f5de0291` | LANDED | 样例 + 契约表 + 完整性判据 |
| 2663 | 运维面计数陈旧 | #2691 `497952c8d` | LANDED | 派生真值门禁（非抄数字） |
| 2694 | audit_logs 无界聚合/无保留 | #2699 `8a310c3ce` | LANDED | 有界 + 索引；保留策略当时未跟踪 → **已于收尾前闭环**（见 §4 更新） |
| 2700 | 前端缺浏览器层 | #2720 `56fc6c142` | LANDED | 文档制度化；NO_TEST（纯文档） |
| 2706 | handover MS-04 假 BLOCKED | #2708 `08e8d87a0` | LANDED | S3 候选集 |
| 2707 | 报告页实体混用 | #2711 `ef520dcb6` | LANDED | 任务信息 → 计划信息 |
| 2718 | 验收证据只反映最近一次 | #2724 `2874c7b93` | LANDED | 按发布物累积，语义正确 |

---

## 3. 流程与治理观察

### 3.1 关闭流程：1 项归因缺失，2 项 reason 取值不精确

- **#2568** 无 closing PR（缺陷被 #1520 顺带修掉）——建议后续同类情况在关闭评论中
  写明「由 <sha> 顺带修复」，否则审计只能靠读代码反推。
- **#2559 / #2617** 以 `NOT_PLANNED` 关闭「已被修复的重复单」。语义上 `NOT_PLANNED`
  意为「不打算做」，与事实（已做/被别的单做了）不符。零工程影响。

### 3.2 Agent Note 与 ADR 纪律

窗口内 43 个修复相关文档齐全：`docs/notes/` 下 09-17/09-18 共 132 份笔记，
被审的每个 PR 均带四节制 Agent Note（Decision / Alternatives / Verification / Revisit）。
`docs/reviews/` 现有 30 份审计与审查文档，命名与结构一致。

### 3.3 值得注意的工程优点（建议保持）

1. **守卫必须自证红绿**：本窗口多个 PR 显式写明「旧守卫为何恒绿」，并在 PR 正文附
   「移除修复 → 红」的实测。这是防止「假防线」最有效的做法。
2. **敢于用生产数据推翻 issue 前提**（#2694），并据此缩小作用域而非硬做。
3. **作用域收缩显式声明**（#2694「未采纳」、#2600「两条指控经复验不成立」并给出
   base commit 复核，#2617 记录创建教训）。

---

## 4. 待办建议（供 owner 裁决，本审计不代为决定）

| 优先级 | 对象 | 内容 |
|---|---|---|
| **P1** | #2649 | MANUAL 准入期 `serial_conflict` 仍无限重试——本审计已复现；按逐设备拒绝处理（约 3 行）+ 补该路径用例 |
| **P1** | #2636 | 中段可清行被超预算跳过块夹住时永久空转——**本审计已复现 4 轮 purged=0**；根治是 issue 建议 2（把 `path_invalid` 行排除出候选谓词），或持久化扫描游标 + 修正 docstring:607 的过度断言 |
| **P1** | #2647 | 根修方向在转单 #2639 后丢失、**当前无任何 open issue 承接**——建议重开一单或原地重开 #2647，并把三处签名改为 `Annotated[Optional[int], Query(...)] = None` |
| **P2** | #2637 | autofill 一次完成（issue 建议 1）未实现，需二次补填；若接受现语义则应在 issue 中标注为作用域收缩而非修复 |
| P3 | #2694 | ~~保留策略未跟踪~~ → **已闭环**（见下方「审计期间闭环」） |
| P3 | #2601 | 2 处内联显示名未收口到 `hostLabel`；可加「禁止第 16 处复制」的守卫 |
| P3 | #2651 | `admission_excluded_devices` 无 UI 消费方，「run 结果可见」未闭环 |
| P3 | #2614 | 无浏览器层用例验证 160px 几何充分性（已知边界，见 #2700） |

### 4.1 审计期间闭环的一项（#2694 保留策略）

本审计初稿把「#2694 的保留策略（issue 建议 3）无 open issue 承接」列为待办。**该缺口在
审计收尾前已由社区流程自行补上**，现记录闭环证据（本审计仅复核，未参与）：

| 环节 | 载体 | 内容 |
|---|---|---|
| 拆单 | **#2741**（2026-09-18 17:31 开、同日 17:31 关闭） | 「audit_logs 保留期裁决与裁剪实现——#2694 拆单」，显式记录「top-N 只把症状从下拉里挪走，表仍只增不减」 |
| 裁决 | **ADR-0049** `Accepted v1.0`（2026-09-19 owner） | 分层保留 **security 180d / business 90d / session 30d**，env 可调；同表不拆、不做 UI 折叠；`terminal_payload_conflict` 爆发行不例外 |
| 实现 | PR #2761 | `backend/scheduler/audit_log_cleanup.py` + `backend/core/settings/scheduler.py:109-115` |

**本审计独立复核**（读码 + 跑测）：

- env 默认值与 ADR **逐项一致**：`audit_log_session_retention_days=30`、
  `business=90`、`security=180`、`batch_size=5000`（`settings/scheduler.py:109-113`）；
- 分层词表方向正确：`SECURITY_ACTIONS` 显式枚举（含 `token_issued`，与 #2694 dev 分组
  不同——裁决记录理由为「谁取得凭据」），**默认桶 = business**，故新增 action 自动落 90d
  网而非漏裁；
- 自免环已处理：裁剪仅在**确有删除**时写一条 `audit_retention_pruned` 汇总审计，
  该行同样受裁剪谓词约束，不形成自持环；
- `backend/tests/scheduler/test_audit_log_cleanup.py` **5 passed**。

> 方法论价值：这是「本轮审计发现的缺口 → 下一小时被拆单 → 隔日裁决 + 实现」的完整闭环，
> 说明该仓库对审计类反馈的响应链路是通的。也提示：**审计报告中的"未跟踪"结论有时效性**，
> 引用时必须带时点。

---

## 5. 复现方式

```bash
# 权威关闭链
gh pr list --state merged --limit 120 \
  --json number,mergeCommit,closingIssuesReferences

# 祖先核验
git merge-base --is-ancestor <merge-sha> origin/main && echo IN_MAIN

# 定向守卫（本审计实际运行）
.venv/bin/python -m pytest tests/test_offline_subset_guard.py \
  tests/test_lock_order_pr_path_contract.py tests/test_automerge_queue_alerts.py -q   # 47 passed
.venv/bin/python -m pytest backend/tests/scheduler/test_reconciler_drain_lock_order_2635.py -q  # 2 passed
.venv/bin/python -m pytest backend/tests/services/test_plan_chain_trigger.py \
  backend/tests/services/test_plan_dispatcher_device_validation.py -q                # 61 passed
.venv/bin/python -m pytest tests/test_site_handover.py tests/test_site_install.py -q  # 93 passed
.venv/bin/python -m pytest backend/tests/api/test_plan_run_abort_api.py -q            # 21 passed
cd frontend && npx vitest run src/pages/users/components/UserModal.test.tsx           # 19 passed

# 上述 9 个后端文件合并一次运行（推荐口径，避免逐组重复计数）
.venv/bin/python -m pytest tests/test_offline_subset_guard.py \
  tests/test_lock_order_pr_path_contract.py tests/test_automerge_queue_alerts.py \
  backend/tests/scheduler/test_reconciler_drain_lock_order_2635.py \
  backend/tests/services/test_plan_chain_trigger.py \
  backend/tests/services/test_plan_dispatcher_device_validation.py \
  tests/test_site_handover.py tests/test_site_install.py \
  backend/tests/api/test_plan_run_abort_api.py -q                                     # 229 passed
```

**两个缺陷复现探针**（本审计已执行；探针为临时追加，执行后即删除，工作区已还原）：

```bash
# #2649：MANUAL run 在准入期遇 serial_conflict → 实测 RETRYABLE（无限重试）
#   把探针追加到 backend/tests/services/test_plan_dispatcher_device_validation.py 末尾
#   （必须显式用真占位 serial，如 "0000000000000000"——"000000000000001" 不是占位值；
#     且要改 device 行，因为分类读的是准入时实时构建的快照）
#   → PROBE-2649-MANUAL: RETRYABLE queue_reason=DEVICE_BUSY
#       blockers=[{'id': 1, 'reason': 'serial_conflict', ...}]

# #2636：可清行被两个「各超单轮预算」的跳过块夹住 → 实测永久空转
#   把探针追加到 backend/tests/scheduler/test_retention_cleanup.py 末尾
#   （必须显式覆写可清行的 updated_at 到中段——_mk_orphan_event 靠列默认 now() 会落到
#     最新端；且跳过块须 > MAX_PAGES(10) × limit(100) = 1000 行）
#   → round0..round3 purged=0；PROBE-2636-MID: total_purged=0 remaining_cleanable=3/3

# #2635 守卫红绿对拍（本审计已做，工作区已还原）
#   移除 device_lease_reconciler.py:278 的 `await db.commit()` → 两条用例 FAILED → 还原后通过
```

### 5.2 守卫红绿对拍明细（6 项，全部通过）

方法：**削弱或还原修复 → 运行该修复的守卫 → 确认变红 → 从备份还原 → 确认变绿**。
目的专为排除「恒绿守卫」（断言在修复被移除后仍成立 = 防线是假的）。

| # | 削弱方式 | 结果 | 结论 |
|---|---|---|---|
| #2635 | 移除 `device_lease_reconciler.py:278` 的 `await db.commit()` | **2 failed** | 锁序守卫真实（`pg_locks` 实测） |
| #2629 | 在 facets 聚合上注入 `.where(False)`（返回空候选） | **5 failed** | 死选项回归判据真实 |
| #2631 | 把轴标签从 `Specialty.display_name` 改回 `Plan.name` | **6 failed** | specialty 口径守卫真实 |
| #2638 | 从 except 元组移除 `HostRetiredError`（还原原缺陷） | **1 failed** | 409 可达性守卫真实 |
| #2641 | 注释掉 PR 路径**真实**的 `python -m pytest tests/` 调用 | **3 failed** | 含反空洞断言，判据落在真实执行者上 |
| #2551 | 注入一个「停用版本但不做引用检查」的新 seed 文件 | **1 failed** | 静态守卫按文件名捕获违规 |

**两处需要记录的自我更正**（本审计先误判、后纠正，留痕以免误导）：

1. **#2641 首轮"发现"是我的定位错误**：我第一次注释的是 `backend-test`（夜间 job，
   `if: github.event != 'pull_request'`）里的 pytest 调用，守卫**不该**为它变红，故
   「7 passed」是正确的。改注释 `pr-agent-tests` 内并行子 shell 的真实调用后，守卫
   如期变红。另有一次我用了非法缩进注释，导致 YAML 解析失败、3 项因
   `ParserError` 变红——那是**假红**，不是守卫生效；改用合法 YAML 注释后复测确认。
2. **#2638 首轮"发现"也是定位错误**：我改了 `hosts.py:835` 的同名 handler，但被守卫覆盖
   的端点是 `/agent/hosts/{id}/upgrade-gate`，实际修复点在
   `backend/services/agent_upgrade_gate.py:141` 的 except 元组。改对位置后守卫如期变红。

> 这两次误判本身说明了同一件事：本窗口的修复普遍**有多个同名/邻近路径**，审计若只做
> 文本搜索而不追溯真实调用链，很容易得出「守卫是假的」这类错误结论。反之也提醒：
> 红绿对拍必须**作用于守卫真正锚定的那个执行者**，否则得到的红/绿都不可信。

> **只读性声明**：§5.1 与 §5.2 的全部探针均为「改到备份副本 → 运行 → 立即从备份还原」
> 或「临时注入文件 → 运行 → 立即删除」。执行后已逐文件核验
> `git diff --quiet`：`backend/scheduler/device_lease_reconciler.py`、
> `backend/api/routes/audit.py`、`backend/api/routes/results.py`、
> `backend/api/routes/hosts.py`、`backend/services/agent_upgrade_gate.py`、
> `.github/workflows/ci.yml`、`backend/tests/scheduler/test_retention_cleanup.py`、
> `backend/tests/services/test_plan_dispatcher_device_validation.py` **全部 CLEAN**，
> `backend/alembic/versions/` 无遗留探针文件。

---

*本审计为只读交付；未修改任何被审文件，未运行迁移，未触碰生产库。*
