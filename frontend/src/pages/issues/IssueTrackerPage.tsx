import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ClickableRow } from '@/components/ui/clickable-row';
import { api, type JiraDraft, type PlanRun } from '@/utils/api';
import { RefreshCw } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import JiraSubmitPanel from '@/components/issues/JiraSubmitPanel';
import JiraRunHistory from '@/components/issues/JiraRunHistory';
import { InlineEmpty } from '@/components/ui/empty-state';
import { StateTabs } from '@/components/ui/state-tabs';
import { LAYOUT, TEXT } from '@/design-system';
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
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="问题追踪"
        subtitle="上传去重报告进行批量 Jira 提单，或查看任务自动生成的草稿"
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <StateTabs
          items={tabs}
          activeKey={tab}
          onChange={(key) => setTab(key as TabKey)}
          testId="issue-tracker-tabs"
          ariaLabel="提单视图切换"
          className="min-w-0 flex-1"
        />
        {tab === 'drafts' && (
          <Button
            variant="outline"
            size="sm"
            onClick={() => void handleRefresh()}
            disabled={isRefreshing}
            aria-label="刷新 JIRA 草稿列表"
            className="shrink-0"
          >
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        )}
      </div>

      {tab === 'form' && (
        <div className="rounded-lg border border-border p-4">
          <JiraSubmitPanel />
        </div>
      )}

      {tab === 'drafts' && (
        <div className="space-y-3">
          {isError && (
            <InlineError
              message="JIRA 草稿列表加载失败，请检查后端服务连接。"
              onRetry={() => void refetch()}
            />
          )}

          {isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : runsData?.length === 0 ? (
            <InlineEmpty bordered>暂无 JIRA 草稿 · 完成任务执行后会自动生成</InlineEmpty>
          ) : (
            <div className="overflow-x-auto rounded-lg border border-border">
              <Table className="min-w-[720px]">
                <TableHeader>
                  <TableRow className="border-b text-left text-xs text-muted-foreground hover:bg-transparent">
                    <TableHead className="h-9 px-3">摘要</TableHead>
                    <TableHead className="h-9 px-3">优先级</TableHead>
                    <TableHead className="h-9 px-3">项目</TableHead>
                    <TableHead className="h-9 px-3">PlanRun</TableHead>
                    <TableHead className="h-9 px-3">结束时间</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {runsData?.map(({ run, draft }) => {
                    const priority = draft?.priority || 'Minor';
                    return (
                      <ClickableRow
                        key={run.id}
                        className="border-b transition-colors last:border-0 hover:bg-muted/50"
                        onClick={() => navigate(`/execution/plan-runs/${run.id}`)}
                        role="button"
                      >
                        <TableCell className="max-w-[280px] px-3 py-2.5">
                          <span className={cn('block truncate text-sm', TEXT.heading)}>
                            {draft?.summary || '—'}
                          </span>
                          <span className={cn('mt-0.5 block truncate text-xs', TEXT.caption)}>
                            {draft?.issue_type || '—'}
                            {draft?.component ? ` · ${draft.component}` : ''}
                          </span>
                        </TableCell>
                        <TableCell className="px-3 py-2.5">
                          <StatusBadge kind="priority" status={priority} size="sm" />
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 font-mono text-xs', TEXT.caption)}>
                          {draft?.project_key || '—'}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 font-mono text-xs', TEXT.subtitle)}>
                          #{run.id}
                        </TableCell>
                        <TableCell className={cn('px-3 py-2.5 text-xs whitespace-nowrap', TEXT.caption)}>
                          {formatLocalDateTime(run.ended_at ?? null)}
                        </TableCell>
                      </ClickableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}

          <p className={cn('text-xs', TEXT.caption)}>
            批量提单上传去重报告；草稿来自任务执行后自动生成，点击行跳转对应 PlanRun。
          </p>
        </div>
      )}

      {tab === 'history' && <JiraRunHistory />}
    </PageContainer>
  );
}
