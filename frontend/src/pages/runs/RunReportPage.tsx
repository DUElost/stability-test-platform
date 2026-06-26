import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { api, type RunRiskSummary } from '@/utils/api';
import { jobReportKeys } from '@/utils/api/queryKeys';
import { StatusBadge } from '@/components/ui/status-badge';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import {
  ArrowLeft,
  Download,
  FileText,
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
} from 'lucide-react';
import { format } from 'date-fns';
import { ENTITY_STATUS_COLORS } from '@/design-system/colors';
import { ALERT_BANNER, RISK_RATING_TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

const severityIcons: Record<string, React.ReactNode> = {
  HIGH: <AlertTriangle className={cn('w-4 h-4', ENTITY_STATUS_COLORS.alert.high)} />,
  MEDIUM: <Info className={cn('w-4 h-4', ENTITY_STATUS_COLORS.alert.medium)} />,
  LOW: <CheckCircle2 className={cn('w-4 h-4', ENTITY_STATUS_COLORS.alert.low)} />,
};

export default function RunReportPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [showJira, setShowJira] = useState(false);

  const jobId = runId ? parseInt(runId, 10) : NaN;
  const enabled = !!runId && !Number.isNaN(jobId);

  const reportQ = useQuery({
    queryKey: jobReportKeys.report(jobId),
    queryFn: () => api.runs.getCachedReport(jobId),
    enabled,
    retry: false,
  });

  const jiraQ = useQuery({
    queryKey: jobReportKeys.jiraDraft(jobId),
    queryFn: async () => {
      try {
        return await api.runs.getCachedJiraDraft(jobId);
      } catch {
        return null;
      }
    },
    enabled: enabled && reportQ.isSuccess,
    retry: false,
  });

  if (!enabled) {
    return (
      <ErrorState
        title="无效 Job ID"
        description="URL 中的 Job ID 无法解析"
        action={
          <Button variant="outline" onClick={() => navigate(-1)}>
            <ArrowLeft className="mr-2 h-4 w-4" />
            返回
          </Button>
        }
      />
    );
  }

  if (reportQ.isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">加载报告中...</span>
      </div>
    );
  }

  if (reportQ.isError || !reportQ.data) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <ErrorState
          title="报告加载失败"
          description="报告数据不存在或尚未生成"
          onRetry={() => reportQ.refetch()}
        />
        <Button variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          返回
        </Button>
      </div>
    );
  }

  const report = reportQ.data;
  const jiraDraft = jiraQ.data ?? null;
  const risk: RunRiskSummary = report.risk_summary || {};
  const riskLevel = risk.risk_level || 'UNKNOWN';
  const counts = risk.counts || {};

  return (
    <PageContainer width="default">
      <PageHeader
        title={`Job 运行报告 #${report.run.id}`}
        subtitle={`任务: ${report.task.name} (${report.task.type})`}
        breadcrumbs={[{ label: '测试结果', path: '/results' }, { label: `Job #${report.run.id}` }]}
        action={
          <div className="flex items-center gap-2">
            <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
              <ArrowLeft className="h-4 w-4" />
            </Button>
            <a
              href={`/api/v1/runs/${jobId}/report/export?format=markdown`}
              download
            >
              <Button variant="outline" size="sm">
                <Download className="mr-2 h-4 w-4" />
                Markdown
              </Button>
            </a>
            <a
              href={`/api/v1/runs/${jobId}/report/export?format=json`}
              download
            >
              <Button variant="outline" size="sm">
                <Download className="mr-2 h-4 w-4" />
                JSON
              </Button>
            </a>
          </div>
        }
      />

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">任务信息</h3>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">任务ID</span>
              <span>{report.task.id}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">类型</span>
              <span>{report.task.type}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">状态</span>
              <StatusBadge kind="job" status={report.run.status} size="sm" />
            </div>
          </div>
        </div>

        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">执行环境</h3>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">主机</span>
              <span>{report.host?.name || 'N/A'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">设备</span>
              <span className="font-mono text-xs">{report.device?.serial || 'N/A'}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">生成时间</span>
              <span>{format(new Date(report.generated_at), 'yyyy-MM-dd HH:mm')}</span>
            </div>
          </div>
        </div>

        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">风险摘要</h3>
          {report.report_status === 'pending_archive' && (
            <div className={cn('mb-2 rounded-md border px-3 py-2 text-xs', ALERT_BANNER.warning)}>
              归档进行中，风险摘要将在归档完成后可用
            </div>
          )}
          <div className="flex items-center gap-2 mb-2">
            <StatusBadge kind="risk" status={riskLevel} size="sm" />
          </div>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">事件总数(去重)</span>
              <span>{counts.events_total ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">AEE 条目</span>
              <span>{counts.aee_entries ?? 0}</span>
            </div>
            {counts.by_severity && (
              <div className="flex justify-between">
                <span className="text-muted-foreground">S/A/B 分布</span>
                <span>
                  <span className={RISK_RATING_TEXT.S}>{counts.by_severity.S ?? 0}</span>/
                  <span className={RISK_RATING_TEXT.A}>{counts.by_severity.A ?? 0}</span>/
                  <span className={RISK_RATING_TEXT.B}>{counts.by_severity.B ?? 0}</span>
                </span>
              </div>
            )}
            {counts.by_type && Object.keys(counts.by_type).length > 0 && (
              <div className="mt-2 space-y-0.5 text-xs">
                {Object.entries(counts.by_type).map(([subtype, count]) => (
                  <div key={subtype} className="flex justify-between">
                    <span className="text-muted-foreground">{subtype}</span>
                    <span className="font-mono">{count}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {report.summary_metrics && Object.keys(report.summary_metrics).length > 0 && (
        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">汇总指标</h3>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {Object.entries(report.summary_metrics).map(([key, value]) => (
              <div key={key} className="text-sm">
                <span className="text-muted-foreground">{key}</span>
                <p className="font-medium">{String(value)}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-lg border p-4 space-y-3">
        <h3 className="text-sm font-medium text-muted-foreground">
          告警列表 ({report.alerts?.length ?? 0})
        </h3>
        {report.alerts && report.alerts.length > 0 ? (
          <div className="space-y-2">
            {report.alerts.map((alert, idx) => (
              <div
                key={idx}
                className="flex items-start gap-3 rounded-md border p-3 text-sm"
              >
                {severityIcons[alert.severity] || severityIcons.LOW}
                <div className="flex-1">
                  <div className="flex items-center gap-2">
                    <StatusBadge kind="risk" status={alert.severity} size="sm" />
                    <span className="font-medium">{alert.code}</span>
                  </div>
                  <p className="text-muted-foreground mt-1">{alert.message}</p>
                </div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">无告警</p>
        )}
      </div>

      {jiraDraft && (
        <div className="rounded-lg border p-4 space-y-3">
          <button
            className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowJira(!showJira)}
          >
            <FileText className="h-4 w-4" />
            JIRA 草稿 {showJira ? '(收起)' : '(展开)'}
          </button>
          {showJira && (
            <div className="space-y-2 text-sm border-t pt-3">
              <div>
                <span className="text-muted-foreground">摘要: </span>
                <span className="font-medium">{jiraDraft.summary}</span>
              </div>
              <div>
                <span className="text-muted-foreground">优先级: </span>
                <StatusBadge kind="priority" status={jiraDraft.priority} size="sm" />
              </div>
              <div>
                <span className="text-muted-foreground">标签: </span>
                <span>{jiraDraft.labels?.join(', ') || 'N/A'}</span>
              </div>
              <div className="mt-2">
                <span className="text-muted-foreground">描述:</span>
                <pre className="mt-1 p-3 bg-muted rounded-md text-xs whitespace-pre-wrap overflow-auto max-h-64">
                  {jiraDraft.description}
                </pre>
              </div>
            </div>
          )}
        </div>
      )}
    </PageContainer>
  );
}
