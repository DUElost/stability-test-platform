import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import { Check, ClipboardCheck, X } from 'lucide-react';
import { api, toApiError } from '@/utils/api';
import type { AiPendingAction } from '@/utils/api/types';
import { aiAssistantKeys } from '@/utils/api/queryKeys';
import { useToast } from '@/hooks/useToast';
import { useConfirm } from '@/hooks/useConfirm';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { BORDER, SURFACE, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

/** 工具名 → 运维语义标签（与后端工具注册表对齐）。 */
const TOOL_LABELS: Record<string, string> = {
  run_quality_gate: '运行质量门禁',
  run_agent_tests: '运行 Agent 测试',
  run_gov_checks: '运行治理检查',
  scan_script_catalog: '脚本目录扫描',
  test_notification_channel: '通知通道测试发送',
  reload_agent_config: 'Agent 配置重载',
  dispatch_plan_run: '发起 Plan 执行',
};

/**
 * 管理员待审批队列（R13-F05 / #1217）。
 *
 * 普通用户的 T2 提案只挂在其个人会话里，管理员无法读他人会话，原先没有
 * 可用的审批入口。此页跨会话列出 proposed 动作（仅摘要，不拉取他人会话
 * 消息全文），提供批准/拒绝。
 */
export default function AssistantApprovalsPage() {
  const toast = useToast();
  const confirmDialog = useConfirm();
  const qc = useQueryClient();

  const pendingQ = useQuery({
    queryKey: aiAssistantKeys.pendingActions(),
    queryFn: api.aiAssistant.listPendingActions,
  });

  const decide = useMutation({
    mutationFn: ({ id, verb }: { id: number; verb: 'approve' | 'reject' }) =>
      verb === 'approve'
        ? api.aiAssistant.approveAction(id)
        : api.aiAssistant.rejectAction(id),
    onSuccess: (_data, variables) => {
      toast.success(variables.verb === 'approve' ? '已批准，开始执行' : '已拒绝该操作');
      qc.invalidateQueries({ queryKey: aiAssistantKeys.pendingActions() });
    },
    onError: (err) => toast.error(toApiError(err).message || '操作失败'),
  });

  const handleApprove = async (action: AiPendingAction) => {
    const label = TOOL_LABELS[action.tool_name] ?? action.tool_name;
    const ok = await confirmDialog({
      title: '批准执行',
      description: `确认批准「${label}」（发起人：${action.requested_by ?? '未知'}）？该动作将写入审计日志。`,
      confirmText: '批准',
    });
    if (ok) decide.mutate({ id: action.id, verb: 'approve' });
  };

  const handleReject = async (action: AiPendingAction) => {
    const ok = await confirmDialog({
      title: '拒绝操作',
      description: '确认拒绝该操作？助手将收到拒绝结果并继续对话。',
      variant: 'destructive',
      confirmText: '拒绝',
    });
    if (ok) decide.mutate({ id: action.id, verb: 'reject' });
  };

  if (pendingQ.isLoading) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手待审批" subtitle="跨会话查看并处理助手产生的待审批操作" />
        <PageSkeleton>
          <PageSkeleton.List count={2} />
        </PageSkeleton>
      </PageContainer>
    );
  }

  if (pendingQ.isError) {
    return (
      <PageContainer>
        <PageHeader title="AI 助手待审批" subtitle="跨会话查看并处理助手产生的待审批操作" />
        <ErrorState
          title="待审批列表加载失败"
          description={toApiError(pendingQ.error).message}
          onRetry={() => pendingQ.refetch()}
        />
      </PageContainer>
    );
  }

  const items = pendingQ.data ?? [];

  return (
    <PageContainer>
      <PageHeader
        title="AI 助手待审批"
        subtitle="跨会话查看并处理助手产生的待审批操作（不展示他人会话消息全文）"
      />
      {items.length === 0 ? (
        <EmptyState
          title="暂无待审批操作"
          description="当普通用户请求需要管理员确认的 T2 操作时，会出现在这里。"
          action={
            <Button variant="outline" asChild>
              <Link to="/assistant">返回 AI 助手</Link>
            </Button>
          }
        />
      ) : (
        <div className="grid gap-3">
          {items.map((action) => (
            <Card key={action.id}>
              <CardHeader className="flex-row items-center justify-between space-y-0 py-3">
                <CardTitle className="flex items-center gap-2 text-sm font-medium">
                  <ClipboardCheck className="h-4 w-4 text-primary" />
                  {TOOL_LABELS[action.tool_name] ?? action.tool_name}
                  <span className="font-mono text-xs text-muted-foreground">
                    {action.tool_name}
                  </span>
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 py-0">
                {action.preview_text && (
                  <pre
                    className={cn(
                      'max-h-48 overflow-auto rounded-md p-2 font-mono text-xs whitespace-pre-wrap',
                      SURFACE.subtle,
                      BORDER.default,
                    )}
                  >
                    {action.preview_text}
                  </pre>
                )}
                <p className={cn('text-xs', TEXT.caption)}>
                  发起人：{action.requested_by ?? '未知'}
                </p>
              </CardContent>
              <CardFooter className="flex-wrap gap-2 py-3">
                <Button
                  size="sm"
                  onClick={() => handleApprove(action)}
                  disabled={decide.isPending}
                >
                  <Check className="mr-1 h-3.5 w-3.5" />
                  批准执行
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => handleReject(action)}
                  disabled={decide.isPending}
                >
                  <X className="mr-1 h-3.5 w-3.5" />
                  拒绝
                </Button>
              </CardFooter>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
