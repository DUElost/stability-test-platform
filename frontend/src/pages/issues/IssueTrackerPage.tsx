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
import JiraRunHistory from '@/components/issues/JiraRunHistory';
import { EmptyState } from '@/components/ui/empty-state';
import { INTERACTIVE, TEXT, tabLinkClass } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';

interface RunWithDraft {
  run: PlanRun;
  draft: JiraDraft | null;
}

type TabKey = 'form' | 'drafts' | 'history';

export default function IssueTrackerPage() {
  const navigate = useNavigate();
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [tab, setTab] = useState<TabKey>('form');

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

  const tabs: { key: TabKey; label: string; testId: string }[] = [
    { key: 'form', label: '批量提单', testId: 'issue-tracker-tab-form' },
    { key: 'drafts', label: '草稿列表', testId: 'issue-tracker-tab-drafts' },
    { key: 'history', label: '历史记录', testId: 'issue-tracker-tab-history' },
  ];

  return (
    <PageContainer width="list">
      <PageHeader
        title="问题追踪"
        subtitle="上传去重报告进行批量 Jira 提单，或查看任务自动生成的草稿"
        action={
          <Button
            variant="outline"
            onClick={handleRefresh}
            disabled={isRefreshing}
            aria-label="刷新 JIRA 草稿列表"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      />

      <div className="space-y-6">
        <div data-testid="issue-tracker-tabs" className="flex items-center gap-x-1 border-b border-border">
          {tabs.map(t => (
            <button
              key={t.key}
              type="button"
              data-testid={t.testId}
              onClick={() => setTab(t.key)}
              className={tabLinkClass(tab === t.key)}
            >
              {t.label}
            </button>
          ))}
        </div>

        {tab === 'form' && (
          <Card>
            <CardHeader>
              <CardTitle>批量提单（去重报告）</CardTitle>
            </CardHeader>
            <CardContent>
              <JiraSubmitPanel />
            </CardContent>
          </Card>
        )}

        {tab === 'drafts' && (
          <>
            {isError && <InlineError message="JIRA 草稿列表加载失败，请检查后端服务连接。" />}

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
                <p>「批量提单」页签上传去重报告，经厂商脚本一键执行（生成上传模板 → 建单）。</p>
                <p>「草稿列表」页签展示任务执行后自动生成的 JIRA 草稿，点击可跳转到对应任务。</p>
                <p>「历史记录」页签展示历次批量提单执行结果与日志 replay。</p>
              </CardContent>
            </Card>
          </>
        )}

        {tab === 'history' && (
          <JiraRunHistory />
        )}
      </div>
    </PageContainer>
  );
}