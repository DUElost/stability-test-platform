import { CheckCircle2, Loader2, Clock, PauseCircle, AlertCircle, AlertTriangle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import type { ChainDispatchFailed, ChainNode, PlanChain } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  chain?: PlanChain;
  isLoading?: boolean;
  isError?: boolean;
  chainDispatchFailed?: ChainDispatchFailed | null;
  onNavigateRun?: (planRunId: number) => void;
}

// 圆形状态标记
function NodeDot({ node }: { node: ChainNode }) {
  const isPending = node.status === 'pending' || !node.plan_run_id;
  const isDone    = node.status === 'SUCCESS' || node.status === 'PARTIAL_SUCCESS';
  const isFailed  = node.status === 'FAILED'  || node.status === 'DEGRADED';
  const isRunning = !isPending && !isDone && !isFailed;

  if (isPending) {
    return (
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-dashed border-gray-300 bg-white">
        <Clock className="h-2.5 w-2.5 text-gray-400" />
      </div>
    );
  }
  if (isRunning) {
    return (
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-orange-400 bg-orange-50">
        <Loader2 className="h-2.5 w-2.5 text-orange-500 animate-spin" />
      </div>
    );
  }
  if (isDone) {
    return (
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-green-400 bg-green-50">
        <CheckCircle2 className="h-2.5 w-2.5 text-green-600" />
      </div>
    );
  }
  if (isFailed) {
    return (
      <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-red-400 bg-red-50">
        <AlertCircle className="h-2.5 w-2.5 text-red-600" />
      </div>
    );
  }
  return (
    <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2 border-gray-300 bg-white">
      <PauseCircle className="h-2.5 w-2.5 text-gray-400" />
    </div>
  );
}

function ChainNodeRow({
  node,
  isLast,
  onNavigate,
}: {
  node: ChainNode;
  isLast: boolean;
  onNavigate?: (id: number) => void;
}) {
  const isPending   = node.status === 'pending' || !node.plan_run_id;
  const isNavigable = !!node.plan_run_id && !node.is_current;

  return (
    <div
      data-testid={`chain-node-${node.plan_id}`}
      className={`flex items-start gap-2 ${isPending ? 'opacity-45' : ''}`}
    >
      {/* 圆点 + 连接线 */}
      <div className="flex flex-col items-center">
        <NodeDot node={node} />
        {!isLast && (
          <div className="my-1 w-px flex-1 bg-gray-200" style={{ minHeight: 16 }} />
        )}
      </div>

      {/* 文字信息 */}
      <div className="min-w-0 pb-3 pt-0.5">
        {isPending ? (
          <span className="text-[11px] font-semibold text-gray-500">
            {node.plan_name ?? `Plan #${node.plan_id}`}
          </span>
        ) : (
          <button
            type="button"
            disabled={!isNavigable}
            onClick={() => node.plan_run_id && onNavigate?.(node.plan_run_id)}
            className={`text-[11px] font-semibold leading-none ${
              node.is_current
                ? 'cursor-default text-orange-600'
                : isNavigable
                  ? 'cursor-pointer text-blue-500 hover:underline'
                  : 'cursor-default text-gray-600'
            }`}
          >
            PlanRun #{node.plan_run_id}
          </button>
        )}

        {node.is_current && (
          <span className="ml-1 rounded-full bg-orange-100 px-1.5 py-0.5 text-[10px] font-semibold text-orange-600">
            当前
          </span>
        )}

        {node.is_blocked && node.block_reason && (
          <span className="ml-1 rounded-full bg-gray-100 px-1.5 py-0.5 text-[10px] text-gray-500">
            暂不触发
          </span>
        )}

        <div className="mt-0.5 truncate text-[10px] text-gray-400">
          {node.plan_name ?? `Plan #${node.plan_id}`}
        </div>

        {!isPending && node.pass_rate != null && (
          <div className="mt-0.5 text-[10px] font-medium text-gray-500">
            通过率 {Math.round(node.pass_rate * 100)}%
          </div>
        )}
      </div>
    </div>
  );
}

export default function PlanChainSidebar({
  chain,
  isLoading,
  isError,
  chainDispatchFailed,
  onNavigateRun,
}: Props) {
  const nodes = chain?.nodes ?? [];

  return (
    <div className="space-y-2.5">
      <SectionHeader title="执行链" color="gray" />

      {/* 派发失败 banner */}
      {chainDispatchFailed && (
        <div
          data-testid="chain-dispatch-failed-banner"
          className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-2.5 py-2 text-xs text-red-800"
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-600" />
          <div className="min-w-0 space-y-0.5">
            <p className="font-semibold">下游 Plan 派发失败</p>
            {chainDispatchFailed.error && (
              <p className="break-words text-[10px] text-red-700/90">{chainDispatchFailed.error}</p>
            )}
          </div>
        </div>
      )}

      <div className="rounded-xl border border-gray-100 bg-white p-3">
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}

        {isError && (
          <div className="text-[11px] text-red-500">加载失败</div>
        )}

        {!isLoading && !isError && nodes.length === 0 && !chainDispatchFailed && (
          <div className="text-[11px] text-gray-400">暂无执行链数据</div>
        )}

        {!isLoading && !isError && nodes.length > 0 && (
          <div>
            {nodes.map((node, idx) => (
              <ChainNodeRow
                key={node.plan_id}
                node={node}
                isLast={idx === nodes.length - 1}
                onNavigate={onNavigateRun}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
