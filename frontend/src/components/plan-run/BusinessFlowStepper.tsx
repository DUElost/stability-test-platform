import { CheckCircle2, Loader2, Clock, AlertCircle } from 'lucide-react';
import { STEPPER_STAGE, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { PlanRunTimeline, TimelineStage } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  timeline?: PlanRunTimeline;
  isLoading?: boolean;
  isError?: boolean;
}

type StageKey = 'init' | 'patrol' | 'teardown';

const STAGE_META: Record<StageKey, { label: string; desc: string }> = {
  init:     { label: '初始化', desc: '设备准备 & 环境配置' },
  patrol:   { label: '巡检',   desc: '周期性状态轮询' },
  teardown: { label: '清理',   desc: '设备释放 & 日志归档' },
};

const STAGES: StageKey[] = ['init', 'patrol', 'teardown'];

function stageStatus(stage: TimelineStage | undefined): 'done' | 'running' | 'pending' | 'failed' {
  if (!stage) return 'pending';
  if (stage.status === 'completed') return 'done';
  if (stage.status === 'failed') return 'failed';
  if (stage.status === 'running') return 'running';
  return 'pending';
}

function NodeIcon({ status }: { status: ReturnType<typeof stageStatus> }) {
  if (status === 'done')    return <CheckCircle2 className={cn('h-5 w-5', STEPPER_STAGE.done.icon)} />;
  if (status === 'running') return <Loader2 className={cn('h-5 w-5 animate-spin', STEPPER_STAGE.running.icon)} />;
  if (status === 'failed')  return <AlertCircle className={cn('h-5 w-5', STEPPER_STAGE.failed.icon)} />;
  return <Clock className={cn('h-5 w-5', STEPPER_STAGE.pending.icon)} />;
}

function stageVisual(
  status: ReturnType<typeof stageStatus>,
  isCurrent: boolean,
): { border: string; bg: string } {
  if (status === 'running') return STEPPER_STAGE.running;
  if (status === 'done') return STEPPER_STAGE.done;
  if (status === 'failed') return STEPPER_STAGE.failed;
  if (isCurrent) return STEPPER_STAGE.current;
  return STEPPER_STAGE.pending;
}

function StageNode({ stageKey, stage, isCurrent }: {
  stageKey: StageKey;
  stage: TimelineStage | undefined;
  isCurrent: boolean;
}) {
  const meta = STAGE_META[stageKey];
  const status = stageStatus(stage);
  const visual = stageVisual(status, isCurrent);

  const completedDevices = stage?.device_succeeded ?? null;
  const activeDevices    = stage?.patrol_active_devices ?? null;
  const patrolCycle      = stage?.patrol_cycle_index ?? null;

  return (
    <div
      className={cn('flex-1 rounded-lg border p-2.5 shadow-sm', visual.border, visual.bg)}
      data-testid={`stage-node-${stageKey}`}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <NodeIcon status={status} />
        <span className={cn('text-xs font-semibold', TEXT.body)}>{meta.label}</span>
      </div>
      <div className={cn('text-[10px]', TEXT.subtitle)}>{meta.desc}</div>
      {(completedDevices != null || activeDevices != null) && (
        <div className={cn('mt-1.5 text-[10px]', TEXT.subtitle)}>
          {activeDevices != null && <span className="text-warning font-medium">{activeDevices} 活跃</span>}
          {activeDevices != null && completedDevices != null && ' · '}
          {completedDevices != null && <span>{completedDevices} 完成</span>}
        </div>
      )}
      {patrolCycle != null && (
        <div className="mt-1 text-[10px] font-mono text-warning">周期 #{patrolCycle}</div>
      )}
    </div>
  );
}

export default function BusinessFlowStepper({ timeline, isLoading, isError }: Props) {
  const stages = timeline?.stages ?? [];

  const currentStage = stages.find(
    (s) => s.status === 'running'
  );

  return (
    <div className="space-y-2.5" data-testid="business-flow-stepper">
      <SectionHeader title="业务流进展" color="blue" />

      {isLoading && (
        <div className={cn('h-16 flex items-center justify-center text-xs', TEXT.subtitle)}>加载中…</div>
      )}
      {isError && (
        <div className="h-16 flex items-center justify-center text-xs text-destructive">加载失败</div>
      )}

      {!isLoading && !isError && (
        <div className="flex gap-2">
          {STAGES.map((key, idx) => {
            const stage = stages.find((s) => s.stage === key);
            const isCurrent = currentStage?.stage === key;
            return (
              <div key={key} className="flex items-center flex-1">
                <StageNode stageKey={key} stage={stage} isCurrent={isCurrent} />
                {idx < STAGES.length - 1 && (
                  <div className="mx-1 h-px w-4 shrink-0 bg-border" />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
