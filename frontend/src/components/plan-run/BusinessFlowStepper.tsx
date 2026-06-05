import { CheckCircle2, Loader2, Clock, AlertCircle } from 'lucide-react';
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
  if (status === 'done')    return <CheckCircle2 className="h-5 w-5 text-green-500" />;
  if (status === 'running') return <Loader2 className="h-5 w-5 text-orange-500 animate-spin" />;
  if (status === 'failed')  return <AlertCircle className="h-5 w-5 text-red-500" />;
  return <Clock className="h-5 w-5 text-gray-300" />;
}

function StageNode({ stageKey, stage, isCurrent }: {
  stageKey: StageKey;
  stage: TimelineStage | undefined;
  isCurrent: boolean;
}) {
  const meta = STAGE_META[stageKey];
  const status = stageStatus(stage);

  const borderCls =
    status === 'running' ? 'border-orange-300 ring-1 ring-orange-200' :
    status === 'done'    ? 'border-green-200' :
    status === 'failed'  ? 'border-red-200' :
    isCurrent            ? 'border-blue-300' :
    'border-gray-200';

  const bgCls =
    status === 'running' ? 'bg-orange-50' :
    status === 'done'    ? 'bg-green-50'  :
    status === 'failed'  ? 'bg-red-50'    :
    'bg-white';

  const completedDevices = stage?.device_succeeded ?? null;
  const activeDevices    = stage?.patrol_active_devices ?? null;
  const patrolCycle      = stage?.patrol_cycle_index ?? null;

  return (
    <div
      className={`flex-1 rounded-lg border p-2.5 shadow-sm ${borderCls} ${bgCls}`}
      data-testid={`stage-node-${stageKey}`}
    >
      <div className="flex items-center gap-1.5 mb-1">
        <NodeIcon status={status} />
        <span className="text-xs font-semibold text-gray-800">{meta.label}</span>
      </div>
      <div className="text-[10px] text-gray-400">{meta.desc}</div>
      {(completedDevices != null || activeDevices != null) && (
        <div className="mt-1.5 text-[10px] text-gray-500">
          {activeDevices != null && <span className="text-orange-500 font-medium">{activeDevices} 活跃</span>}
          {activeDevices != null && completedDevices != null && ' · '}
          {completedDevices != null && <span>{completedDevices} 完成</span>}
        </div>
      )}
      {patrolCycle != null && (
        <div className="mt-1 text-[10px] font-mono text-orange-600">周期 #{patrolCycle}</div>
      )}
    </div>
  );
}

export default function BusinessFlowStepper({ timeline, isLoading, isError }: Props) {
  const stages = timeline?.stages ?? [];

  const findStage = (key: StageKey) => stages.find((s) => s.stage === key);

  const currentStage = stages.find(
    (s) => s.status === 'running'
  );

  return (
    <div className="space-y-2.5" data-testid="business-flow-stepper">
      <SectionHeader title="业务流进展" color="blue" />

      {isLoading && (
        <div className="h-16 flex items-center justify-center text-xs text-gray-400">加载中…</div>
      )}
      {isError && (
        <div className="h-16 flex items-center justify-center text-xs text-red-500">加载失败</div>
      )}

      {!isLoading && !isError && (
        <div className="flex gap-2">
          {STAGES.map((key, idx) => {
            const stage = findStage(key);
            const isCurrent = currentStage?.stage === key;
            return (
              <div key={key} className="flex items-center flex-1">
                <StageNode stageKey={key} stage={stage} isCurrent={isCurrent} />
                {idx < STAGES.length - 1 && (
                  <div className="mx-1 h-px w-4 shrink-0 bg-gray-200" />
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
