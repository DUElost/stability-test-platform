import { useEffect, useRef, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Ban, ChevronDown, ChevronUp, ScrollText, Wrench, X } from 'lucide-react';
import { api, toApiError } from '@/utils/api';
import type { AiActionStatus } from '@/utils/api/types';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { LogPanel } from './LogPanel';

/** 工具名 → 运维语义标签（与后端工具注册表对齐）。 */
const TOOL_LABELS: Record<string, string> = {
  run_quality_gate: '运行质量门禁',
  run_agent_tests: '运行 Agent 测试',
  run_gov_checks: '运行治理检查',
  scan_script_catalog: '脚本目录扫描',
  test_notification_channel: '通知通道测试发送',
  reload_agent_config: 'Agent 配置重载',
};

const STATUS_BADGE: Record<AiActionStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' }> = {
  proposed: { label: '待审批', variant: 'secondary' },
  approved: { label: '已批准', variant: 'default' },
  rejected: { label: '已拒绝', variant: 'destructive' },
  expired: { label: '已过期', variant: 'outline' },
  running: { label: '执行中', variant: 'default' },
  succeeded: { label: '已成功', variant: 'success' },
  failed: { label: '执行失败', variant: 'destructive' },
  cancelled: { label: '已取消', variant: 'outline' },
};

const ACTIVE_STATUSES: ReadonlySet<string> = new Set(['approved', 'running']);
/** 终态：到达时立刻刷新对话流（续轮汇报紧随其后落库）。 */
const TERMINAL_STATUSES: ReadonlySet<string> = new Set([
  'succeeded',
  'failed',
  'cancelled',
  'rejected',
  'expired',
]);
/** 有日志可看的终态（含运行中）。 */
const LOG_STATUSES: ReadonlySet<string> = new Set(['approved', 'running', 'succeeded', 'failed', 'cancelled']);

interface ActionCardProps {
  actionId: number;
  className?: string;
}

/**
 * T2 运维动作操作卡（ADR-0031 D1/D6）：提案 → admin 审批 → RunConsole 执行 → 结果回填。
 * 自带 action 查询（按 meta.proposed_action_id 内嵌在消息流中），running 时 2s 轮询。
 */
export function ActionCard({ actionId, className }: ActionCardProps) {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const qc = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const [logOpen, setLogOpen] = useState(false);

  const actionQ = useQuery({
    queryKey: aiAssistantKeys.action(actionId),
    queryFn: () => api.aiAssistant.getAction(actionId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ACTIVE_STATUSES.has(status) ? 2000 : false;
    },
  });

  const invalidate = (sessionId: number) => {
    qc.invalidateQueries({ queryKey: aiAssistantKeys.action(actionId) });
    qc.invalidateQueries({ queryKey: aiAssistantKeys.messages(sessionId) });
    qc.invalidateQueries({ queryKey: aiAssistantKeys.sessions() });
  };

  // 动作到达终态时立刻刷一次对话流。续轮汇报本身由后端 pending 占位驱动
  // 2s 轮询，这里只是让「结果落卡」与「助手汇报」尽量同时到位。
  const lastStatusRef = useRef<string | null>(null);
  const actionStatus = actionQ.data?.status;
  const actionSessionId = actionQ.data?.session_id;
  useEffect(() => {
    if (!actionStatus || actionSessionId == null) return;
    const prev = lastStatusRef.current;
    lastStatusRef.current = actionStatus;
    if (prev && prev !== actionStatus && TERMINAL_STATUSES.has(actionStatus)) {
      qc.invalidateQueries({ queryKey: aiAssistantKeys.messages(actionSessionId) });
      qc.invalidateQueries({ queryKey: aiAssistantKeys.sessions() });
    }
  }, [actionStatus, actionSessionId, qc]);

  const decide = useMutation({
    mutationFn: async ({ verb }: { verb: 'approve' | 'reject' }) => {
      const action = actionQ.data;
      if (!action) throw new Error('操作卡尚未加载');
      return verb === 'approve'
        ? api.aiAssistant.approveAction(action.id)
        : api.aiAssistant.rejectAction(action.id);
    },
    onSuccess: (_data, variables) => {
      toast.success(variables.verb === 'approve' ? '已批准，开始执行' : '已拒绝该操作');
      if (actionQ.data) invalidate(actionQ.data.session_id);
      if (variables.verb === 'approve') {
        setLogOpen(true);
      }
    },
    onError: (err) => toast.error(toApiError(err).message || '操作失败'),
  });

  const cancel = useMutation({
    mutationFn: async () => {
      const action = actionQ.data;
      if (!action) throw new Error('操作卡尚未加载');
      return api.aiAssistant.cancelAction(action.id);
    },
    onSuccess: () => {
      toast.success('已发送取消请求');
      if (actionQ.data) invalidate(actionQ.data.session_id);
    },
    onError: (err) => toast.error(toApiError(err).message || '取消失败'),
  });

  const handleApprove = async () => {
    const label = actionQ.data ? (TOOL_LABELS[actionQ.data.tool_name] ?? actionQ.data.tool_name) : '该操作';
    const ok = await confirmDialog({
      title: '批准执行',
      description: `确认批准「${label}」在控制面执行？该动作将写入审计日志。`,
      confirmText: '批准',
    });
    if (ok) decide.mutate({ verb: 'approve' });
  };

  const handleReject = async () => {
    const ok = await confirmDialog({
      title: '拒绝操作',
      description: '确认拒绝该操作？助手将收到拒绝结果并继续对话。',
      variant: 'destructive',
      confirmText: '拒绝',
    });
    if (ok) decide.mutate({ verb: 'reject' });
  };

  if (actionQ.isLoading) {
    return (
      <Card className={cn('animate-pulse', className)}>
        <CardContent className="py-4 text-sm text-muted-foreground">加载操作卡…</CardContent>
      </Card>
    );
  }
  if (actionQ.isError || !actionQ.data) {
    return (
      <Card className={cn('border-destructive/40', className)}>
        <CardContent className="py-4 text-sm text-destructive">操作卡加载失败</CardContent>
      </Card>
    );
  }

  const action = actionQ.data;
  const badge = STATUS_BADGE[action.status];
  const label = TOOL_LABELS[action.tool_name] ?? action.tool_name;
  const isActive = ACTIVE_STATUSES.has(action.status);
  const canSeeLog = LOG_STATUSES.has(action.status);

  return (
    <Card className={cn('w-full max-w-[90%]', className)}>
      <CardHeader className="flex-row items-center justify-between space-y-0 py-3">
        <CardTitle className="flex items-center gap-2 text-sm font-medium">
          <Wrench className="h-4 w-4 text-primary" />
          {label}
          <span className="font-mono text-xs text-muted-foreground">{action.tool_name}</span>
        </CardTitle>
        <Badge variant={badge.variant}>{badge.label}</Badge>
      </CardHeader>
      <CardContent className="space-y-2 py-0">
        <details>
          <summary className={cn('cursor-pointer text-xs', TEXT.subtitle)}>参数</summary>
          <pre
            className={cn(
              'mt-1 max-h-40 overflow-auto rounded-md p-2 font-mono text-xs',
              SURFACE.subtle,
            )}
          >
            {JSON.stringify(action.params, null, 2)}
          </pre>
        </details>
        {action.status === 'proposed' && !isAdmin && (
          <p className={cn('text-xs', TEXT.caption)}>等待管理员在平台上审批后执行。</p>
        )}
        {action.result_summary && (
          <p className={cn('text-xs', TEXT.body)}>
            <span className={TEXT.caption}>结果：</span>
            {action.result_summary}
          </p>
        )}
        {canSeeLog && (logOpen || isActive) && (
          <LogPanel actionId={action.id} active={isActive} />
        )}
      </CardContent>
      <CardFooter className="flex-wrap gap-2 py-3">
        {action.status === 'proposed' && isAdmin && (
          <>
            <Button size="sm" onClick={handleApprove} disabled={decide.isPending}>
              批准执行
            </Button>
            <Button size="sm" variant="outline" onClick={handleReject} disabled={decide.isPending}>
              <X className="mr-1 h-3.5 w-3.5" />
              拒绝
            </Button>
          </>
        )}
        {canSeeLog && !isActive && (
          <Button
            size="sm"
            variant="ghost"
            onClick={() => setLogOpen((v) => !v)}
            className={cn('text-xs', TEXT.caption)}
          >
            <ScrollText className="mr-1 h-3.5 w-3.5" />
            {logOpen ? '收起日志' : '查看日志'}
            {logOpen ? <ChevronUp className="ml-1 h-3 w-3" /> : <ChevronDown className="ml-1 h-3 w-3" />}
          </Button>
        )}
        {isActive && isAdmin && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => cancel.mutate()}
            disabled={cancel.isPending}
          >
            <Ban className="mr-1 h-3.5 w-3.5" />
            取消
          </Button>
        )}
        <span className={cn('ml-auto text-xs', TEXT.caption)}>
          发起人：{action.requested_by}
          {action.decided_by ? ` · 审批：${action.decided_by}` : ''}
        </span>
      </CardFooter>
    </Card>
  );
}

export default ActionCard;
