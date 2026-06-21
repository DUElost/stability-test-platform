# PlanRunDetailPage UI 重设计实施计划

> **实施说明(2026-06-05 最终落地,与下方原计划有偏差)**:最终未采用"单页内 state-tab 切换",改为**路由分页**——`/execution/plan-runs/:runId`(概览)与 `:runId/logs`(巡检日志)两个独立路由,经 `PlanRunTabs` 切换;两页共用 `HeaderSlotContext` 把"返回 + tab"注入 AppShell 顶栏(详情页启用 `fullBleed`)。日志页采用 `PlanRunEventStream`(阶段/严重度过滤 + 分页),而非 `PatrolLogPanel`;`BusinessFlowTimeline` 本次改动已回滚(组件无引用,仍为孤儿)。下方任务清单为原始计划,保留作历史参考。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 PlanRunDetailPage 改为左侧固定状态面板 + 右侧 Tab 双栏布局，"运行详情"Tab 展示设备总览 / 异常仪表盘 / 业务流步骤，"巡检日志"Tab 展示按周期折叠的事件流。

**Architecture:**
- 左侧 256px sticky 面板：Hero（状态大 badge + meta + 操作）→ KPI 宫格 → 执行链 → 派发门禁（含 precheck-row）
- 右侧主区：sticky Tab 栏（运行详情 / 巡检日志）→ 对应 Panel
- 新建 4 个组件（PlanRunKpiGrid / AnomalyDashboard / BusinessFlowStepper / PatrolLogPanel）；不删除任何旧组件（防止测试连锁崩溃）
- 现有测试通过率目标：全量 `npx vitest run` 零新失败

**Tech Stack:** React 18, TypeScript, Tailwind CSS, @tanstack/react-query, lucide-react, Vitest + @testing-library/react

---

## 文件变更总表

| 操作 | 文件 | 说明 |
|------|------|------|
| **修改** | `frontend/src/components/plan-run/SectionHeader.tsx` | 加粗色条 + 字号提升 |
| **修改** | `frontend/src/components/plan-run/PlanRunHero.tsx` | 脉冲背景 + 大状态 badge + meta 网格 |
| **新建** | `frontend/src/components/plan-run/PlanRunKpiGrid.tsx` | 2×3 数字宫格（替代 KpiBar 在页面的位置）|
| **新建** | `frontend/src/components/plan-run/AnomalyDashboard.tsx` | Gauge + Metric Cards + 分布条 |
| **新建** | `frontend/src/components/plan-run/BusinessFlowStepper.tsx` | 水平三阶段步进器（不含事件流）|
| **新建** | `frontend/src/components/plan-run/PatrolLogPanel.tsx` | 巡检日志 Tab（按周期折叠 + 过滤 + 分页）|
| **修改** | `frontend/src/pages/execution/PlanRunDetailPage.tsx` | 双栏布局 + Tab 系统 + 组件重新接入 |
| **新建** | `frontend/src/components/plan-run/PlanRunKpiGrid.test.tsx` | KpiGrid 单元测试 |
| **新建** | `frontend/src/components/plan-run/AnomalyDashboard.test.tsx` | AnomalyDashboard 单元测试 |
| **新建** | `frontend/src/components/plan-run/BusinessFlowStepper.test.tsx` | BusinessFlowStepper 单元测试 |
| **新建** | `frontend/src/components/plan-run/PatrolLogPanel.test.tsx` | PatrolLogPanel 单元测试 |
| **修改** | `frontend/src/pages/execution/PlanRunDetailPage.test.tsx` | 更新集成测试 |

**不修改（保持原有测试通过）：**
- `WatcherSummaryCard.tsx` / `.test.tsx`
- `BusinessFlowTimeline.tsx` / `.test.tsx`
- `PlanRunKpiBar.tsx` / `.test.tsx`
- `PlanRunEventStream.tsx` / `.test.tsx`
- `DeviceOverview.tsx` / `.test.tsx`
- `DispatchGateCard.tsx` / `.test.tsx`
- `PlanChainBreadcrumb.tsx` / `.test.tsx`
- `DeviceDetailDrawer.tsx` / `.test.tsx`

---

## Task 1：SectionHeader 视觉加粗

**Files:**
- Modify: `frontend/src/components/plan-run/SectionHeader.tsx`

- [ ] **Step 1: 修改 SectionHeader 组件**

将色条从 `h-3 w-1` 改为 `h-4 w-1`，标题从 `text-xs` 改为 `text-sm`：

```tsx
// frontend/src/components/plan-run/SectionHeader.tsx
import type { ReactNode } from 'react';

interface Props {
  title: string;
  meta?: string;
  extra?: ReactNode;
  children?: ReactNode;
  /** 色条颜色，默认蓝色 */
  color?: 'blue' | 'red' | 'green' | 'amber' | 'gray';
}

const COLOR_CLS: Record<NonNullable<Props['color']>, string> = {
  blue:  'from-blue-600 to-blue-400',
  red:   'from-red-500 to-red-400',
  green: 'from-green-600 to-green-400',
  amber: 'from-amber-500 to-amber-400',
  gray:  'from-gray-400 to-gray-300',
};

export default function SectionHeader({ title, meta, extra, children, color = 'blue' }: Props) {
  return (
    <div className="mx-1 flex flex-wrap items-center gap-x-2.5 gap-y-1">
      <span className={`h-4 w-1 rounded-full bg-gradient-to-b ${COLOR_CLS[color]}`} />
      <span className="text-sm font-bold text-gray-800">{title}</span>
      {meta && <span className="text-xs text-gray-400">{meta}</span>}
      {children}
      {extra && <div className="ml-auto">{extra}</div>}
    </div>
  );
}
```

- [ ] **Step 2: 验证现有测试无破坏**

```bash
cd frontend && npx vitest run src/components/plan-run --reporter=verbose 2>&1 | tail -20
```

期望：现有测试无新 FAIL（SectionHeader 无自有测试，仅被其他组件引用）。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/plan-run/SectionHeader.tsx
git commit -m "feat(ui): thicken SectionHeader bar and promote title to text-sm"
```

---

## Task 2：PlanRunHero 重设计

**Files:**
- Modify: `frontend/src/components/plan-run/PlanRunHero.tsx`

Hero 改为脉冲暖色背景、大状态 badge（ping 动画圆点 + 旋转图标 + 运行时间）、2×2 meta 网格、水平操作按钮行。

- [ ] **Step 1: 替换 PlanRunHero 实现**

```tsx
// frontend/src/components/plan-run/PlanRunHero.tsx
import { useEffect, useMemo, useState } from 'react';
import { Download, X, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import type { PlanRun, PlanRunStatus } from '@/utils/api/types';
import { PLAN_RUN_PILL, isPlanRunTerminal } from './planRunStatus';

// 状态 → 容器背景/边框
const HERO_CLS: Record<PlanRunStatus, string> = {
  RUNNING:        'border-orange-200 bg-gradient-to-br from-orange-50/80 to-white',
  SUCCESS:        'border-green-200  bg-gradient-to-br from-green-50/60  to-white',
  PARTIAL_SUCCESS:'border-yellow-200 bg-gradient-to-br from-yellow-50/60 to-white',
  FAILED:         'border-red-200    bg-gradient-to-br from-red-50/60    to-white',
  DEGRADED:       'border-purple-200 bg-gradient-to-br from-purple-50/60 to-white',
};

// 状态 → badge 样式
const BADGE_CLS: Record<PlanRunStatus, string> = {
  RUNNING:        'border-orange-300 bg-white text-orange-700',
  SUCCESS:        'border-green-300  bg-white text-green-700',
  PARTIAL_SUCCESS:'border-yellow-300 bg-white text-yellow-700',
  FAILED:         'border-red-300    bg-white text-red-700',
  DEGRADED:       'border-purple-300 bg-white text-purple-700',
};

function formatDuration(seconds: number): string {
  if (!isFinite(seconds) || seconds < 0) return '—';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

interface Props {
  run: PlanRun | undefined;
  planName?: string | null;
  isAborting?: boolean;
  onAbort?: (reason: string) => void;
  onExportReport?: () => void;
  now?: Date;
}

export default function PlanRunHero({
  run,
  planName,
  isAborting = false,
  onAbort,
  onExportReport,
  now,
}: Props) {
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [reason, setReason] = useState('');
  const [tick, setTick] = useState(0);
  const isTerminal = !!run && isPlanRunTerminal(run.status);

  useEffect(() => {
    if (isTerminal || now) return;
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, [isTerminal, now]);

  const runDuration = useMemo(() => {
    if (!run) return null;
    const start = new Date(run.started_at).getTime();
    const end = run.ended_at
      ? new Date(run.ended_at).getTime()
      : (now ?? new Date()).getTime();
    return formatDuration(Math.max(0, (end - start) / 1000));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run, now, tick]);

  const pill = run ? PLAN_RUN_PILL[run.status] : null;
  const heroCls  = run ? HERO_CLS[run.status]  : 'border-gray-200 bg-white';
  const badgeCls = run ? BADGE_CLS[run.status] : '';
  const isRunning = run?.status === 'RUNNING';

  return (
    <div className={`rounded-xl border shadow-sm overflow-hidden ${heroCls}`}>
      <div className="px-4 pt-3 pb-1">
        {/* Plan 标识 */}
        <div className="text-[10px] text-gray-400 mb-0.5">
          <span className="font-semibold text-blue-600">
            {planName ? `Plan #${run?.plan_id} · ${planName}` : `Plan #${run?.plan_id ?? '—'}`}
          </span>
        </div>
        <div className="text-sm font-bold text-gray-900">
          PlanRun{' '}
          <span className={run?.status === 'RUNNING' ? 'text-orange-600' : 'text-gray-700'}>
            #{run?.id ?? '—'}
          </span>
        </div>
      </div>

      {/* 大状态 badge */}
      <div className="px-4 pb-3">
        {pill && run && (
          <div
            data-testid="plan-run-status-pill"
            className={`inline-flex items-center gap-2 rounded-xl border px-3.5 py-2 shadow-sm ${badgeCls}`}
          >
            {isRunning && (
              <span className="relative flex h-2.5 w-2.5 shrink-0">
                <span className="absolute inset-0 rounded-full bg-orange-400 opacity-60 animate-ping" />
                <span className="relative h-2.5 w-2.5 rounded-full bg-orange-500" />
              </span>
            )}
            <pill.Icon
              className={`h-4 w-4 ${isRunning ? 'animate-spin' : ''}`}
            />
            <div>
              <div className="text-sm font-bold">{pill.label}</div>
              {runDuration && (
                <div
                  data-testid="plan-run-duration"
                  className="font-mono text-[10px] opacity-70"
                >
                  {runDuration}
                </div>
              )}
            </div>
          </div>
        )}
      </div>

      {/* 2×2 meta 网格 */}
      <div className="px-4 pb-3 grid grid-cols-2 gap-x-3 gap-y-1 text-[10px]">
        <span className="text-gray-400">触发方式</span>
        <span className="font-medium text-gray-700">{run?.run_type ?? '—'}</span>
        <span className="text-gray-400">操作人</span>
        <span className="font-medium text-gray-700">{run?.triggered_by ?? '—'}</span>
        <span className="text-gray-400">开始时间</span>
        <span className="font-mono text-gray-700">
          {run?.started_at
            ? new Date(run.started_at).toLocaleString('zh-CN', {
                month: '2-digit', day: '2-digit',
                hour: '2-digit', minute: '2-digit',
              })
            : '—'}
        </span>
        <span className="text-gray-400">失败阈值</span>
        <span className="font-medium text-gray-700">
          {run?.failure_threshold != null
            ? `${Math.round(run.failure_threshold * 100)}%`
            : '—'}
        </span>
      </div>

      {/* 操作按钮行 */}
      <div className="flex gap-1.5 px-4 pb-4">
        <Button
          variant="outline"
          size="sm"
          onClick={onExportReport}
          disabled={!run}
          className="flex-1 text-[10px] h-7"
        >
          <Download className="mr-1 h-3 w-3" />
          导出报告
        </Button>

        {!isTerminal && (
          <Button
            variant="destructive"
            size="sm"
            data-testid="plan-run-abort-btn"
            onClick={() => setConfirmOpen(true)}
            disabled={!run || isAborting}
            className="flex-1 text-[10px] h-7"
          >
            {isAborting ? (
              <><Loader2 className="mr-1 h-3 w-3 animate-spin" />中止中…</>
            ) : (
              <><X className="mr-1 h-3 w-3" />中止运行</>
            )}
          </Button>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>确认中止 PlanRun?</AlertDialogTitle>
            <AlertDialogDescription>
              将释放运行中设备的租约，PENDING Job 标记为 ABORTED；Agent 上正在运行的 step
              会异步收到中止信号。操作不可撤销。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="space-y-2">
            <label className="block text-sm font-medium text-gray-700">中止原因（可选）</label>
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="例如：资源池整改"
              className="w-full rounded-md border px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-500/30"
            />
          </div>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction
              data-testid="plan-run-abort-confirm"
              onClick={() => {
                setConfirmOpen(false);
                onAbort?.(reason.trim() || 'aborted_by_user');
              }}
              className="bg-red-600 text-white hover:bg-red-700"
            >
              确认中止
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
```

- [ ] **Step 2: 验证现有 Hero 相关测试**

```bash
cd frontend && npx vitest run --reporter=verbose 2>&1 | grep -E "(PASS|FAIL|plan-run-status-pill|abort)"
```

期望：`plan-run-status-pill` 和 abort 相关用例仍通过。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/plan-run/PlanRunHero.tsx
git commit -m "feat(ui): redesign PlanRunHero with status-driven badge and meta grid"
```

---

## Task 3：新建 PlanRunKpiGrid

**Files:**
- Create: `frontend/src/components/plan-run/PlanRunKpiGrid.tsx`
- Create: `frontend/src/components/plan-run/PlanRunKpiGrid.test.tsx`

2×3 数字宫格，数字更大（text-2xl）。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/plan-run/PlanRunKpiGrid.test.tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import PlanRunKpiGrid from './PlanRunKpiGrid';
import type { PlanRunDevicesPayload } from '@/utils/api/types';

const devices: PlanRunDevicesPayload = {
  plan_run_id: 12,
  total: 48,
  by_status: { all: 48, running: 41, failed: 3, unknown: 1 },
  by_host: { 'host-101': 24, 'host-102': 24 },
  devices: [],
};

describe('PlanRunKpiGrid', () => {
  it('renders all six KPI cells', () => {
    render(<PlanRunKpiGrid devices={devices} currentStage="patrol" patrolCycle={14} />);
    expect(screen.getByTestId('kpig-total')).toHaveTextContent('48');
    expect(screen.getByTestId('kpig-running')).toHaveTextContent('41');
    expect(screen.getByTestId('kpig-failed')).toHaveTextContent('3');
    expect(screen.getByTestId('kpig-unknown')).toHaveTextContent('1');
    expect(screen.getByTestId('kpig-hosts')).toHaveTextContent('2');
    expect(screen.getByTestId('kpig-stage')).toHaveTextContent('PATROL');
    expect(screen.getByTestId('kpig-stage')).toHaveTextContent('#14');
  });

  it('tolerates missing devices (renders zeros, no crash)', () => {
    render(<PlanRunKpiGrid />);
    expect(screen.getByTestId('kpig-total')).toHaveTextContent('0');
    expect(screen.getByTestId('kpig-running')).toHaveTextContent('0');
  });

  it('shows no stage chip when currentStage is absent', () => {
    render(<PlanRunKpiGrid devices={devices} />);
    expect(screen.queryByTestId('kpig-stage')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
cd frontend && npx vitest run src/components/plan-run/PlanRunKpiGrid.test.tsx --reporter=verbose
```

期望：`Cannot find module './PlanRunKpiGrid'`。

- [ ] **Step 3: 实现 PlanRunKpiGrid**

```tsx
// frontend/src/components/plan-run/PlanRunKpiGrid.tsx
import type { PlanRunDevicesPayload } from '@/utils/api/types';

interface Props {
  devices?: PlanRunDevicesPayload;
  currentStage?: string | null;
  patrolCycle?: number | null;
}

const STAGE_LABEL: Record<string, string> = {
  init: 'INIT', patrol: 'PATROL', teardown: 'TEARDOWN', done: 'DONE', pending: 'PENDING',
};

function Cell({
  value,
  label,
  tone,
  testId,
}: {
  value: number;
  label: string;
  tone?: 'orange' | 'red' | 'purple' | 'default';
  testId: string;
}) {
  const numCls =
    tone === 'orange' ? 'text-orange-500'
    : tone === 'red'    ? 'text-red-500'
    : tone === 'purple' ? 'text-purple-500'
    : 'text-gray-900';
  return (
    <div data-testid={testId}>
      <div className={`font-mono text-2xl font-bold tabular-nums leading-none ${numCls}`}>
        {value}
      </div>
      <div className="text-[9px] uppercase tracking-wider text-gray-400 mt-0.5">{label}</div>
    </div>
  );
}

export default function PlanRunKpiGrid({ devices, currentStage, patrolCycle }: Props) {
  const total   = devices?.total ?? 0;
  const byStatus = devices?.by_status ?? {};
  const running = byStatus.running ?? 0;
  const failed  = (byStatus.failed ?? 0) + (byStatus.risk ?? 0);
  const unknown = byStatus.unknown ?? 0;
  const hostCount = Object.keys(devices?.by_host ?? {}).length;
  const stageStr = currentStage ? (STAGE_LABEL[currentStage] ?? currentStage.toUpperCase()) : null;

  return (
    <div className="rounded-xl border border-gray-100 bg-gray-50/60 px-4 py-3">
      <div className="text-[9px] font-bold uppercase tracking-widest text-gray-400 mb-2.5">
        当前态势
      </div>
      <div className="grid grid-cols-2 gap-x-4 gap-y-3">
        <Cell value={total}   label="总设备" testId="kpig-total" />
        <Cell value={running} label="运行中" tone="orange" testId="kpig-running" />
        <Cell value={failed}  label="失败"   tone={failed  > 0 ? 'red'    : 'default'} testId="kpig-failed" />
        <Cell value={unknown} label="失联"   tone={unknown > 0 ? 'purple' : 'default'} testId="kpig-unknown" />

        {hostCount > 0 && (
          <Cell value={hostCount} label="主机" testId="kpig-hosts" />
        )}

        {stageStr && (
          <div data-testid="kpig-stage" className="col-span-1">
            <div className="flex items-center gap-1 leading-none">
              <span className="h-2 w-2 rounded-full bg-orange-400 animate-pulse" />
              <span className="text-sm font-bold text-gray-700">{stageStr}</span>
              {patrolCycle != null && patrolCycle >= 0 && (
                <span className="font-mono text-[10px] text-orange-400">#{patrolCycle}</span>
              )}
            </div>
            <div className="text-[9px] uppercase tracking-wider text-gray-400 mt-0.5">阶段</div>
          </div>
        )}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd frontend && npx vitest run src/components/plan-run/PlanRunKpiGrid.test.tsx --reporter=verbose
```

期望：3/3 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/plan-run/PlanRunKpiGrid.tsx \
        frontend/src/components/plan-run/PlanRunKpiGrid.test.tsx
git commit -m "feat(ui): add PlanRunKpiGrid 2x3 numeric grid component"
```

---

## Task 4：新建 AnomalyDashboard

**Files:**
- Create: `frontend/src/components/plan-run/AnomalyDashboard.tsx`
- Create: `frontend/src/components/plan-run/AnomalyDashboard.test.tsx`

替代运行详情 Tab 中的 WatcherSummaryCard。保留 `watcher-summary` 和 `watcher-threshold-banner` testid（保持集成测试兼容）。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/plan-run/AnomalyDashboard.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import AnomalyDashboard from './AnomalyDashboard';
import type { WatcherSummary } from '@/utils/api/types';

const summary: WatcherSummary = {
  plan_run_id: 12,
  window_minutes: 60,
  window_start_at: '2026-06-04T10:00:00Z',
  window_end_at:   '2026-06-04T11:00:00Z',
  categories: [
    {
      category: 'AEE',
      count: 12,
      affected_device_count: 5,
      trend_change: 4,
      latest_device_serial: 'R5CT10',
      latest_detected_at: null,
    },
    {
      category: 'VENDOR_AEE',
      count: 3,
      affected_device_count: 2,
      trend_change: 0,
      latest_device_serial: 'R5CT22',
      latest_detected_at: null,
    },
  ],
  total: 15,
  affected_device_count: 6,
  total_devices: 48,
  abnormal_rate: 0.125,
  threshold: 0.1,
  exceeded: true,
};

describe('AnomalyDashboard', () => {
  it('renders watcher-summary container', () => {
    render(<AnomalyDashboard data={summary} />);
    expect(screen.getByTestId('watcher-summary')).toBeInTheDocument();
  });

  it('shows watcher-threshold-banner when exceeded=true', () => {
    render(<AnomalyDashboard data={summary} />);
    expect(screen.getByTestId('watcher-threshold-banner')).toBeInTheDocument();
  });

  it('does not show banner when not exceeded', () => {
    render(<AnomalyDashboard data={{ ...summary, exceeded: false }} />);
    expect(screen.queryByTestId('watcher-threshold-banner')).not.toBeInTheDocument();
  });

  it('shows abnormal rate as percentage', () => {
    render(<AnomalyDashboard data={summary} />);
    // 12.5% — formatted to 1 decimal or rounded
    expect(screen.getByTestId('watcher-summary')).toHaveTextContent('13%');
  });

  it('shows top category count', () => {
    render(<AnomalyDashboard data={summary} />);
    expect(screen.getByTestId('watcher-summary')).toHaveTextContent('12');
  });

  it('shows most affected device serial', () => {
    render(<AnomalyDashboard data={summary} />);
    expect(screen.getByTestId('watcher-summary')).toHaveTextContent('R5CT10');
  });

  it('calls onWindowChange when window selector is clicked', () => {
    const onWindowChange = vi.fn();
    render(<AnomalyDashboard data={summary} windowMinutes={60} onWindowChange={onWindowChange} />);
    fireEvent.click(screen.getByRole('button', { name: '24h' }));
    expect(onWindowChange).toHaveBeenCalledWith(1440);
  });

  it('renders loading skeleton when isLoading and no data', () => {
    render(<AnomalyDashboard isLoading />);
    expect(screen.getByTestId('watcher-summary')).toBeInTheDocument();
    expect(screen.queryByTestId('watcher-threshold-banner')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
cd frontend && npx vitest run src/components/plan-run/AnomalyDashboard.test.tsx --reporter=verbose
```

期望：`Cannot find module './AnomalyDashboard'`。

- [ ] **Step 3: 实现 AnomalyDashboard**

```tsx
// frontend/src/components/plan-run/AnomalyDashboard.tsx
import { AlertTriangle, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import SectionHeader from './SectionHeader';
import type { WatcherCategory, WatcherSummary } from '@/utils/api/types';

interface Props {
  data?: WatcherSummary;
  isLoading?: boolean;
  isError?: boolean;
  windowMinutes?: number;
  onWindowChange?: (minutes: number) => void;
}

const WINDOW_OPTIONS = [
  { value: 15,   label: '15m' },
  { value: 60,   label: '1h'  },
  { value: 360,  label: '6h'  },
  { value: 1440, label: '24h' },
];

const CATEGORY_LABEL: Record<string, string> = {
  AEE:       'AEE 崩溃',
  VENDOR_AEE:'VENDOR_AEE',
  ANR:       'ANR',
  TOMBSTONE: 'Tombstone',
  MOBILELOG: 'Mobile log',
};

const CATEGORY_BAR_CLS: Record<string, string> = {
  AEE:       'bg-red-500',
  VENDOR_AEE:'bg-amber-400',
  ANR:       'bg-orange-400',
  TOMBSTONE: 'bg-purple-400',
  MOBILELOG: 'bg-blue-400',
};

const CATEGORY_TEXT_CLS: Record<string, string> = {
  AEE:       'text-red-700',
  VENDOR_AEE:'text-amber-700',
  ANR:       'text-orange-700',
  TOMBSTONE: 'text-purple-700',
  MOBILELOG: 'text-blue-700',
};

/** SVG Gauge ring — renders a partial arc for abnormal_rate [0,1]. */
function GaugeRing({ rate, exceeded }: { rate: number; exceeded: boolean }) {
  const r = 30;
  const circ = 2 * Math.PI * r;
  const filled = Math.min(1, rate) * circ;
  const pct = Math.round(rate * 100);
  const color = exceeded ? '#ef4444' : '#f97316';
  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width="80" height="80" style={{ transform: 'rotate(-90deg)' }}>
        <circle cx="40" cy="40" r={r} fill="none" stroke="#f3f4f6" strokeWidth="7" />
        <circle
          cx="40" cy="40" r={r} fill="none"
          stroke={color} strokeWidth="7"
          strokeDasharray={`${filled} ${circ - filled}`}
          strokeLinecap="round"
        />
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span
          className="font-mono text-lg font-bold leading-none"
          style={{ color }}
        >
          {pct}%
        </span>
      </div>
    </div>
  );
}

/** Derive "top" category by count. */
function topCategory(cats: WatcherCategory[]): WatcherCategory | null {
  if (!cats.length) return null;
  return cats.reduce((a, b) => (b.count > a.count ? b : a));
}

/** Most-affected device across all categories. */
function mostAffectedSerial(cats: WatcherCategory[]): string | null {
  for (const cat of cats) {
    if (cat.latest_device_serial) return cat.latest_device_serial;
  }
  return null;
}

export default function AnomalyDashboard({
  data,
  isLoading = false,
  isError = false,
  windowMinutes = 60,
  onWindowChange,
}: Props) {
  const windowSelector = (
    <div className="flex items-center gap-0.5 rounded-md border border-gray-200 bg-white p-0.5 shadow-sm">
      {WINDOW_OPTIONS.map(({ value, label }) => (
        <button
          key={value}
          type="button"
          onClick={() => onWindowChange?.(value)}
          className={`rounded px-2 py-0.5 text-[11px] transition-colors ${
            windowMinutes === value
              ? 'bg-blue-100 font-semibold text-blue-700'
              : 'text-gray-500 hover:bg-gray-100'
          }`}
        >
          {label}
        </button>
      ))}
    </div>
  );

  return (
    <section data-testid="watcher-summary" className="space-y-2">
      <SectionHeader
        title="异常仪表盘"
        meta={`Watcher 聚合 · ${windowMinutes >= 60 ? `${windowMinutes / 60}h` : `${windowMinutes}m`} 窗口`}
        color="red"
        extra={windowSelector}
      />

      {/* 超阈值 banner */}
      {data?.exceeded && (
        <div
          data-testid="watcher-threshold-banner"
          className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-2.5 text-xs"
        >
          <AlertTriangle className="h-3.5 w-3.5 text-red-500 shrink-0" />
          <span className="text-red-700">
            综合异常率{' '}
            <strong>{Math.round((data.abnormal_rate ?? 0) * 100)}%</strong>
            {' '}超过告警阈值{' '}
            <strong>{Math.round((data.threshold ?? 0) * 100)}%</strong>
          </span>
        </div>
      )}

      {/* Loading */}
      {isLoading && !data && (
        <div className="space-y-2 rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-4 w-3/4" />
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="flex items-center gap-2 rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-xs text-red-600">
          <AlertCircle className="h-4 w-4 shrink-0" />
          加载 Watcher 数据失败
        </div>
      )}

      {/* Main dashboard */}
      {data && (
        <>
          {/* 三列 Metric Cards */}
          <div className="grid grid-cols-3 gap-3">
            {/* Gauge */}
            <div className="rounded-xl border border-gray-200 bg-white px-4 py-4 shadow-sm text-center">
              <GaugeRing rate={data.abnormal_rate ?? 0} exceeded={data.exceeded} />
              <div className="text-[10px] font-semibold text-gray-600 mt-1">综合异常率</div>
              <div className={`text-[9px] mt-0.5 ${data.exceeded ? 'text-red-400' : 'text-gray-400'}`}>
                阈值 {Math.round((data.threshold ?? 0) * 100)}%
              </div>
            </div>

            {/* Top category count */}
            {(() => {
              const top = topCategory(data.categories);
              return (
                <div className="rounded-xl border border-amber-200 bg-white px-4 py-4 shadow-sm text-center">
                  <div className="font-mono text-3xl font-bold text-amber-600 tabular-nums leading-none">
                    {top?.count ?? 0}
                  </div>
                  <div className="text-[10px] font-semibold text-gray-600 mt-1">
                    {top ? (CATEGORY_LABEL[top.category] ?? top.category) : '无异常'}
                  </div>
                  <div className="text-[9px] text-gray-400 mt-0.5">
                    影响 {top?.affected_device_count ?? 0} 台设备
                  </div>
                </div>
              );
            })()}

            {/* Most affected device */}
            <div className="rounded-xl border border-orange-200 bg-white px-4 py-4 shadow-sm text-center">
              <div className="font-mono text-sm font-bold text-orange-700 break-all leading-tight">
                {mostAffectedSerial(data.categories) ?? '—'}
              </div>
              <div className="text-[10px] font-semibold text-gray-600 mt-1">最多异常设备</div>
              <div className="text-[9px] text-gray-400 mt-0.5">
                {data.affected_device_count} 台受影响
              </div>
            </div>
          </div>

          {/* 异常类型分布 */}
          {data.categories.length > 0 && (
            <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm">
              <div className="px-4 py-2 bg-gray-50 border-b border-gray-100 text-[10px] font-bold uppercase tracking-wider text-gray-500">
                异常类型分布
              </div>
              <div className="divide-y divide-gray-50">
                {data.categories.map((cat) => {
                  const maxCount = Math.max(...data.categories.map((c) => c.count), 1);
                  const pct = (cat.count / maxCount) * 100;
                  const label = CATEGORY_LABEL[cat.category] ?? cat.category;
                  const barCls = CATEGORY_BAR_CLS[cat.category] ?? 'bg-gray-400';
                  const textCls = CATEGORY_TEXT_CLS[cat.category] ?? 'text-gray-700';
                  return (
                    <div key={cat.category} className="flex items-center gap-3 px-4 py-2.5">
                      <div className={`w-24 text-[11px] font-semibold shrink-0 ${textCls}`}>
                        {label}
                      </div>
                      <div className="flex-1">
                        <div className="flex items-center gap-2">
                          <div className="flex-1 h-1.5 rounded-full bg-gray-100">
                            <div
                              className={`h-1.5 rounded-full ${barCls}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                          <span className={`font-mono text-[11px] font-bold w-6 text-right ${textCls}`}>
                            {cat.count}
                          </span>
                        </div>
                      </div>
                      <div className="text-[10px] text-gray-400 w-20 text-right shrink-0">
                        {cat.affected_device_count} 台
                        {cat.trend_change > 0 && (
                          <span className="text-red-500 ml-1">↑{cat.trend_change}</span>
                        )}
                        {cat.trend_change < 0 && (
                          <span className="text-green-500 ml-1">↓{Math.abs(cat.trend_change)}</span>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: 运行 AnomalyDashboard 测试确认通过**

```bash
cd frontend && npx vitest run src/components/plan-run/AnomalyDashboard.test.tsx --reporter=verbose
```

期望：8/8 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/plan-run/AnomalyDashboard.tsx \
        frontend/src/components/plan-run/AnomalyDashboard.test.tsx
git commit -m "feat(ui): add AnomalyDashboard with gauge ring and category distribution"
```

---

## Task 5：新建 BusinessFlowStepper

**Files:**
- Create: `frontend/src/components/plan-run/BusinessFlowStepper.tsx`
- Create: `frontend/src/components/plan-run/BusinessFlowStepper.test.tsx`

水平三阶段步进器，不含事件流，仅展示阶段进度和当前步骤摘要。保留 `business-flow-timeline` testid（集成测试兼容）。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/plan-run/BusinessFlowStepper.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import BusinessFlowStepper from './BusinessFlowStepper';
import type { PlanRunTimeline } from '@/utils/api/types';

const timeline: PlanRunTimeline = {
  plan_run_id: 12,
  current_stage: 'patrol',
  plan_name: '24h 烧机',
  triggered_at: '2026-06-04T10:00:00Z',
  triggered_by: 'admin',
  run_type: 'MANUAL',
  stages: [
    {
      stage: 'init',
      status: 'completed',
      device_total: 48,
      device_succeeded: 45,
      device_failed: 3,
      steps: [
        { step_key: 'monkey_launch', status: 'completed',
          device_succeeded: 45, device_failed: 3, started_at: null, ended_at: null },
      ],
    },
    {
      stage: 'patrol',
      status: 'running',
      device_total: 45,
      device_succeeded: 41,
      device_failed: 0,
      patrol_cycle_index: 14,
      patrol_interval_seconds: 60,
      steps: [],
    },
    {
      stage: 'teardown',
      status: 'pending',
      device_total: 0,
      device_succeeded: 0,
      device_failed: 0,
      steps: [],
    },
  ],
};

describe('BusinessFlowStepper', () => {
  it('renders business-flow-timeline container', () => {
    render(<BusinessFlowStepper timeline={timeline} />);
    expect(screen.getByTestId('business-flow-timeline')).toBeInTheDocument();
  });

  it('shows three stage nodes: INIT(done), PATROL(running), TEARDOWN(pending)', () => {
    render(<BusinessFlowStepper timeline={timeline} />);
    expect(screen.getByTestId('stage-node-init')).toBeInTheDocument();
    expect(screen.getByTestId('stage-node-patrol')).toBeInTheDocument();
    expect(screen.getByTestId('stage-node-teardown')).toBeInTheDocument();
  });

  it('shows patrol cycle index', () => {
    render(<BusinessFlowStepper timeline={timeline} />);
    expect(screen.getByTestId('stage-node-patrol')).toHaveTextContent('#14');
  });

  it('shows init step failure counts', () => {
    render(<BusinessFlowStepper timeline={timeline} />);
    expect(screen.getByTestId('stage-node-init')).toHaveTextContent('45');
    expect(screen.getByTestId('stage-node-init')).toHaveTextContent('3');
  });

  it('INIT steps detail collapses/expands', () => {
    render(<BusinessFlowStepper timeline={timeline} />);
    const toggle = screen.getByTestId('init-steps-toggle');
    // default hidden
    expect(screen.queryByTestId('init-steps-detail')).not.toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.getByTestId('init-steps-detail')).toBeInTheDocument();
  });

  it('shows loading skeleton when isLoading and no data', () => {
    render(<BusinessFlowStepper isLoading />);
    expect(screen.getByTestId('business-flow-timeline')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
cd frontend && npx vitest run src/components/plan-run/BusinessFlowStepper.test.tsx --reporter=verbose
```

- [ ] **Step 3: 实现 BusinessFlowStepper**

```tsx
// frontend/src/components/plan-run/BusinessFlowStepper.tsx
import { useState } from 'react';
import { Check, Loader2, Circle, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import SectionHeader from './SectionHeader';
import type { PlanRunTimeline, TimelineStage } from '@/utils/api/types';

interface Props {
  timeline?: PlanRunTimeline;
  isLoading?: boolean;
  isError?: boolean;
}

const STAGE_TITLE: Record<string, string> = {
  init:     '前置准备',
  patrol:   '巡检循环',
  teardown: '收尾清理',
};

function StageNode({ stage, isCurrent }: { stage: TimelineStage; isCurrent: boolean }) {
  const [initOpen, setInitOpen] = useState(false);

  let iconEl: React.ReactNode;
  let ringCls: string;
  let textCls: string;
  let labelCls: string;
  let opacity = '';

  if (stage.status === 'completed') {
    iconEl  = <Check className="h-4 w-4 text-green-600" />;
    ringCls = 'border-green-400 bg-green-50';
    textCls = 'text-green-600';
    labelCls = 'font-bold text-green-600';
  } else if (stage.status === 'running' || isCurrent) {
    iconEl  = <Loader2 className="h-4 w-4 text-orange-500 animate-spin" />;
    ringCls = 'border-orange-400 bg-orange-50';
    textCls = 'text-orange-600';
    labelCls = 'font-bold text-orange-600';
  } else if (stage.status === 'failed') {
    iconEl  = <AlertCircle className="h-4 w-4 text-red-500" />;
    ringCls = 'border-red-400 bg-red-50';
    textCls = 'text-red-600';
    labelCls = 'font-bold text-red-600';
  } else {
    iconEl  = <Circle className="h-4 w-4 text-gray-300" />;
    ringCls = 'border-dashed border-gray-300 bg-white';
    textCls = 'text-gray-400';
    labelCls = 'font-bold text-gray-400';
    opacity = 'opacity-50';
  }

  const isPatrol = stage.stage === 'patrol';
  const isInit   = stage.stage === 'init';

  return (
    <div data-testid={`stage-node-${stage.stage}`} className={`flex-1 text-center ${opacity}`}>
      {/* 圆形图标 */}
      <div className="flex justify-center mb-1.5">
        <div
          className={`h-8 w-8 rounded-full border-2 flex items-center justify-center shadow-sm ${ringCls}`}
        >
          {iconEl}
        </div>
      </div>

      {/* 阶段标签 */}
      <div className={`text-[10px] uppercase tracking-wider ${labelCls}`}>
        {stage.stage}
      </div>
      <div className="text-[9px] text-gray-400 mt-0.5">
        {STAGE_TITLE[stage.stage] ?? stage.stage}
      </div>

      {/* PATROL：显示周期 */}
      {isPatrol && stage.patrol_cycle_index != null && (
        <div className={`text-[9px] mt-0.5 ${textCls}`}>
          周期 <span className="font-mono">#{stage.patrol_cycle_index}</span>
          {' '}· 活跃 {stage.device_total ?? 0} 台
        </div>
      )}

      {/* INIT：成功/失败摘要 */}
      {isInit && stage.status !== 'pending' && (
        <div className={`text-[9px] mt-0.5 ${textCls}`}>
          <span className="text-green-600">{stage.device_succeeded} ✓</span>
          {stage.device_failed > 0 && (
            <span className="text-red-500 ml-1">{stage.device_failed} ✗</span>
          )}
        </div>
      )}

      {/* INIT 步骤折叠 */}
      {isInit && stage.steps.length > 0 && (
        <div className="mt-1.5">
          <button
            data-testid="init-steps-toggle"
            type="button"
            onClick={() => setInitOpen((v) => !v)}
            className="text-[9px] text-blue-400 hover:underline"
          >
            {initOpen ? '收起步骤 ↑' : '展开步骤 ↓'}
          </button>
          {initOpen && (
            <div data-testid="init-steps-detail" className="mt-1 space-y-0.5">
              {stage.steps.map((step) => (
                <div key={step.step_key} className="flex items-center justify-between text-[9px] px-1">
                  <span className="text-gray-500 truncate">{step.step_key}</span>
                  <span>
                    <span className="text-green-600">{step.device_succeeded}</span>
                    {step.device_failed > 0 && (
                      <span className="text-red-500 ml-0.5">/{step.device_failed}✗</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

export default function BusinessFlowStepper({ timeline, isLoading, isError }: Props) {
  const stages = timeline?.stages ?? [];

  return (
    <section data-testid="business-flow-timeline" className="space-y-2">
      <SectionHeader title="业务流状态" meta="三阶段进度" color="green" />

      <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm p-4">
        {isLoading && !timeline && (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}

        {isError && (
          <div className="flex items-center gap-2 text-xs text-red-500">
            <AlertCircle className="h-4 w-4" /> 加载时间线失败
          </div>
        )}

        {!isLoading && !isError && stages.length > 0 && (
          <div className="flex items-start gap-0">
            {stages.map((stage, idx) => (
              <div key={stage.stage} className="contents">
                <StageNode
                  stage={stage}
                  isCurrent={stage.stage === timeline?.current_stage}
                />
                {idx < stages.length - 1 && (
                  <div className="flex-none w-8 h-px bg-gray-200 mt-4 self-start" />
                )}
              </div>
            ))}
          </div>
        )}

        {/* PATROL 当前步骤详情框 */}
        {(() => {
          const patrolStage = stages.find((s) => s.stage === 'patrol' && s.status === 'running');
          if (!patrolStage || patrolStage.steps.length === 0) return null;
          return (
            <div className="mt-4 rounded-lg border border-orange-100 bg-orange-50/50 px-3 py-2.5 space-y-1.5">
              <div className="text-[10px] font-bold text-orange-600 uppercase tracking-wider">
                当前 PATROL 步骤
              </div>
              {patrolStage.steps.map((step) => (
                <div key={step.step_key} className="flex items-center justify-between rounded bg-white border border-orange-100 px-2.5 py-1.5 text-[11px]">
                  <span className="font-mono text-gray-700">{step.step_key}</span>
                  <div className="flex items-center gap-1">
                    <span className="h-1.5 w-1.5 rounded-full bg-orange-400 animate-pulse" />
                    <span className="text-orange-600 font-mono">
                      {step.device_succeeded + (patrolStage.device_total - patrolStage.device_succeeded - patrolStage.device_failed)} 运行中
                    </span>
                  </div>
                </div>
              ))}
            </div>
          );
        })()}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: 运行测试确认通过**

```bash
cd frontend && npx vitest run src/components/plan-run/BusinessFlowStepper.test.tsx --reporter=verbose
```

期望：6/6 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/plan-run/BusinessFlowStepper.tsx \
        frontend/src/components/plan-run/BusinessFlowStepper.test.tsx
git commit -m "feat(ui): add BusinessFlowStepper horizontal three-stage component"
```

---

## Task 6：新建 PatrolLogPanel

**Files:**
- Create: `frontend/src/components/plan-run/PatrolLogPanel.tsx`
- Create: `frontend/src/components/plan-run/PatrolLogPanel.test.tsx`

巡检日志 Tab 内容：周期折叠 + 三维过滤（周期/设备/级别）+ 分页。  
日志数据来源：events API（stage=patrol）+ timeline（用于计算周期边界）。

- [ ] **Step 1: 写失败测试**

```tsx
// frontend/src/components/plan-run/PatrolLogPanel.test.tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PatrolLogPanel from './PatrolLogPanel';
import type { PlanRunEventsPayload, PlanRunTimeline } from '@/utils/api/types';

const timeline: PlanRunTimeline = {
  plan_run_id: 12,
  current_stage: 'patrol',
  plan_name: '24h 烧机',
  triggered_at: '2026-06-04T10:00:00Z',
  triggered_by: 'admin',
  run_type: 'MANUAL',
  stages: [
    { stage: 'init',     status: 'completed', device_total: 8, device_succeeded: 8, device_failed: 0, steps: [] },
    { stage: 'patrol',   status: 'running',   device_total: 8, device_succeeded: 7, device_failed: 0,
      patrol_cycle_index: 3, patrol_interval_seconds: 60,
      started_at: '2026-06-04T10:05:00Z', steps: [] },
    { stage: 'teardown', status: 'pending',   device_total: 0, device_succeeded: 0, device_failed: 0, steps: [] },
  ],
};

const events: PlanRunEventsPayload = {
  plan_run_id: 12,
  total: 4,
  events: [
    { ts: '2026-06-04T10:08:03Z', stage: 'patrol', severity: 'err',  category: 'log_signal', title: 'AEE 崩溃', description: 'R5CT10 上报 AEE', device_serial: 'R5CT10' },
    { ts: '2026-06-04T10:07:00Z', stage: 'patrol', severity: 'warn', category: 'log_signal', title: '内存告警', description: '可用 348MB', device_serial: 'R5CT33' },
    { ts: '2026-06-04T10:06:00Z', stage: 'patrol', severity: 'info', category: 'system',     title: '周期 #2 启动', description: '8 台设备' },
    { ts: '2026-06-04T10:05:00Z', stage: 'patrol', severity: 'info', category: 'system',     title: '周期 #1 启动', description: '8 台设备' },
  ],
  facets: { by_stage: { patrol: 4, all: 4 }, by_severity: { err: 1, warn: 1, info: 2, all: 4 } },
};

describe('PatrolLogPanel', () => {
  it('renders patrol-log-panel container', () => {
    render(<PatrolLogPanel events={events} timeline={timeline} />);
    expect(screen.getByTestId('patrol-log-panel')).toBeInTheDocument();
  });

  it('renders current cycle as expanded', () => {
    render(<PatrolLogPanel events={events} timeline={timeline} />);
    expect(screen.getByTestId('patrol-cycle-current')).toBeInTheDocument();
  });

  it('shows event titles in the expanded current cycle', () => {
    render(<PatrolLogPanel events={events} timeline={timeline} />);
    // Events in cycle #3 (10:08 is in cycle 3 if patrol started 10:05 with 60s interval)
    expect(screen.getByTestId('patrol-log-panel')).toHaveTextContent('AEE 崩溃');
  });

  it('shows total event count in header', () => {
    render(<PatrolLogPanel events={events} timeline={timeline} />);
    expect(screen.getByTestId('patrol-log-panel')).toHaveTextContent('4');
  });

  it('calls onSeverityFilterChange when severity button clicked', () => {
    const onSeverityFilterChange = vi.fn();
    render(
      <PatrolLogPanel
        events={events}
        timeline={timeline}
        onSeverityFilterChange={onSeverityFilterChange}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '异常' }));
    expect(onSeverityFilterChange).toHaveBeenCalledWith('err');
  });

  it('renders loading state without crash', () => {
    render(<PatrolLogPanel isLoading />);
    expect(screen.getByTestId('patrol-log-panel')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: 运行测试，确认 FAIL**

```bash
cd frontend && npx vitest run src/components/plan-run/PatrolLogPanel.test.tsx --reporter=verbose
```

- [ ] **Step 3: 实现 PatrolLogPanel**

```tsx
// frontend/src/components/plan-run/PatrolLogPanel.tsx
import { useMemo } from 'react';
import { Skeleton } from '@/components/ui/skeleton';
import type {
  EventSeverity,
  PlanRunEvent,
  PlanRunEventsPayload,
  PlanRunTimeline,
  TimelineStage,
} from '@/utils/api/types';

interface Props {
  events?: PlanRunEventsPayload;
  timeline?: PlanRunTimeline;
  isLoading?: boolean;
  isError?: boolean;
  severityFilter?: EventSeverity | 'all';
  onSeverityFilterChange?: (s: EventSeverity | 'all') => void;
  page?: number;
  onPageChange?: (p: number) => void;
}

const SEVERITY_DOT: Record<EventSeverity, string> = {
  ok:   'bg-green-500',
  info: 'bg-blue-500',
  warn: 'bg-amber-400',
  err:  'bg-red-500',
};

const SEV_LABEL: Record<EventSeverity, string> = { ok: '完成', info: '信息', warn: '告警', err: '异常' };

/**
 * Given a patrol stage and a list of patrol events,
 * group events by patrol cycle index computed from timestamps.
 *
 * cycle_index = floor((event_ts - patrol_started_at) / interval_seconds)
 * If patrol_started_at or interval_seconds is unavailable, treat all as cycle 0.
 */
function groupByCycle(
  events: PlanRunEvent[],
  patrolStage: TimelineStage | undefined,
): Map<number, PlanRunEvent[]> {
  const map = new Map<number, PlanRunEvent[]>();
  if (!patrolStage) {
    map.set(0, events);
    return map;
  }

  const patrolStartTs =
    (patrolStage as any).started_at
      ? new Date((patrolStage as any).started_at).getTime()
      : null;
  const intervalMs = (patrolStage.patrol_interval_seconds ?? 60) * 1000;

  for (const ev of events) {
    let cycleIdx = patrolStage.patrol_cycle_index ?? 0;
    if (patrolStartTs) {
      const evTs = new Date(ev.ts).getTime();
      const diff = evTs - patrolStartTs;
      if (diff >= 0) {
        cycleIdx = Math.floor(diff / intervalMs);
      }
    }
    const bucket = map.get(cycleIdx) ?? [];
    bucket.push(ev);
    map.set(cycleIdx, bucket);
  }

  return map;
}

function EventRow({ ev }: { ev: PlanRunEvent }) {
  const dotCls = SEVERITY_DOT[ev.severity] ?? 'bg-gray-400';
  const time = new Date(ev.ts).toLocaleTimeString('zh-CN', {
    hour: '2-digit', minute: '2-digit', second: '2-digit',
  });
  return (
    <div className="flex items-start gap-3 px-4 py-2.5 hover:bg-gray-50 transition-colors">
      <span className={`mt-1.5 h-1.5 w-1.5 rounded-full shrink-0 ${dotCls}`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-baseline gap-2 flex-wrap">
          <span className="font-mono text-[10px] text-gray-400 shrink-0">{time}</span>
          <span className="text-[11px] font-semibold text-gray-800 truncate">{ev.title}</span>
          {ev.device_serial && (
            <span className="font-mono text-[10px] text-blue-600">{ev.device_serial}</span>
          )}
        </div>
        {ev.description && (
          <div className="text-[10px] text-gray-500 mt-0.5 truncate">{ev.description}</div>
        )}
      </div>
    </div>
  );
}

const PAGE_SIZE = 20;

export default function PatrolLogPanel({
  events,
  timeline,
  isLoading = false,
  isError = false,
  severityFilter = 'all',
  onSeverityFilterChange,
  page = 1,
  onPageChange,
}: Props) {
  const patrolStage = useMemo(
    () => timeline?.stages?.find((s) => s.stage === 'patrol'),
    [timeline],
  );

  const allEvents = events?.events ?? [];
  const total = events?.total ?? 0;
  const facets = events?.facets ?? { by_stage: {}, by_severity: {} };
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  // Group by cycle
  const cycleMap = useMemo(
    () => groupByCycle(allEvents, patrolStage),
    [allEvents, patrolStage],
  );

  const currentCycleIdx = patrolStage?.patrol_cycle_index ?? 0;

  // Sorted cycle indices (descending — newest first)
  const cycleIndices = useMemo(
    () => [...cycleMap.keys()].sort((a, b) => b - a),
    [cycleMap],
  );

  return (
    <div data-testid="patrol-log-panel" className="space-y-3">
      {/* 过滤控制栏 */}
      <div className="flex flex-wrap items-center gap-2">
        {/* 级别过滤 */}
        <div className="flex items-center gap-0.5 rounded-lg border border-gray-200 bg-white p-0.5 shadow-sm">
          {(['all', 'err', 'warn', 'info'] as const).map((s) => {
            const label =
              s === 'all'
                ? `全部 ${total}`
                : `${SEV_LABEL[s]} ${facets.by_severity?.[s] ?? 0}`;
            return (
              <button
                key={s}
                type="button"
                onClick={() =>
                  onSeverityFilterChange?.(s === 'all' ? 'all' : (s as EventSeverity))
                }
                className={`rounded px-2.5 py-1 text-[10px] transition-colors ${
                  severityFilter === s
                    ? 'bg-blue-100 font-semibold text-blue-700'
                    : 'text-gray-500 hover:bg-gray-100'
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>

        <div className="ml-auto text-[10px] text-gray-400">
          共 {total} 条 · 第 {page}/{totalPages} 页
        </div>
      </div>

      {/* Loading */}
      {isLoading && !events && (
        <div className="space-y-2 rounded-xl border bg-white p-4">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-6 w-3/4" />
          <Skeleton className="h-6 w-2/3" />
        </div>
      )}

      {/* Error */}
      {isError && (
        <div className="rounded-xl border border-red-100 bg-red-50 px-4 py-3 text-xs text-red-600">
          加载巡检日志失败
        </div>
      )}

      {/* Cycle accordion */}
      {!isLoading && !isError && cycleIndices.map((idx) => {
        const cycleEvents = cycleMap.get(idx) ?? [];
        const isCurrent = idx === currentCycleIdx;
        const errCount  = cycleEvents.filter((e) => e.severity === 'err').length;
        const warnCount = cycleEvents.filter((e) => e.severity === 'warn').length;

        return isCurrent ? (
          /* 当前周期：始终展开 */
          <div
            key={idx}
            data-testid="patrol-cycle-current"
            className="overflow-hidden rounded-xl border border-orange-200 bg-white shadow-sm"
          >
            <div className="flex items-center gap-3 px-4 py-3 bg-orange-50/60 border-b border-orange-100">
              <span className="h-2 w-2 rounded-full bg-orange-400 animate-pulse" />
              <span className="text-xs font-bold text-orange-700">巡检周期 #{idx}</span>
              <span className="text-[10px] text-orange-400">进行中</span>
              <div className="ml-auto flex items-center gap-2 text-[10px]">
                {errCount > 0  && <span className="text-red-500 font-semibold">{errCount} 异常</span>}
                {warnCount > 0 && <span className="text-amber-500">{warnCount} 告警</span>}
              </div>
            </div>
            <div className="divide-y divide-gray-50">
              {cycleEvents.map((ev, i) => <EventRow key={i} ev={ev} />)}
              {cycleEvents.length === 0 && (
                <div className="py-6 text-center text-[11px] text-gray-400">暂无事件</div>
              )}
            </div>
          </div>
        ) : (
          /* 历史周期：折叠 */
          <details key={idx} className="group">
            <summary className="flex items-center gap-3 px-4 py-3 rounded-xl border border-gray-200 bg-white shadow-sm cursor-pointer list-none hover:border-gray-300 transition-colors">
              <svg
                className="h-3 w-3 text-gray-400 transition-transform group-open:rotate-90"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
              </svg>
              <span className="h-1.5 w-1.5 rounded-full bg-green-400 shrink-0" />
              <span className="text-xs font-semibold text-gray-700">巡检周期 #{idx}</span>
              <span className="text-[10px] text-gray-400">已完成</span>
              <div className="ml-auto flex items-center gap-2 text-[10px]">
                {errCount > 0  && <span className="text-red-500 font-semibold">{errCount} 异常</span>}
                {warnCount > 0 && <span className="text-amber-500">{warnCount} 告警</span>}
                {errCount === 0 && warnCount === 0 && <span className="text-gray-400">无异常</span>}
              </div>
            </summary>
            <div className="mt-1 overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm divide-y divide-gray-50">
              {cycleEvents.map((ev, i) => <EventRow key={i} ev={ev} />)}
            </div>
          </details>
        );
      })}

      {/* 分页 */}
      {totalPages > 1 && (
        <div className="flex items-center justify-between pt-1">
          <span className="text-[10px] text-gray-400">共 {total} 条事件</span>
          <div className="flex items-center gap-1">
            <button
              type="button"
              disabled={page <= 1}
              onClick={() => onPageChange?.(page - 1)}
              className="rounded border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-500 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              上一页
            </button>
            {Array.from({ length: Math.min(totalPages, 5) }, (_, i) => i + 1).map((p) => (
              <button
                key={p}
                type="button"
                onClick={() => onPageChange?.(p)}
                className={`rounded border px-2.5 py-1 text-[10px] ${
                  p === page
                    ? 'border-blue-300 bg-blue-50 font-semibold text-blue-700'
                    : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'
                }`}
              >
                {p}
              </button>
            ))}
            <button
              type="button"
              disabled={page >= totalPages}
              onClick={() => onPageChange?.(page + 1)}
              className="rounded border border-gray-200 bg-white px-2.5 py-1 text-[10px] text-gray-500 hover:bg-gray-50 disabled:opacity-40 disabled:cursor-not-allowed"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: 运行 PatrolLogPanel 测试确认通过**

```bash
cd frontend && npx vitest run src/components/plan-run/PatrolLogPanel.test.tsx --reporter=verbose
```

期望：6/6 PASS。

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/plan-run/PatrolLogPanel.tsx \
        frontend/src/components/plan-run/PatrolLogPanel.test.tsx
git commit -m "feat(ui): add PatrolLogPanel with cycle accordion and severity filter"
```

---

## Task 7：PlanRunDetailPage 全量重构

**Files:**
- Modify: `frontend/src/pages/execution/PlanRunDetailPage.tsx`
- Modify: `frontend/src/pages/execution/PlanRunDetailPage.test.tsx`

双栏布局（256px 左侧固定 + 右侧主区）+ Tab 系统 + 所有新组件接入。  
**关键 testid 保留清单**（集成测试依赖）：

| testid | 来源组件 | 说明 |
|--------|----------|------|
| `plan-run-status-pill` | PlanRunHero | ✓ Task 2 已保留 |
| `plan-run-abort-btn` / `plan-run-abort-confirm` | PlanRunHero | ✓ Task 2 已保留 |
| `plan-run-tabs` | PlanRunDetailPage | 新增，Tab 容器 |
| `device-overview` | DeviceOverview | ✓ 不变 |
| `device-overview-table-btn` | DeviceOverview | ✓ 不变 |
| `watcher-summary` | AnomalyDashboard | ✓ Task 4 已保留 |
| `watcher-threshold-banner` | AnomalyDashboard | ✓ Task 4 已保留 |
| `business-flow-timeline` | BusinessFlowStepper | ✓ Task 5 已保留 |
| `precheck-row` | BusinessFlowTimeline（左侧面板复用）| 须在左侧面板中渲染 |
| `dispatch-gate-section` | 左侧面板 | 新位置，保留 testid |
| `dispatch-gate-card` | DispatchGateCard | ✓ 不变 |
| `dispatch-gate-host-{id}` | DispatchGateCard | ✓ 不变 |
| `stuck-jobs-banner` | PlanRunDetailPage | 保留在 Tab 内容顶部 |
| `minimap-cell-{jobId}` | DeviceOverview | ✓ 不变 |
| `device-drawer` | DeviceDetailDrawer | ✓ 不变 |
| `chain-dispatch-failed-banner` | PlanChainBreadcrumb | 移入左侧面板 |
| `chain-node-{planId}` | PlanChainBreadcrumb | ✓ 不变 |

**注意**：`precheck-row` 目前在 BusinessFlowTimeline 内部。新设计中，precheck 状态显示在左侧面板的派发门禁区域。需要把 PrecheckRow（及 testid）渲染到左侧面板中，而非 BusinessFlowStepper 中。具体实现：左侧面板引用 `BusinessFlowTimeline` 中提取出来的 `PrecheckRow`（可以通过在 BusinessFlowTimeline.tsx 中 export 该子组件，或在 PlanRunDetailPage 内直接内联相同逻辑）。最简单方案：在左侧面板 DispatchGate 展开内容中渲染 `<BusinessFlowTimeline>` 的 precheck 部分，保留 testid。

- [ ] **Step 1: 更新 PlanRunDetailPage.tsx**

完整替换为如下实现（保留所有 query、mutation、socketIO 逻辑）：

```tsx
// frontend/src/pages/execution/PlanRunDetailPage.tsx
import { useCallback, useMemo, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { AlertCircle, AlertTriangle, ChevronDown } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { useToast } from '@/components/ui/toast';
import { useSocketIO, type SocketIOMessage } from '@/hooks/useSocketIO';
import { api } from '@/utils/api';
import { SOCKET_MESSAGE_TYPES } from '@/utils/socketEvents';
import type {
  DeviceMatrixItem,
  DeviceUiStatus,
  EventSeverity,
  PlanRun,
  PlanRunStatus,
} from '@/utils/api/types';

// Components
import PlanRunHero from '@/components/plan-run/PlanRunHero';
import PlanRunKpiGrid from '@/components/plan-run/PlanRunKpiGrid';
import AnomalyDashboard from '@/components/plan-run/AnomalyDashboard';
import BusinessFlowStepper from '@/components/plan-run/BusinessFlowStepper';
import DeviceOverview from '@/components/plan-run/DeviceOverview';
import DeviceDetailDrawer from '@/components/plan-run/DeviceDetailDrawer';
import PatrolLogPanel from '@/components/plan-run/PatrolLogPanel';
import PlanChainBreadcrumb from '@/components/plan-run/PlanChainBreadcrumb';
import DispatchGateCard from '@/components/plan-run/DispatchGateCard';

// ── Constants ───────────────────────────────────────────────────────────────

const TERMINAL: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DEGRADED',
];

const GATE_ACTIVE_REFETCH_MS = 3_000;
const FAST_REFETCH_MS = 10_000;
const SLOW_REFETCH_MS = 30_000;
const STALE_PATROL_HEARTBEAT_MS = 180_000;
const STALE_INIT_HEARTBEAT_MS = 900_000;

function isDispatchGateActive(run: PlanRun | undefined): boolean {
  if (!run || run.status !== 'RUNNING') return false;
  const precheck = run.run_context?.precheck;
  const dispatch = run.run_context?.dispatch_state;
  if (!precheck) {
    return dispatch?.status === 'queued' || dispatch?.status === 'running';
  }
  if (precheck.phase !== 'ready' && precheck.phase !== 'failed') return true;
  if (precheck.phase === 'ready') {
    const dispatchStatus = dispatch?.status;
    return dispatchStatus !== 'completed' && dispatchStatus !== 'failed';
  }
  return false;
}

function isJobStuck(d: DeviceMatrixItem, now = Date.now()): boolean {
  if (d.job_status !== 'RUNNING') return false;
  if (d.last_heartbeat_at) {
    const t = new Date(d.last_heartbeat_at).getTime();
    if (!Number.isNaN(t) && now - t > STALE_PATROL_HEARTBEAT_MS) return true;
  }
  if (d.current_stage === 'patrol') return false;
  if (d.started_at) {
    const t = new Date(d.started_at).getTime();
    if (!Number.isNaN(t) && now - t > STALE_INIT_HEARTBEAT_MS) return true;
  }
  return false;
}

type ActiveTab = 'detail' | 'logs';

// ── Component ───────────────────────────────────────────────────────────────

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const qc = useQueryClient();
  const toast = useToast();

  // Tab & filter state
  const [activeTab, setActiveTab] = useState<ActiveTab>('detail');
  const [deviceStatusFilter, setDeviceStatusFilter] = useState<DeviceUiStatus | 'all'>('all');
  const [deviceHostFilter, setDeviceHostFilter] = useState<string | 'all'>('all');
  const [watcherWindow, setWatcherWindow] = useState<number>(60);
  const [logSeverityFilter, setLogSeverityFilter] = useState<EventSeverity | 'all'>('all');
  const [logPage, setLogPage] = useState(1);
  const [selectedDevice, setSelectedDevice] = useState<DeviceMatrixItem | null>(null);
  const [diagOpen, setDiagOpen] = useState(false);

  // ── Queries ──────────────────────────────────────────────────────────────

  const runQ = useQuery({
    queryKey: ['plan-run', id],
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    refetchInterval: (data) => {
      if (data && TERMINAL.includes(data.status)) return false;
      return isDispatchGateActive(data) ? GATE_ACTIVE_REFETCH_MS : FAST_REFETCH_MS;
    },
  });

  const isTerminal = !!runQ.data && TERMINAL.includes(runQ.data.status);
  const gateActive = isDispatchGateActive(runQ.data);
  const refetchInterval = isTerminal
    ? false
    : gateActive ? GATE_ACTIVE_REFETCH_MS : FAST_REFETCH_MS;

  const timelineQ = useQuery({
    queryKey: ['plan-run-timeline', id],
    queryFn: () => api.planRuns.getTimeline(id),
    enabled: !!id,
    refetchInterval,
  });

  // Patrol events (for PatrolLogPanel)
  const patrolEventsQ = useQuery({
    queryKey: ['plan-run-events', id, 'patrol', logSeverityFilter, logPage],
    queryFn: () =>
      api.planRuns.getEvents(id, {
        stage: 'patrol',
        severity: logSeverityFilter,
        limit: 20,
        offset: (logPage - 1) * 20,
      }),
    enabled: !!id,
    refetchInterval,
  });

  const devicesQ = useQuery({
    queryKey: ['plan-run-devices', id, deviceStatusFilter, deviceHostFilter],
    queryFn: () => api.planRuns.getDevices(id, { status: deviceStatusFilter, host_id: deviceHostFilter }),
    enabled: !!id,
    refetchInterval,
  });

  const watcherQ = useQuery({
    queryKey: ['plan-run-watcher', id, watcherWindow],
    queryFn: () => api.planRuns.getWatcherSummary(id, watcherWindow),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  const chainQ = useQuery({
    queryKey: ['plan-run-chain', id],
    queryFn: () => api.planRuns.getChain(id),
    enabled: !!id,
    refetchInterval: isTerminal ? false : refetchInterval,
  });

  const chainDispatchFailed = useMemo(() => {
    const summary = runQ.data?.result_summary;
    const fail = summary?.chain_dispatch_failed;
    if (fail && typeof fail === 'object' && 'error' in fail) return fail;
    return null;
  }, [runQ.data?.result_summary]);

  const showPlanChain =
    chainDispatchFailed != null
    || (chainQ.data?.nodes?.length ?? 0) > 1
    || chainQ.isLoading;

  const stuckJobs = useMemo(() => {
    if (isTerminal || !devicesQ.data?.devices?.length) return [];
    const now = Date.now();
    return devicesQ.data.devices.filter((d) => isJobStuck(d, now));
  }, [devicesQ.data, isTerminal]);

  const planName = useMemo(() => timelineQ.data?.plan_name ?? null, [timelineQ.data?.plan_name]);

  const patrolCycle = useMemo(
    () => timelineQ.data?.stages?.find((s) => s.stage === 'patrol')?.patrol_cycle_index ?? null,
    [timelineQ.data],
  );

  // ── SocketIO ─────────────────────────────────────────────────────────────

  const onSocketMessage = useCallback(
    (msg: SocketIOMessage<unknown>) => {
      if (!id) return;
      if (msg.type === SOCKET_MESSAGE_TYPES.JOB_STATUS) {
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PLAN_RUN_STATUS) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-chain', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.PRECHECK_UPDATE) {
        qc.invalidateQueries({ queryKey: ['plan-run', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
      } else if (msg.type === SOCKET_MESSAGE_TYPES.WATCHER_SIGNAL) {
        qc.invalidateQueries({ queryKey: ['plan-run-watcher', id] });
        qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      }
    },
    [id, qc],
  );

  useSocketIO(id ? `/ws/plan-runs/${id}` : '', {
    enabled: !!id && !isTerminal,
    onMessage: onSocketMessage,
  });

  // ── Mutations ─────────────────────────────────────────────────────────────

  const abortMut = useMutation({
    mutationFn: (reason: string) => api.planRuns.abort(id, reason),
    onSuccess: (data) => {
      toast.success(`PlanRun 中止已发起 — 状态: ${data.status}`);
      qc.invalidateQueries({ queryKey: ['plan-run', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => toast.error(`中止失败: ${err instanceof Error ? err.message : String(err)}`),
  });

  const retryMut = useMutation({
    mutationFn: (jobId: number) => api.planRuns.manualRetryJob(id, jobId),
    onSuccess: (data) => {
      toast.success(`已请求 Job #${data.job_id} 立即重试`);
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => toast.error(`重试失败: ${err instanceof Error ? err.message : String(err)}`),
  });

  const exitMut = useMutation({
    mutationFn: (jobId: number) => api.planRuns.manualExitJob(id, jobId),
    onSuccess: (data) => {
      toast.success(`已请求 Job #${data.job_id} 退出`);
      qc.invalidateQueries({ queryKey: ['plan-run-devices', id] });
    },
    onError: (err: unknown) => toast.error(`退出失败: ${err instanceof Error ? err.message : String(err)}`),
  });

  const retryDispatchMut = useMutation({
    mutationFn: () => api.planRuns.retryDispatch(id),
    onSuccess: () => {
      toast.success('已重新入队派发门禁');
      qc.invalidateQueries({ queryKey: ['plan-run', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-timeline', id] });
      qc.invalidateQueries({ queryKey: ['plan-run-events', id] });
    },
    onError: (err: unknown) => toast.error(`重试派发失败: ${err instanceof Error ? err.message : String(err)}`),
  });

  // ── Error / invalid states ────────────────────────────────────────────────

  if (!id || Number.isNaN(id)) {
    return (
      <div className="flex h-64 items-center justify-center text-sm text-gray-500">
        <AlertCircle className="mr-2 h-4 w-4" /> 无效 PlanRun ID
      </div>
    );
  }

  if (runQ.isError) {
    return (
      <div className="space-y-3 p-4">
        <Button variant="ghost" size="sm" onClick={() => navigate('/execution/plan-runs')}>
          返回列表
        </Button>
        <div className="flex h-48 items-center justify-center rounded-lg border bg-red-50 text-sm text-red-700">
          <AlertCircle className="mr-2 h-4 w-4" />
          {(runQ.error as Error)?.message || '加载 PlanRun 失败'}
        </div>
      </div>
    );
  }

  const precheck = runQ.data?.run_context?.precheck ?? null;
  const dispatchState = runQ.data?.run_context?.dispatch_state ?? null;
  const gateFailed = precheck?.phase === 'failed' || dispatchState?.status === 'failed';
  const showDiag = diagOpen || gateFailed;

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="flex" style={{ minHeight: '100vh' }}>

      {/* ──────── 左侧固定面板 ──────── */}
      <aside
        className="w-64 shrink-0 border-r border-gray-200 bg-white flex flex-col gap-0"
        style={{ position: 'sticky', top: 0, height: '100vh', overflowY: 'auto' }}
      >
        {/* 返回 */}
        <div className="px-4 pt-3 pb-1">
          <button
            type="button"
            onClick={() => navigate('/execution/plan-runs')}
            className="flex items-center gap-1 text-[11px] text-gray-400 hover:text-gray-600 transition-colors"
          >
            ← 返回执行列表
          </button>
        </div>

        {/* Hero */}
        <div className="p-3">
          {runQ.isLoading ? (
            <Skeleton className="h-48 w-full rounded-xl" />
          ) : (
            <PlanRunHero
              run={runQ.data}
              planName={planName}
              isAborting={abortMut.isPending}
              onAbort={(reason) => abortMut.mutate(reason)}
              onExportReport={async () => {
                try {
                  const blob = await api.planRuns.exportReport(id, 'markdown');
                  const url = URL.createObjectURL(blob);
                  const a = document.createElement('a');
                  a.href = url; a.download = `plan-run-${id}-report.md`; a.click();
                  URL.revokeObjectURL(url);
                  toast.success('PlanRun 报告已导出');
                } catch (err: unknown) {
                  toast.error(`导出失败: ${err instanceof Error ? err.message : String(err)}`);
                }
              }}
            />
          )}
        </div>

        {/* KPI 宫格 */}
        <div className="px-3 pb-3">
          <PlanRunKpiGrid
            devices={devicesQ.data}
            currentStage={timelineQ.data?.current_stage ?? null}
            patrolCycle={patrolCycle}
          />
        </div>

        {/* 执行链 */}
        {showPlanChain && (
          <div className="px-3 pb-3">
            <PlanChainBreadcrumb
              chain={chainQ.data}
              isLoading={chainQ.isLoading}
              isError={chainQ.isError}
              chainDispatchFailed={chainDispatchFailed}
              onNavigateRun={(planRunId) => navigate(`/execution/plan-runs/${planRunId}`)}
            />
          </div>
        )}

        {/* 派发门禁（含 precheck-row） */}
        {precheck && (
          <div
            data-testid="dispatch-gate-section"
            className="px-3 pb-3"
          >
            <button
              type="button"
              data-testid="dispatch-gate-section-toggle"
              onClick={() => setDiagOpen((v) => !v)}
              className="w-full flex items-center gap-2 rounded-lg border border-gray-100 bg-gray-50 px-3 py-2 text-left hover:bg-gray-100 transition-colors"
            >
              <ChevronDown
                className={`h-3 w-3 text-gray-400 transition-transform ${showDiag ? '' : '-rotate-90'}`}
              />
              <span className="text-[10px] font-bold uppercase tracking-wider text-gray-600">派发门禁</span>
              {gateFailed
                ? <span className="ml-auto h-1.5 w-1.5 rounded-full bg-red-500" />
                : <span className="ml-auto text-[9px] text-green-500 font-semibold">✓ 通过</span>
              }
            </button>
            <div className={showDiag ? 'mt-2' : 'hidden'}>
              <DispatchGateCard
                precheck={precheck}
                dispatchState={dispatchState}
                isTerminal={isTerminal}
                onRetryDispatch={() => retryDispatchMut.mutate()}
                isRetrying={retryDispatchMut.isPending}
              />
            </div>
          </div>
        )}
      </aside>

      {/* ──────── 右侧主区 ──────── */}
      <main className="flex-1 flex flex-col bg-slate-50" style={{ minHeight: '100vh' }}>

        {/* Tab 导航 */}
        <div
          data-testid="plan-run-tabs"
          className="sticky top-0 z-40 flex items-center border-b border-gray-200 bg-white shadow-sm px-5"
        >
          {(
            [
              { key: 'detail', label: '运行详情' },
              {
                key: 'logs',
                label: '巡检日志',
                badge: patrolCycle != null ? `PATROL #${patrolCycle}` : undefined,
              },
            ] as const
          ).map(({ key, label, badge }) => (
            <button
              key={key}
              type="button"
              onClick={() => setActiveTab(key)}
              className={`px-4 py-3 text-xs transition-colors border-b-2 ${
                activeTab === key
                  ? 'border-blue-500 font-bold text-blue-600'
                  : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
            >
              {label}
              {badge && (
                <span className="ml-1.5 rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-semibold text-orange-600">
                  {badge}
                </span>
              )}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-2 py-2 pr-1 text-[10px] text-gray-400">
            <span className="h-1.5 w-1.5 rounded-full bg-green-400 animate-pulse" />
            实时同步
          </div>
        </div>

        {/* ── 运行详情 Panel ── */}
        <div className={`flex-1 p-5 space-y-4 ${activeTab === 'detail' ? '' : 'hidden'}`}>

          {/* 心跳超时警告 */}
          {stuckJobs.length > 0 && (
            <div
              data-testid="stuck-jobs-banner"
              className="flex items-start gap-2 rounded-xl border border-amber-200 bg-amber-50 px-4 py-2.5 text-xs text-amber-900"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
              <div className="min-w-0 space-y-1">
                <p className="font-semibold">{stuckJobs.length} 个 Job 心跳超时，可能已失联</p>
                <p className="text-xs text-amber-800/90">
                  设备：{stuckJobs.map((d) => d.device_serial || `#${d.device_id}`).join('、')}
                </p>
              </div>
            </div>
          )}

          {/* 设备总览 */}
          <DeviceOverview
            data={devicesQ.data}
            isLoading={devicesQ.isLoading}
            isError={devicesQ.isError}
            statusFilter={deviceStatusFilter}
            hostFilter={deviceHostFilter}
            onStatusFilterChange={setDeviceStatusFilter}
            onHostFilterChange={setDeviceHostFilter}
            onSelectDevice={setSelectedDevice}
          />

          {/* 异常仪表盘 */}
          <AnomalyDashboard
            data={watcherQ.data}
            isLoading={watcherQ.isLoading}
            isError={watcherQ.isError}
            windowMinutes={watcherWindow}
            onWindowChange={setWatcherWindow}
          />

          {/* 业务流步进器 */}
          <BusinessFlowStepper
            timeline={timelineQ.data}
            isLoading={timelineQ.isLoading}
            isError={timelineQ.isError}
          />

          <div className="h-8" />
        </div>

        {/* ── 巡检日志 Panel ── */}
        <div className={`flex-1 p-5 ${activeTab === 'logs' ? '' : 'hidden'}`}>
          <PatrolLogPanel
            events={patrolEventsQ.data}
            timeline={timelineQ.data}
            isLoading={patrolEventsQ.isLoading}
            isError={patrolEventsQ.isError}
            severityFilter={logSeverityFilter}
            onSeverityFilterChange={(s) => { setLogSeverityFilter(s); setLogPage(1); }}
            page={logPage}
            onPageChange={setLogPage}
          />
        </div>

      </main>

      {/* Device detail drawer */}
      <DeviceDetailDrawer
        device={selectedDevice}
        onClose={() => setSelectedDevice(null)}
        onManualRetry={(jobId) => retryMut.mutate(jobId)}
        onManualExit={(jobId) => exitMut.mutate(jobId)}
        onOpenReport={(jobId) => navigate(`/runs/${jobId}/report`)}
        isRetryPending={retryMut.isPending}
        isExitPending={exitMut.isPending}
      />
    </div>
  );
}
```

- [ ] **Step 2: 更新集成测试 PlanRunDetailPage.test.tsx**

在现有测试的 `mocks` 中增加 `retryDispatch`，并更新与布局相关的查找方式：

```typescript
// 在 mocks 对象中新增：
retryDispatch: vi.fn(),

// 在 vi.mock('@/utils/api') 的 planRuns 中新增：
retryDispatch: mocks.retryDispatch,

// beforeEach 新增：
mocks.retryDispatch.mockResolvedValue({ plan_run_id: 12, status: 'RUNNING' });
```

同时将所有查找 `business-flow-timeline` 保持不变（BusinessFlowStepper 保留该 testid），  
将所有查找 `watcher-summary` / `watcher-threshold-banner` 保持不变（AnomalyDashboard 保留这些 testid）。

> **注意**：`precheck-row` 现在渲染在左侧面板的 `DispatchGateCard` 内。现有测试中使用 `getByTestId('precheck-row')` 仍然有效，因为 DispatchGateCard 保留该 testid，且 `showDiag` 默认展开条件（`precheck.phase === 'syncing'` 时非 gateFailed，但 `diagOpen` 默认 false）——需要让 precheck 在 `phase='syncing'` 时自动展开门禁面板，或修改初始值。
>
> **修复方案**：将 `const [diagOpen, setDiagOpen] = useState(false)` 改为：
> ```typescript
> const [diagOpen, setDiagOpen] = useState(false);
> // precheck 非 ready/null 时自动展开
> const showDiag = diagOpen || gateFailed || (precheck != null && precheck.phase !== 'ready');
> ```

- [ ] **Step 3: 运行 PlanRunDetailPage 集成测试**

```bash
cd frontend && npx vitest run src/pages/execution/PlanRunDetailPage.test.tsx --reporter=verbose
```

逐条确认通过。如有失败，按 testid 追踪对应组件并修复。

- [ ] **Step 4: 全量回归测试**

```bash
cd frontend && npx vitest run --reporter=verbose 2>&1 | tail -30
```

期望：无新 FAIL（旧组件测试全部通过）。

- [ ] **Step 5: TypeScript 类型检查**

```bash
cd frontend && npx tsc --noEmit 2>&1 | head -40
```

期望：0 errors。

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/execution/PlanRunDetailPage.tsx \
        frontend/src/pages/execution/PlanRunDetailPage.test.tsx
git commit -m "feat(ui): restructure PlanRunDetailPage with left panel + tab layout"
```

---

## Task 8：PatrolLogPanel 中 SVG 属性修正

**注意**：Task 6 Step 3 中的 PatrolLogPanel 代码有一处 JSX 错误：SVG `<path>` 的 HTML 属性需用 camelCase（`strokeLinecap`、`strokeLinejoin`、`strokeWidth`）。

- [ ] **Step 1: 修正 PatrolLogPanel.tsx 中的 SVG 属性**

将 PatrolLogPanel.tsx 中 `<details>` summary 内的 SVG path 改为：

```tsx
<path strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" d="M9 5l7 7-7 7" />
```

- [ ] **Step 2: 运行类型检查确认无错误**

```bash
cd frontend && npx tsc --noEmit 2>&1 | grep -i "patrol"
```

期望：无输出。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/components/plan-run/PatrolLogPanel.tsx
git commit -m "fix: use camelCase SVG attributes in PatrolLogPanel"
```

---

## 最终验收

- [ ] **全量 Vitest**

```bash
cd frontend && npx vitest run 2>&1 | tail -5
```

期望输出格式：`X passed (X)` — 无 FAIL，无新 skipped。

- [ ] **TypeScript 无错误**

```bash
cd frontend && npx tsc --noEmit
```

期望：静默退出（exit code 0）。

- [ ] **构建验证**

```bash
cd frontend && npm run build 2>&1 | tail -10
```

期望：`✓ built in Xs`。

---

## 设计参考

静态设计稿：`docs/archive/prototypes/plan-run-detail-v2.html`（本地预览：在 `docs/archive/prototypes/` 下 `python -m http.server 9900`）

关键视觉对照：
- 左侧面板宽 256px，Hero 用 `bg-gradient-to-br from-{status-color}-50/80 to-white`
- 状态 badge：`rounded-xl border px-3.5 py-2`，RUNNING 带 ping 动画
- KPI 宫格：2×3，数字 `text-2xl font-mono font-bold`
- Section header 竖条：`h-4 w-1 rounded-full`，标题 `text-sm font-bold`
- AnomalyDashboard Gauge ring：SVG，r=30，`strokeDasharray` 按比例填充
- Tab bar：`border-b-2 border-blue-500` 激活样式，固定在主区顶部
