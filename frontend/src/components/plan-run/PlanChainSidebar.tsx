import { CheckCircle2, Loader2, Clock, PauseCircle, AlertCircle, AlertTriangle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { ALERT_BANNER, CHAIN_DOT, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { ChainDispatchFailed, ChainNode, PlanChain } from '@/utils/api/types';
import SectionHeader from './SectionHeader';

interface Props {
  chain?: PlanChain;
  isLoading?: boolean;
  isError?: boolean;
  chainDispatchFailed?: ChainDispatchFailed | null;
  onNavigateRun?: (planRunId: number) => void;
}

function NodeDot({ node }: { node: ChainNode }) {
  const isPending = node.status === 'pending' || !node.plan_run_id;
  const isDone = node.status === 'SUCCESS' || node.status === 'PARTIAL_SUCCESS';
  const isFailed = node.status === 'FAILED' || node.status === 'DEGRADED';
  const isRunning = !isPending && !isDone && !isFailed;

  if (isPending) {
    return (
      <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2', CHAIN_DOT.pending)}>
        <Clock className="h-2.5 w-2.5 text-muted-foreground/70" />
      </div>
    );
  }
  if (isRunning) {
    return (
      <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2', CHAIN_DOT.running)}>
        <Loader2 className="h-2.5 w-2.5 text-warning animate-spin" />
      </div>
    );
  }
  if (isDone) {
    return (
      <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2', CHAIN_DOT.done)}>
        <CheckCircle2 className="h-2.5 w-2.5 text-success" />
      </div>
    );
  }
  if (isFailed) {
    return (
      <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2', CHAIN_DOT.failed)}>
        <AlertCircle className="h-2.5 w-2.5 text-destructive" />
      </div>
    );
  }
  return (
    <div className={cn('flex h-5 w-5 shrink-0 items-center justify-center rounded-full border-2', CHAIN_DOT.pending)}>
      <PauseCircle className="h-2.5 w-2.5 text-muted-foreground/70" />
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
  const isPending = node.status === 'pending' || !node.plan_run_id;
  const isNavigable = !!node.plan_run_id && !node.is_current;

  return (
    <div
      data-testid={`chain-node-${node.plan_id}`}
      className={cn('flex items-start gap-2', isPending && 'opacity-45')}
    >
      <div className="flex flex-col items-center">
        <NodeDot node={node} />
        {!isLast && (
          <div className={cn('my-1 w-px flex-1', CHAIN_DOT.connector)} style={{ minHeight: 16 }} />
        )}
      </div>

      <div className="min-w-0 pb-3 pt-0.5">
        {isPending ? (
          <span className={cn('text-[11px] font-semibold', TEXT.subtitle)}>
            {node.plan_name ?? `Plan #${node.plan_id}`}
          </span>
        ) : (
          <button
            type="button"
            disabled={!isNavigable}
            onClick={() => node.plan_run_id && onNavigate?.(node.plan_run_id)}
            className={cn(
              'text-[11px] font-semibold leading-none',
              node.is_current
                ? 'cursor-default text-warning'
                : isNavigable
                  ? 'cursor-pointer text-primary hover:underline'
                  : cn('cursor-default', TEXT.subtitle),
            )}
          >
            PlanRun #{node.plan_run_id}
          </button>
        )}

        {node.is_current && (
          <span className={cn('ml-1 rounded-full px-1.5 py-0.5 text-[10px] font-semibold', STATUS_CHIP.warning)}>
            当前
          </span>
        )}

        {node.is_blocked && node.block_reason && (
          <span className={cn('ml-1 rounded-full px-1.5 py-0.5 text-[10px]', STATUS_CHIP.muted)}>
            暂不触发
          </span>
        )}

        <div className={cn('mt-0.5 truncate text-[10px]', TEXT.subtitle)}>
          {node.plan_name ?? `Plan #${node.plan_id}`}
        </div>

        {!isPending && node.pass_rate != null && (
          <div className={cn('mt-0.5 text-[10px] font-medium', TEXT.subtitle)}>
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

      {chainDispatchFailed && (
        <div
          data-testid="chain-dispatch-failed-banner"
          className={cn('flex items-start gap-2 rounded-lg px-2.5 py-2 text-xs', ALERT_BANNER.destructive)}
        >
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          <div className="min-w-0 space-y-0.5">
            <p className="font-semibold">下游 Plan 派发失败</p>
            {chainDispatchFailed.error && (
              <p className="break-words text-[10px] opacity-90">{chainDispatchFailed.error}</p>
            )}
          </div>
        </div>
      )}

      <div className={cn('p-3', PANEL.root)}>
        {isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-3/4" />
            <Skeleton className="h-4 w-1/2" />
          </div>
        )}

        {isError && (
          <div className="text-[11px] text-destructive">加载失败</div>
        )}

        {!isLoading && !isError && nodes.length === 0 && !chainDispatchFailed && (
          <div className={cn('text-[11px]', TEXT.subtitle)}>暂无执行链数据</div>
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
