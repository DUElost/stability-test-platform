import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { api, RunReport, JiraDraft } from '@/utils/api';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
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

const riskColors: Record<string, string> = {
  HIGH: 'bg-red-500/10 text-red-600 border-red-500/20',
  MEDIUM: 'bg-yellow-500/10 text-yellow-600 border-yellow-500/20',
  LOW: 'bg-green-500/10 text-green-600 border-green-500/20',
  UNKNOWN: 'bg-gray-500/10 text-gray-600 border-gray-500/20',
};

const severityIcons: Record<string, React.ReactNode> = {
  HIGH: <AlertTriangle className="w-4 h-4 text-red-500" />,
  MEDIUM: <Info className="w-4 h-4 text-yellow-500" />,
  LOW: <CheckCircle2 className="w-4 h-4 text-green-500" />,
};

export default function RunReportPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [report, setReport] = useState<RunReport | null>(null);
  const [jiraDraft, setJiraDraft] = useState<JiraDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showJira, setShowJira] = useState(false);

  useEffect(() => {
    if (!runId) return;
    const id = parseInt(runId, 10);
    setLoading(true);
    Promise.all([
      api.tasks.getCachedReport(id).then((r) => r.data).catch(() => null),
      api.tasks.getCachedJiraDraft(id).then((r) => r.data).catch(() => null),
    ])
      .then(([r, j]) => {
        if (r) setReport(r);
        else setError('报告数据加载失败');
        if (j) setJiraDraft(j);
      })
      .finally(() => setLoading(false));
  }, [runId]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        <span className="ml-2 text-muted-foreground">加载报告中...</span>
      </div>
    );
  }

  if (error || !report) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <p className="text-destructive">{error || '报告不存在'}</p>
        <Button variant="outline" onClick={() => navigate(-1)}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          返回
        </Button>
      </div>
    );
  }

  const risk = report.risk_summary || {};
  const riskLevel = (risk as any).risk_level || 'UNKNOWN';
  const counts = (risk as any).counts || {};

  return (
    <div className="space-y-6 p-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
            <ArrowLeft className="h-4 w-4" />
          </Button>
          <div>
            <h1 className="text-xl font-semibold">
              运行报告 #{report.run.id}
            </h1>
            <p className="text-sm text-muted-foreground">
              任务: {report.task.name} ({report.task.type})
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <a
            href={api.tasks.getRunReportExportUrl(parseInt(runId!, 10), 'markdown')}
            download
          >
            <Button variant="outline" size="sm">
              <Download className="mr-2 h-4 w-4" />
              Markdown
            </Button>
          </a>
          <a
            href={api.tasks.getRunReportExportUrl(parseInt(runId!, 10), 'json')}
            download
          >
            <Button variant="outline" size="sm">
              <Download className="mr-2 h-4 w-4" />
              JSON
            </Button>
          </a>
        </div>
      </div>

      {/* Info Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        {/* Task Info */}
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
              <Badge variant="outline">{report.run.status}</Badge>
            </div>
          </div>
        </div>

        {/* Host/Device Info */}
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

        {/* Risk Summary */}
        <div className="rounded-lg border p-4 space-y-2">
          <h3 className="text-sm font-medium text-muted-foreground">风险摘要</h3>
          <div className="flex items-center gap-2 mb-2">
            <Badge className={riskColors[riskLevel] || riskColors.UNKNOWN}>
              {riskLevel}
            </Badge>
          </div>
          <div className="space-y-1 text-sm">
            <div className="flex justify-between">
              <span className="text-muted-foreground">事件总数</span>
              <span>{counts.events_total ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">重启次数</span>
              <span>{counts.restart_count ?? 0}</span>
            </div>
            <div className="flex justify-between">
              <span className="text-muted-foreground">AEE 条目</span>
              <span>{counts.aee_entries ?? 0}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Summary Metrics */}
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

      {/* Alerts */}
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
                    <Badge variant="outline" className="text-xs">
                      {alert.severity}
                    </Badge>
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

      {/* JIRA Draft (collapsible) */}
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
                <Badge variant="outline">{jiraDraft.priority}</Badge>
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
    </div>
  );
}
