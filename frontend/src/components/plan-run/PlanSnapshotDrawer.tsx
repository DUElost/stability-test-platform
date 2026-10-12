import { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { ChevronDown, ChevronRight, X } from 'lucide-react';
import { ParameterProjectionList } from '@/components/parameters/ParameterProjectionList';
import { Button } from '@/components/ui/button';
import { DRAWER, PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type {
  DeviceMatrixItem,
  ParameterProjection,
  PlanSnapshot,
  PlanSnapshotStep,
} from '@/utils/api/types';
import { formatScriptIdentity } from './scriptIdentity';

/**
 * #3350（ADR-0023 D4）+ #3653 U4（ADR-0060）：PlanRun「查看快照」抽屉。
 *
 * 步骤身份仍来自 detail 已返回的 `plan_snapshot`（不 lazy 拉原始参数）。
 * 参数 / 设置 / watcher / 折叠 JSON 只消费服务端安全投影：默认 L2
 *（`detail.parameter_projection`），选中具体 Job 后切换为 L3。
 */

interface Props {
  open: boolean;
  onClose: () => void;
  /** `GET /plan-runs/{id}` 已返回的 plan_snapshot（旧 PlanRun 可能为 null） */
  snapshot?: PlanSnapshot | null;
  /** detail 内嵌 L2；缺省或失败时不得回退原始参数 */
  l2Projection?: ParameterProjection | null;
  l2Unavailable?: boolean;
  devices?: DeviceMatrixItem[];
  selectedJobId?: number | null;
  onSelectJobId?: (jobId: number | null) => void;
  l3Projection?: ParameterProjection | null;
  l3Loading?: boolean;
  l3Unavailable?: boolean;
}

/** 旧快照可能缺字段（迁移前只有部分键）——统一按 Partial 读，缺即 '—'。 */
type SnapshotStep = Partial<PlanSnapshotStep>;

const STAGE_ORDER: Record<string, number> = { init: 0, patrol: 1, teardown: 2 };
const STAGE_LABEL: Record<string, string> = {
  init: '初始化',
  patrol: '巡检',
  teardown: '清理',
};

function sortSteps(steps: SnapshotStep[]): SnapshotStep[] {
  return [...steps].sort((a, b) => {
    const sa = STAGE_ORDER[String(a.stage ?? '')] ?? 99;
    const sb = STAGE_ORDER[String(b.stage ?? '')] ?? 99;
    if (sa !== sb) return sa - sb;
    return (a.sort_order ?? 0) - (b.sort_order ?? 0);
  });
}

function jsonBlock(value: unknown): string {
  if (value == null) return '—';
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

function deviceLabel(device: DeviceMatrixItem): string {
  const serial = device.device_serial || `device#${device.device_id}`;
  return `${serial} · job#${device.job_id}`;
}

function StepIdentityCard({ step }: { step: SnapshotStep }) {
  const identity = formatScriptIdentity(step.script_name, step.script_version);
  const stage = String(step.stage ?? '');
  return (
    <div
      className={cn(PANEL.root, 'p-2.5')}
      data-testid={`snapshot-step-${stage}-${step.step_key ?? ''}`}
    >
      <div className="flex w-full items-center gap-1.5 text-left">
        <span className={cn('truncate font-mono text-xs font-semibold', TEXT.heading)}>
          {step.step_key || '—'}
        </span>
        <span className={cn('shrink-0 rounded border px-1.5 py-0.5 text-[10px]', TEXT.subtitle)}>
          {STAGE_LABEL[stage] ?? stage ?? '—'}
        </span>
        <span
          className={cn('ml-auto shrink-0 font-mono text-[11px]', TEXT.subtitle)}
          data-testid={`snapshot-step-script-${stage}-${step.step_key ?? ''}`}
        >
          {step.script_name ? (
            <Link
              to={`/script-management?name=${encodeURIComponent(step.script_name)}${
                step.script_version ? `&version=${encodeURIComponent(step.script_version)}` : ''
              }`}
              className="hover:underline"
            >
              {identity}
            </Link>
          ) : (
            '—'
          )}
        </span>
      </div>
      <div className={cn('mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[10px]', TEXT.subtitle)}>
        <span>timeout {step.timeout_seconds ?? '—'}s</span>
        <span>retry {step.retry ?? 0}</span>
        <span>{step.enabled === false ? '已禁用' : '启用'}</span>
        {step.nfs_path ? <span className="truncate font-mono">{step.nfs_path}</span> : null}
      </div>
    </div>
  );
}

function ProjectionContextBar({
  projection,
  layerLabel,
}: {
  projection: ParameterProjection;
  layerLabel: string;
}) {
  const ctx = projection.context;
  return (
    <div
      className={cn('rounded-lg border px-3 py-2 text-[11px]', TEXT.subtitle)}
      data-testid="snapshot-projection-context"
    >
      <div className="flex flex-wrap gap-x-3 gap-y-1">
        <span data-testid="snapshot-projection-layer">{layerLabel}</span>
        {ctx.job_id != null ? <span data-testid="snapshot-context-job">job_id {ctx.job_id}</span> : null}
        {ctx.device_id != null ? (
          <span data-testid="snapshot-context-device">device_id {ctx.device_id}</span>
        ) : null}
        {ctx.host_id ? <span data-testid="snapshot-context-host">host_id {ctx.host_id}</span> : null}
        <span data-testid="snapshot-context-authority">authority {ctx.authority}</span>
        <span data-testid="snapshot-context-read-at">读取 {ctx.read_at}</span>
      </div>
    </div>
  );
}

function SafeDebugBlock({ projection }: { projection: ParameterProjection }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="rounded-lg border" data-testid="snapshot-safe-debug">
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
        data-testid="snapshot-safe-debug-toggle"
        className="flex w-full items-center gap-1.5 px-3 py-2 text-left text-xs"
      >
        {expanded ? (
          <ChevronDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        ) : (
          <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        )}
        <span className="font-medium">安全 debug JSON</span>
        <span className={cn('ml-auto', TEXT.subtitle)}>仅 safe_debug</span>
      </button>
      {expanded ? (
        <pre
          className="overflow-x-auto border-t bg-muted/40 p-2 text-[10px] leading-snug"
          data-testid="snapshot-safe-debug-body"
        >
          {jsonBlock(projection.safe_debug)}
        </pre>
      ) : null}
    </div>
  );
}

export default function PlanSnapshotDrawer({
  open,
  onClose,
  snapshot,
  l2Projection = null,
  l2Unavailable = false,
  devices = [],
  selectedJobId = null,
  onSelectJobId,
  l3Projection = null,
  l3Loading = false,
  l3Unavailable = false,
}: Props) {
  const steps = useMemo<SnapshotStep[]>(() => {
    const raw = snapshot && typeof snapshot === 'object' ? snapshot.steps : null;
    if (!Array.isArray(raw)) return [];
    return sortSteps(
      raw.filter((s) => Boolean(s) && typeof s === 'object') as SnapshotStep[],
    );
  }, [snapshot]);
  const plan = useMemo<Record<string, unknown>>(() => {
    const raw = snapshot && typeof snapshot === 'object' ? snapshot.plan : null;
    return raw && typeof raw === 'object' ? (raw as Record<string, unknown>) : {};
  }, [snapshot]);

  const selectedDevice = useMemo(
    () =>
      selectedJobId == null
        ? null
        : devices.find((device) => device.job_id === selectedJobId) ?? null,
    [devices, selectedJobId],
  );

  const showingL3 = selectedJobId != null;
  const activeProjection = showingL3 ? l3Projection : l2Projection;
  const projectionUnavailable = showingL3
    ? l3Unavailable || (!l3Loading && l3Projection == null)
    : l2Unavailable || l2Projection == null;
  const hasSnapshotIdentity = steps.length > 0;

  if (!open) return null;

  return (
    <>
      <div data-testid="plan-snapshot-overlay" onClick={onClose} className={DRAWER.overlay} />
      <aside
        data-testid="plan-snapshot-drawer"
        role="dialog"
        aria-modal="true"
        aria-label="计划快照"
        className={DRAWER.panel}
      >
        <header className="flex items-center justify-between border-b px-4 py-3">
          <div className="min-w-0">
            <p className={cn('truncate text-xs', TEXT.subtitle)}>运行设计与按设备下发</p>
            <h2 className={cn('truncate text-base font-semibold', TEXT.heading)}>计划快照</h2>
          </div>
          <Button
            variant="ghost"
            size="sm"
            onClick={onClose}
            data-testid="plan-snapshot-close"
            aria-label="关闭计划快照"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3 text-sm">
          {!hasSnapshotIdentity ? (
            <div
              className={cn('rounded-lg border border-dashed p-4 text-center text-xs', TEXT.subtitle)}
              data-testid="plan-snapshot-empty"
            >
              该 PlanRun 没有可浏览的快照（早于快照机制或快照为空）；未回读当前 Plan 或 Script。
            </div>
          ) : (
            <section className="space-y-1" data-testid="plan-snapshot-plan-meta">
              <div className={cn('text-xs font-semibold', TEXT.heading)}>
                {String(plan.name ?? '—')}
              </div>
              <div className={cn('flex flex-wrap gap-x-3 text-[11px]', TEXT.subtitle)}>
                <span>patrol_interval_seconds {String(plan.patrol_interval_seconds ?? '—')}</span>
                <span>timeout_seconds {String(plan.timeout_seconds ?? '—')}</span>
              </div>
            </section>
          )}

          <section className="space-y-2" data-testid="snapshot-device-picker">
            <label className={cn('block text-xs font-medium', TEXT.heading)} htmlFor="snapshot-job-select">
              参数视图
            </label>
            <select
              id="snapshot-job-select"
              data-testid="snapshot-job-select"
              className="w-full rounded-md border bg-background px-2 py-1.5 text-xs"
              value={selectedJobId ?? ''}
              onChange={(event) => {
                const raw = event.target.value;
                onSelectJobId?.(raw === '' ? null : Number(raw));
              }}
            >
              <option value="">运行设计（L2，冻结快照）</option>
              {devices.map((device) => (
                <option key={device.job_id} value={device.job_id}>
                  {deviceLabel(device)}
                  {device.host_id ? ` @ ${device.host_id}` : ''}
                </option>
              ))}
            </select>
            {devices.length === 0 ? (
              <p className={cn('text-[11px]', TEXT.subtitle)} data-testid="snapshot-no-jobs">
                当前没有可选择的 Job/设备；未物化或未 claim 时不能伪造下发值。
              </p>
            ) : null}
            {showingL3 && selectedDevice ? (
              <p className={cn('text-[11px]', TEXT.subtitle)} data-testid="snapshot-selected-device">
                已选 device_id {selectedDevice.device_id}
                {selectedDevice.host_id ? ` · host_id ${selectedDevice.host_id}` : ''}
                {' · '}job_id {selectedDevice.job_id}
              </p>
            ) : null}
          </section>

          {showingL3 && l3Loading ? (
            <div
              className={cn('rounded-lg border border-dashed p-3 text-xs', TEXT.subtitle)}
              data-testid="snapshot-l3-loading"
            >
              正在读取该 Job 的下发投影（L3）…
            </div>
          ) : null}

          {activeProjection && !projectionUnavailable ? (
            <ProjectionContextBar
              projection={activeProjection}
              layerLabel={showingL3 ? 'L3 按设备下发' : 'L2 运行设计'}
            />
          ) : null}

          <div className="overflow-hidden rounded-lg border" data-testid="snapshot-parameter-list">
            <ParameterProjectionList
              projection={projectionUnavailable ? null : activeProjection}
              unavailable={projectionUnavailable && !(showingL3 && l3Loading)}
            />
          </div>

          {activeProjection && !projectionUnavailable ? (
            <SafeDebugBlock projection={activeProjection} />
          ) : null}

          {hasSnapshotIdentity ? (
            <section className="space-y-1.5" data-testid="snapshot-step-identity">
              <div className={cn('text-xs font-medium', TEXT.heading)}>步骤身份（快照）</div>
              <p className={cn('text-[11px]', TEXT.subtitle)}>
                仅脚本身份与时序字段；参数值见上方安全投影，不展示原始 default_params / param_schema。
              </p>
              {steps.map((step) => (
                <StepIdentityCard
                  key={`${step.stage ?? ''}-${step.step_key ?? ''}-${step.sort_order ?? 0}`}
                  step={step}
                />
              ))}
            </section>
          ) : null}
        </div>

        <footer className={cn('border-t px-4 py-2 text-[10px]', TEXT.subtitle)}>
          L2 只读冻结快照；L3 绑定具体 Job/设备/host。后续 Script 升级或当前 Plan 行不回填历史值。
        </footer>
      </aside>
    </>
  );
}
