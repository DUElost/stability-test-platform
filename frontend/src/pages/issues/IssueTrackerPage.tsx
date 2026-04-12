import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type JiraDraft, type JobInstance } from '@/utils/api';
import { AlertCircle, RefreshCw, FileText } from 'lucide-react';

const PRIORITY_BADGE: Record<string, string> = {
  Critical: 'bg-red-100 text-red-700',
  Major: 'bg-orange-100 text-orange-700',
  Minor: 'bg-blue-100 text-blue-700',
};

interface RunWithDraft {
  run: JobInstance;
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
      const result = await api.execution.listJobs(0, 50);
      const runs = result.items;

      const runsWithDrafts: RunWithDraft[] = await Promise.all(
        runs.map(async (run: JobInstance) => {
          try {
            const draftResp = await api.execution.getCachedJobJiraDraft(run.id);
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
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-gray-900">问题追踪</h1>
          <p className="text-gray-500 mt-1">查看任务生成的 JIRA 草稿</p>
        </div>
        <Button variant="outline" onClick={handleRefresh} disabled={isRefreshing}>
          <RefreshCw className={`w-4 h-4 mr-2 ${isRefreshing ? 'animate-spin' : ''}`} />
          刷新
        </Button>
      </div>

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
                    onClick={() => navigate(`/runs/${run.id}/report`)}
                  >
                    <AlertCircle className="w-5 h-5 text-orange-500 mt-0.5" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="font-medium truncate">{draft?.summary}</span>
                        <span className={`px-2 py-0.5 rounded-full text-xs ${PRIORITY_BADGE[priority]}`}>
                          {priority}
                        </span>
                      </div>
                      <div className="text-sm text-gray-500 mt-1">
                        {draft?.project_key}-{draft?.issue_type} | 工作流 #{run.workflow_definition_id ?? '-'} | Job #{run.id}
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
    </div>
  );
}
