import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { StatusBadge } from '@/components/ui/status-badge';
import { api, type JiraDraft, type PlanRun } from '@/utils/api';
import apiClient from '@/utils/api/client';
import { AlertCircle, RefreshCw, FileText } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import JiraSubmitPanel from '@/components/issues/JiraSubmitPanel';

interface RunWithDraft {
  run: PlanRun;
  draft: JiraDraft | null;
}

function formatTime(iso: string | null): string {
  if (!iso) return '-';
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export default function IssueTrackerPage() {
  const navigate = useNavigate();
  const [isRefreshing, setIsRefreshing] = useState(false);

  const { data: runsData, isLoading, refetch } = useQuery({
    queryKey: ['runs-with-jira-drafts'],
    queryFn: async () => {
      const runs = await api.planRuns.list(0, 50);

      const runsWithDrafts: RunWithDraft[] = await Promise.all(
        runs.map(async (run: PlanRun) => {
          try {
            const draftResp = await apiClient.get(`/runs/${run.id}/jira-draft/cached`);
            return { run, draft: draftResp.data };
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
    <PageContainer>
      <PageHeader
        title="问题追踪"
        subtitle="查看任务生成的 JIRA 草稿"
        action={
          <Button variant="outline" onClick={handleRefresh} disabled={isRefreshing}>
            <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
            刷新
          </Button>
        }
      />

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
            <div className="text-center py-12 text-gray-500">
              <FileText className="w-12 h-12 mx-auto mb-4 text-gray-300" />
              <p>暂无 JIRA 草稿</p>
              <p className="text-sm mt-2">完成任务执行后会自动生成 JIRA 草稿</p>
            </div>
          ) : (
            <div className="space-y-4">
              {runsData?.map(({ run, draft }) => {
                const priority = draft?.priority || 'Minor';
                return (
                  <div
                    key={run.id}
                    className="flex items-start gap-4 p-4 rounded-lg border hover:bg-gray-50 cursor-pointer transition-colors"
                    onClick={() => navigate(`/execution/plan-runs/${run.id}`)}
                  >
                    <AlertCircle className="w-5 h-5 text-orange-500 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{draft?.summary}</span>
                        <StatusBadge kind="priority" status={priority} size="sm" />
                      </div>
                      <div className="text-sm text-gray-500 mt-1">
                        {draft?.project_key}-{draft?.issue_type} | Plan #{run.plan_id ?? '-'} | Job #{run.id}
                      </div>
                      <div className="text-sm text-gray-400 mt-1">
                        {draft?.description ? `${draft.description.substring(0, 100)}...` : '-'}
                      </div>
                      <div className="flex items-center gap-4 mt-2 text-xs text-gray-400">
                        <span>标签: {draft?.labels?.join(', ') || '-'}</span>
                        <span>组件: {draft?.component || '-'}</span>
                      </div>
                    </div>
                    <div className="text-right text-sm text-gray-500">
                      <div>{formatTime(run.ended_at ?? null)}</div>
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
        <CardContent className="text-sm text-gray-500 space-y-2">
          <p>问题追踪页面展示任务执行后自动生成的 JIRA 草稿。</p>
          <ul className="list-disc list-inside space-y-1">
            <li>草稿基于任务执行结果自动生成</li>
            <li>点击可查看详情并跳转到对应任务</li>
            <li>后续版本将支持直接提交到 JIRA</li>
          </ul>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
