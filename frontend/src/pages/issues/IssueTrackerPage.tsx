import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { api, type JiraDraft, type PlanRun } from '@/utils/api';
import { AlertCircle, RefreshCw, FileText } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import JiraSubmitPanel from '@/components/issues/JiraSubmitPanel';
import { EmptyState } from '@/components/ui/empty-state';
import { INTERACTIVE, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';

interface RunWithDraft {
  run: PlanRun;
  draft: JiraDraft | null;
}

export default function IssueTrackerPage() {
  const navigate = useNavigate();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const { data: runsData, isLoading, isError, refetch } = useQuery({
    queryKey: ['runs-with-jira-drafts'],
    queryFn: async () => {
      const runs = await api.planRuns.list(0, 50);

      const runsWithDrafts: RunWithDraft[] = await Promise.all(
        runs.map(async (run: PlanRun) => {
          try {
            const draft = await api.runs.getCachedJiraDraft(run.id);
            return { run, draft };
          } catch {
            return { run, draft: null };
          }
        })
      );

      return runsWithDrafts.filter(r => r.draft !== null);
    },
  });

  const handleRefresh = async () => {
    setIsRefreshing(true);
    await refetch();
    setIsRefreshing(false);
  };

  return (
    <PageContainer width="list">
      <PageHeader
        title="问题追踪"
        subtitle="查看任务生成的 JIRA 草稿"
        action={
          <Button variant="outline" onClick={handleRefresh} disabled={isRefreshing} aria-label="刷新 JIRA 草稿列表">
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      />

      <div className="space-y-6">
        {isError && <InlineError message="JIRA 草稿列表加载失败，请检查后端服务连接。" />}

        {/* ADR-0025 §10: 去重→Jira 批量提单（上传 Result/Upload-List + 一键执行 + web 实时日志） */}
        <JiraSubmitPanel />

        <Card>
          <CardHeader>
            <CardTitle>JIRA 草稿</CardTitle>
          </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-20 w-full" />
              ))}
            </div>
          ) : runsData?.length === 0 ? (
            <EmptyState
              title="暂无 JIRA 草稿"
              description="完成任务执行后会自动生成 JIRA 草稿"
              icon={<FileText className="w-16 h-16" />}
            />
          ) : (
            <div className="space-y-4">
              {runsData?.map(({ run, draft }) => {
                const priority = draft?.priority || 'Minor';
                return (
                  <div
                    key={run.id}
                    className={cn('flex cursor-pointer items-start gap-4 rounded-lg border p-4 transition-colors', INTERACTIVE.hover)}
                    onClick={() => navigate(`/execution/plan-runs/${run.id}`)}
                  >
                    <AlertCircle className="mt-0.5 h-5 w-5 text-warning" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{draft?.summary}</span>
                        <StatusBadge kind="priority" status={priority} size="sm" />
                      </div>
                      <div className={cn('mt-1 text-sm', TEXT.subtitle)}>
                        {draft?.project_key}-{draft?.issue_type} | Plan #{run.plan_id ?? '-'} | Job #{run.id}
                      </div>
                      <div className={cn('mt-1 text-sm', TEXT.caption)}>
                        {draft?.description ? `${draft.description.substring(0, 100)}...` : '-'}
                      </div>
                      <div className={cn('mt-2 flex items-center gap-4 text-xs', TEXT.caption)}>
                        <span>标签: {draft?.labels?.join(', ') || '-'}</span>
                        <span>组件: {draft?.component || '-'}</span>
                      </div>
                    </div>
                    <div className={cn('text-right text-sm', TEXT.subtitle)}>
                      <div>{formatLocalDateTime(run.ended_at ?? null)}</div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>说明</CardTitle>
        </CardHeader>
        <CardContent className={cn('space-y-2 text-sm', TEXT.subtitle)}>
          <p>问题追踪页面展示任务执行后自动生成的 JIRA 草稿。</p>
          <ul className="list-disc list-inside space-y-1">
            <li>草稿基于任务执行结果自动生成</li>
            <li>点击可查看详情并跳转到对应任务</li>
            <li>后续版本将支持直接提交到 JIRA</li>
          </ul>
        </CardContent>
      </Card>
      </div>
    </PageContainer>
  );
}
