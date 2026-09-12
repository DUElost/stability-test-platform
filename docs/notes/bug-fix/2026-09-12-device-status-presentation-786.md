# Agent Note: 设备状态呈现两缺口（#786）

Status: implemented
Class: bug-fix
Issue: #786

## Decision

按 issue 建议的**「前端删幻影消费」**分支收口（另一分支「后端补真实字段」见 Alternatives / Revisit）：

1. **`DEVICE_UI` 补 `ABORTED` 键**（`components/ui/status-badge.tsx`）。
   后端 `_job_exec_status_for_job`（`api/routes/plan_runs.py`）含 `aborted` 分支，该值以
   `kind="device-ui"` 渲染；`resolveStatusEntry` 虽已 `toUpperCase()`，但表里无 `ABORTED`
   键 → 落 `FALLBACK`「未知」，与「已断开」混淆，批量中止 PlanRun 时设备矩阵整列显示「未知」。
   新增：`ABORTED: { label: "已中止", variant: "warning", Icon: Ban }`（`Ban` 本就在导入列表内）。

2. **删掉两个幻影字段的消费**（后端 `DeviceOut` 全仓零产出，实测 `grep` 于
   `backend/api/schemas/` + `backend/api/routes/` 为空）：
   - **`current_task`**：`types.ts` 声明 + `DevicesPage` 映射 + `ExpandableDeviceTable` 的
     row 类型与展示。展开行「当前任务」此前**恒显「无任务」**——对 BUSY 设备同样宣称
     「无任务」，属**说假话**而非「没数据」。故删去该展示块（保留同一张卡里的
     「最后活跃」与「查看指标历史」，二者是真实字段）。
   - **`schedulable`**：`types.ts` 与 `ReadinessDevice` 的声明、`tileStatus.isSchedulable`
     与 `planExecuteReadiness` 的读法。原写法
     `typeof device.schedulable === 'boolean' ? device.schedulable : device.status === 'ONLINE'`
     恒落 status 兜底；注释却称其为「Authoritative backend admission decision」——
     **注释在描述一个不存在的契约**。改为直接 `device.status === 'ONLINE'`，并留下注释
     说明现状。

## Alternatives

- **后端补真实字段（`current_task` / `schedulable`）**：本次不选。那是一次 API 面扩展
  （新字段 + 计算口径 + 测试），且需要产品裁定语义（当前任务取哪个 job？准入判据是否
  只看 status？）。issue 明确给出「或前端删幻影消费」分支，故取边界更小的一支；
  真正的字段需求记 Revisit，建议需要时**单独立项**。
- **保留「当前任务」展示位、把值渲染成 `—`**：不选。卡片里已有真实字段（最后活跃），
  留一个永空的槽位仍是死展示；直接删块更诚实。
- **给「未知」兜底加 `fallbackToRaw`**：不选。那是另一条通用回显策略，会波及所有 kind；
  本单只需让 `aborted` 有正确的语义徽章。

## Verification

- **`tsc --noEmit`**：先红后绿。首轮抓到一处我漏掉的**同型夹具**
  `src/utils/planExecuteReadiness.test.ts:25`（构造 `schedulable: false` 断言
  「设备不可调度」——与 `#780` 的「盲区自洽」夹具同型）；已改为用真实语义表达
  （`status: 'BUSY'` → 不可调度），并把该文件补进声明 scope。
- `vitest run`（针对性：`status-badge` / `planExecuteReadiness` / `ExpandableDeviceTable` /
  `DevicesPage` / `plan-execute` 目录）→ 全绿。
- 前端全量 `vitest run` → 见 PR 描述所载数字（本次改动前基线 102 files / 752 tests）。
- `python scripts/run_gates.py check:quick` → **7 gates 全绿**（含 eslint `--max-warnings 0`；
  删块后未使用的 `Clock` 导入已一并移除）。
- **未验证（诚实标注）**：真机/联调下 `job_exec_status='aborted'` 的端到端呈现（需一次
  批量中止的 PlanRun）；本单锁定的是徽章解析（含大小写不敏感）与幻影字段不再被消费。

## Revisit

- **若产品确实需要「设备当前任务」/「后端权威准入」**：应在后端实现字段并单独登记
  （含计算口径与准入语义），前端再消费；本次删除的是**对不存在字段的消费**，不是需求本身。
- `DEVICE_UI` 表现有键（IDLE/TESTING/RUNNING/COMPLETED/FAILED/UNKNOWN/BACKOFF/PENDING/
  ABORTED）与后端 `_job_exec_status_for_job` 的全部返回值应保持一一对应；若后端新增取值，
  需同步补键并加断言（可考虑仿 `test_read_api_auth.py` 的枚举式回归）。
