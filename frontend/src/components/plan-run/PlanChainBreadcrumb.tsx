import { Check, Loader2, PauseCircle, ChevronRight, AlertTriangle, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { ALERT_BANNER, CHAIN_CHIP, PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import type { ChainDispatchFailed, ChainNode, PlanChain } from '@/utils/api/types';

interface Props {
  chain: PlanChain | undefined;
  isLoading?: boolean;
  isError?: boolean;
  chainDispatchFailed?: ChainDispatchFailed | null;
  onNavigateRun?: (planRunId: number) => void;
}

function formatDuration(seconds: number | null | undefined): string {
  if (!seconds || !isFinite(seconds) || seconds <= 0) return '';
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  if (m === 0) return `${s}s`;
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h ${m % 60}m`;
}

function nodeChipClass(node: ChainNode): string {
  if (node.is_current) return CHAIN_CHIP.current;
  if (node.status === 'SUCCESS' || node.status === 'PARTIAL_SUCCESS') return CHAIN_CHIP.success;
  if (node.status === 'FAILED' || node.status === 'DEGRADED') return CHAIN_CHIP.failed;
  return CHAIN_CHIP.pending;
}

function NodeIcon({ node }: { node: ChainNode }) {
  if (node.is_current) return <Loader2 className="h-3 w-3 animate-spin text-warning" />;
  if (node.status === 'SUCCESS' || node.status === 'PARTIAL_SUCCESS') {
    return <Check className="h-3 w-3 text-success" />;
  }
  if (node.status === 'FAILED' || node.status === 'DEGRADED') {
    return <PauseCircle className="h-3 w-3 text-destructive" />;
  }
  return <PauseCircle className="h-3 w-3 text-muted-foreground/70" />;
}

function NodeChip({
  node,
  onNavigate,
}: {
  node: ChainNode;
  onNavigate?: (planRunId: number) => void;
}) {
  const passRate = node.pass_rate != null ? Math.round(node.pass_rate * 100) : null;
  const dur = formatDuration(node.duration_seconds);
  const clickable = !!node.plan_run_id && !node.is_current && !!onNavigate;

  const inner = (
    <span
      data-testid={`chain-node-${node.plan_id}`}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable && node.plan_run_id ? () => onNavigate(node.plan_run_id!) : undefined}
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-xs leading-none transition-colors',
        nodeChipClass(node),
        clickable && cn('cursor-pointer', CHAIN_CHIP.hover),
      )}
    >
      <NodeIcon node={node} />
      <span className={cn('font-mono text-[11px]', TEXT.subtitle)}>#{node.plan_id}</span>
      <span className="font-semibold">{node.plan_name || `Plan ${node.plan_id}`}</span>
      {node.is_current && (
        <span className={cn('rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wide', CHAIN_CHIP.currentTag)}>
          当前
        </span>
      )}
      {dur && (
        <span className={cn('text-[11px] font-normal', TEXT.subtitle)}>{dur}</span>
      )}
      {passRate != null && (
        <span className={cn('text-[11px] font-normal', TEXT.subtitle)}>
          {passRate}% · 阈值 {Math.round(node.failure_threshold * 100)}%
        </span>
      )}
      {node.is_blocked && node.block_reason && (
        <span className="text-[11px] font-medium text-destructive">
          ✗ 暂不触发
        </span>
      )}
    </span>
  );

  if (node.is_blocked && node.block_reason) {
    return (
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>{inner}</TooltipTrigger>
          <TooltipContent
            data-testid={`chain-node-${node.plan_id}-block-reason`}
            side="bottom"
            className="max-w-xs text-xs"
          >
            {node.block_reason}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }
  return inner;
}

function DispatchFailedBanner({ chainDispatchFailed }: { chainDispatchFailed: ChainDispatchFailed }) {
  return (
    <div
      data-testid="chain-dispatch-failed-banner"
      className={cn('flex items-start gap-2 rounded-xl px-3 py-2.5 text-xs', ALERT_BANNER.destructive)}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 space-y-1">
        <p className="font-semibold">下游 Plan 派发失败</p>
        <p className="break-words">
          本 PlanRun 已成功完成，但链上下一段 Plan 未能启动。
          {chainDispatchFailed.error ? ` 原因：${chainDispatchFailed.error}` : ''}
        </p>
        <p className="text-[11px] opacity-80">
          请检查设备可用性与脚本预检后，从 Plan 列表手动触发下游 Plan。
        </p>
      </div>
    </div>
  );
}

export default function PlanChainBreadcrumb({
  chain,
  isLoading = false,
  isError = false,
  chainDispatchFailed = null,
  onNavigateRun,
}: Props) {
  if (isLoading) {
    return (
      <div data-testid="plan-chain-loading" className={cn('flex items-center gap-2 px-3 py-2', PANEL.root)}>
        <span className={cn('text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>
          Plan 链
        </span>
        <Skeleton className="h-4 w-24" />
      </div>
    );
  }
  if (isError) {
    return (
      <div
        data-testid="plan-chain-error"
        className={cn('flex items-center gap-2 px-3 py-2', PANEL.root, ALERT_BANNER.destructive)}
      >
        <AlertCircle className="h-3.5 w-3.5 text-destructive/70" />
        <span className="text-xs text-destructive">Plan 链加载失败</span>
      </div>
    );
  }
  if (!chain || !chain.nodes.length) {
    if (chainDispatchFailed) {
      return <DispatchFailedBanner chainDispatchFailed={chainDispatchFailed} />;
    }
    return (
      <div data-testid="plan-chain-empty" className={cn('flex items-center gap-2 px-3 py-2', PANEL.root)}>
        <span className={cn('text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>
          Plan 链
        </span>
        <span className={cn('text-xs', TEXT.subtitle)}>无 chain 上下文</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {chainDispatchFailed && (
        <DispatchFailedBanner chainDispatchFailed={chainDispatchFailed} />
      )}
      <div className={cn('flex items-center gap-1.5 overflow-x-auto whitespace-nowrap px-3 py-2', PANEL.root)}>
        <span className={cn('mr-1 shrink-0 text-[11px] font-bold uppercase tracking-wider', TEXT.subtitle)}>
          Plan 链
        </span>
        {chain.nodes.map((node, idx) => (
          <span key={`${node.plan_id}-${node.chain_index}`} className="flex items-center gap-1.5">
            {idx > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-muted-foreground/50" />}
            <NodeChip node={node} onNavigate={onNavigateRun} />
          </span>
        ))}
      </div>
    </div>
  );
}
