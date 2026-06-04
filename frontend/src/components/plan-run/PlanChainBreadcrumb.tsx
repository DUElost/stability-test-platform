import { Check, Loader2, PauseCircle, ChevronRight, AlertTriangle, AlertCircle } from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
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

function NodeChip({
  node,
  onNavigate,
}: {
  node: ChainNode;
  onNavigate?: (planRunId: number) => void;
}) {
  const passRate = node.pass_rate != null ? Math.round(node.pass_rate * 100) : null;
  const dur = formatDuration(node.duration_seconds);

  // Style by status / state
  let cls = 'border-gray-300 bg-white text-gray-500';
  let Icon: React.ElementType = PauseCircle;
  let iconCls = 'text-gray-400';
  let tagText: string | null = null;
  let tagCls = '';

  if (node.is_current) {
    cls = 'border-orange-300 bg-orange-50 text-orange-900 ring-2 ring-orange-200';
    Icon = Loader2;
    iconCls = 'text-orange-600 animate-spin';
    tagText = '当前';
    tagCls = 'bg-orange-200 text-orange-900';
  } else if (node.status === 'SUCCESS' || node.status === 'PARTIAL_SUCCESS') {
    cls = 'border-green-300 bg-white text-green-800';
    Icon = Check;
    iconCls = 'text-green-600';
  } else if (node.status === 'pending') {
    cls = 'border-gray-300 bg-white text-gray-500';
    Icon = PauseCircle;
    iconCls = 'text-gray-400';
  } else if (node.status === 'FAILED' || node.status === 'DEGRADED') {
    cls = 'border-red-300 bg-white text-red-700';
    Icon = PauseCircle;
    iconCls = 'text-red-500';
  }

  const clickable = !!node.plan_run_id && !node.is_current && !!onNavigate;

  const inner = (
    <span
      data-testid={`chain-node-${node.plan_id}`}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable && node.plan_run_id ? () => onNavigate(node.plan_run_id!) : undefined}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-3 py-1 text-xs leading-none transition-colors ${cls} ${
        clickable ? 'cursor-pointer hover:bg-gray-50' : ''
      }`}
    >
      <Icon className={`h-3 w-3 ${iconCls}`} />
      <span className="font-mono text-[11px] text-gray-400">#{node.plan_id}</span>
      <span className="font-semibold">{node.plan_name || `Plan ${node.plan_id}`}</span>
      {tagText && (
        <span
          className={`rounded px-1.5 py-px text-[10px] font-bold uppercase tracking-wide ${tagCls}`}
        >
          {tagText}
        </span>
      )}
      {dur && (
        <span className="text-[11px] font-normal text-gray-400">{dur}</span>
      )}
      {passRate != null && (
        <span className="text-[11px] font-normal text-gray-500">
          {passRate}% · 阈值 {Math.round(node.failure_threshold * 100)}%
        </span>
      )}
      {node.is_blocked && node.block_reason && (
        <span className="text-[11px] font-medium text-red-600">
          ✗ 暂不触发
        </span>
      )}
    </span>
  );

  // Wrap with Tooltip if there's hover-only context
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

export default function PlanChainBreadcrumb({
  chain,
  isLoading = false,
  isError = false,
  chainDispatchFailed = null,
  onNavigateRun,
}: Props) {
  if (isLoading) {
    return (
      <div
        data-testid="plan-chain-loading"
        className="flex items-center gap-2 rounded-xl border bg-white px-3 py-2"
      >
        <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400">
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
        className="flex items-center gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2"
      >
        <AlertCircle className="h-3.5 w-3.5 text-red-400" />
        <span className="text-xs text-red-600">Plan 链加载失败</span>
      </div>
    );
  }
  if (!chain || !chain.nodes.length) {
    if (chainDispatchFailed) {
      return (
        <div
          data-testid="chain-dispatch-failed-banner"
          className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-800"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
          <div className="min-w-0 space-y-1">
            <p className="font-semibold">下游 Plan 派发失败</p>
            <p className="break-words text-red-700/90">
              本 PlanRun 已成功完成，但链上下一段 Plan 未能启动。
              {chainDispatchFailed.error ? ` 原因：${chainDispatchFailed.error}` : ''}
            </p>
            <p className="text-xs text-red-600/80">
              请检查设备可用性与脚本预检后，从 Plan 列表手动触发下游 Plan。
            </p>
          </div>
        </div>
      );
    }
    return (
      <div
        data-testid="plan-chain-empty"
        className="flex items-center gap-2 rounded-xl border bg-white px-3 py-2"
      >
        <span className="text-[11px] font-bold uppercase tracking-wider text-gray-400">
          Plan 链
        </span>
        <span className="text-xs text-gray-400">无 chain 上下文</span>
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {chainDispatchFailed && (
        <div
          data-testid="chain-dispatch-failed-banner"
          className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-800"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-red-600" />
          <div className="min-w-0 space-y-1">
            <p className="font-semibold">下游 Plan 派发失败</p>
            <p className="break-words text-red-700/90">
              本 PlanRun 已成功完成，但链上下一段 Plan 未能启动。
              {chainDispatchFailed.error ? ` 原因：${chainDispatchFailed.error}` : ''}
            </p>
            <p className="text-xs text-red-600/80">
              请检查设备可用性与脚本预检后，从 Plan 列表手动触发下游 Plan。
            </p>
          </div>
        </div>
      )}
      <div className="flex items-center gap-1.5 overflow-x-auto whitespace-nowrap rounded-xl border bg-white px-3 py-2 shadow-sm">
        <span className="mr-1 shrink-0 text-[11px] font-bold uppercase tracking-wider text-gray-400">
          Plan 链
        </span>
        {chain.nodes.map((node, idx) => (
          <span key={`${node.plan_id}-${node.chain_index}`} className="flex items-center gap-1.5">
            {idx > 0 && <ChevronRight className="h-3.5 w-3.5 shrink-0 text-gray-300" />}
            <NodeChip node={node} onNavigate={onNavigateRun} />
          </span>
        ))}
      </div>
    </div>
  );
}
