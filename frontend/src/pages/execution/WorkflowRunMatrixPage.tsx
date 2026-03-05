import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type WorkflowRun, type JobInstance, type StepTrace, type JobStatus, type WorkflowStatus } from '@/utils/api';
import { ensureFreshAccessToken, upsertWsToken } from '@/utils/auth';
import { formatLocalDateTime, formatLocalTime, parseIsoToDate } from '@/utils/time';
import { ArrowLeft, RefreshCw, X } from 'lucide-react';

// ─── Status config ────────────────────────────────────────────────────────────

const JOB_STATUS_STYLE: Record<JobStatus, { bg: string; text: string; label: string; pulse?: boolean }> = {
  PENDING:      { bg: 'bg-gray-100',    text: 'text-gray-500',  label: 'Pending' },
  RUNNING:      { bg: 'bg-blue-100',    text: 'text-blue-700',  label: 'Running', pulse: true },
  COMPLETED:    { bg: 'bg-green-100',   text: 'text-green-700', label: 'Done' },
  FAILED:       { bg: 'bg-red-100',     text: 'text-red-700',   label: 'Failed' },
  ABORTED:      { bg: 'bg-orange-100',  text: 'text-orange-700', label: 'Aborted' },
  UNKNOWN:      { bg: 'bg-yellow-100',  text: 'text-yellow-700', label: 'Unknown' },
  PENDING_TOOL: { bg: 'bg-purple-100',  text: 'text-purple-700', label: 'PendingTool' },
};

const WF_STATUS_BADGE: Record<WorkflowStatus, string> = {
  RUNNING:        'bg-blue-100 text-blue-700',
  SUCCESS:        'bg-green-100 text-green-700',
  PARTIAL_SUCCESS: 'bg-orange-100 text-orange-700',
  FAILED:         'bg-red-100 text-red-700',
  DEGRADED:       'bg-gray-200 text-gray-600',
};

const TERMINAL_STATUSES: WorkflowStatus[] = ['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DEGRADED'];

function formatTime(iso: string | null | undefined) {
  return formatLocalDateTime(iso);
}

function formatDuration(start: string, end: string | null | undefined) {
  const startDate = parseIsoToDate(start);
  const endDate = parseIsoToDate(end);
  if (!startDate || !endDate) return end ? '-' : 'running...';
  const s = Math.round((endDate.getTime() - startDate.getTime()) / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${s % 60}s`;
}

// ─── Job block ────────────────────────────────────────────────────────────────

function JobBlock({ job, onClick }: { job: JobInstance; onClick: () => void }) {
  const style = JOB_STATUS_STYLE[job.status] ?? JOB_STATUS_STYLE.PENDING;
  const label = job.device_serial ?? `#${job.device_id}`;
  return (
    <button
      className={`
        w-full p-2 rounded-lg border text-left transition-all duration-200
        ${style.bg} ${style.text} hover:shadow-md hover:scale-105 active:scale-100
        ${style.pulse ? 'animate-pulse' : ''}
      `}
      onClick={onClick}
    >
      <div className="font-mono text-xs font-semibold truncate">{label}</div>
      <div className="text-xs truncate mt-0.5 opacity-75">{style.label}</div>
      {job.status_reason && (
        <div className="text-xs truncate mt-0.5 opacity-60">{job.status_reason}</div>
      )}
    </button>
  );
}

// ─── Step trace timeline ───────────────────────────────────────────────────────

function StepTimeline({ traces }: { traces: StepTrace[] }) {
  if (!traces.length) return <p className="text-sm text-gray-400 py-2">暂无 Step 数据</p>;
  const sorted = [...traces].sort((a, b) => {
    const aTime = parseIsoToDate(a.original_ts)?.getTime() ?? 0;
    const bTime = parseIsoToDate(b.original_ts)?.getTime() ?? 0;
    return aTime - bTime;
  });
  return (
    <div className="space-y-2">
      {sorted.map(t => (
        <div key={t.id} className="flex items-start gap-3 text-sm">
          <div className={`w-2 h-2 rounded-full mt-1.5 flex-shrink-0 ${
            t.event_type === 'COMPLETED' ? 'bg-green-500' :
            t.event_type === 'FAILED'    ? 'bg-red-500' :
            t.event_type === 'RETRIED'   ? 'bg-yellow-500' :
                                           'bg-blue-400'
          }`} />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-mono text-xs text-gray-500">{t.stage}</span>
              <span className="font-medium text-gray-800">{t.step_id}</span>
              <span className="text-xs text-gray-400">{t.event_type}</span>
            </div>
            {t.error_message && (
              <div className="text-xs text-red-600 mt-0.5 truncate">{t.error_message}</div>
            )}
            <div className="text-xs text-gray-400">{formatTime(t.original_ts)}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── Job log stream ───────────────────────────────────────────────────────────

interface LogLine {
  ts: string;
  level: string;
  msg: string;
  step_id?: string;
}

function JobLogStream({ jobId }: { jobId: number }) {
  const [lines, setLines] = useState<LogLine[]>([]);
  const [wsStatus, setWsStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let attempt = 0;

    const getReconnectDelay = () => Math.min(1000 * Math.pow(2, attempt), 30000);

    const connect = async () => {
      if (stopped) return;
      setWsStatus('connecting');

      const token = await ensureFreshAccessToken();
      if (import.meta.env.PROD && !token) {
        setWsStatus('closed');
        return;
      }
      const baseUrl = `${protocol}://${window.location.host}/ws/jobs/${jobId}/logs`;
      const wsUrl = token ? upsertWsToken(baseUrl, token) : baseUrl;
      if (stopped) return;

      ws = new WebSocket(wsUrl);
      ws.onopen = () => {
        attempt = 0;
        setWsStatus('open');
      };
      ws.onclose = () => {
        setWsStatus('closed');
        if (stopped) return;
        const delay = getReconnectDelay();
        attempt += 1;
        reconnectTimer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'STEP_LOG' && msg.payload) {
            setLines(prev => [...prev.slice(-499), msg.payload as LogLine]);
          }
        } catch {}
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [jobId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [lines]);

  const statusColor = wsStatus === 'open' ? 'bg-green-400' : wsStatus === 'connecting' ? 'bg-yellow-400 animate-pulse' : 'bg-gray-300';

  return (
    <div className="flex flex-col h-64">
      <div className="flex items-center gap-2 mb-2">
        <span className={`w-2 h-2 rounded-full ${statusColor}`} />
        <span className="text-xs text-gray-400">{wsStatus === 'open' ? '实时连接' : wsStatus === 'connecting' ? '连接中…' : '已断开'}</span>
      </div>
      <div className="flex-1 overflow-y-auto bg-gray-950 rounded p-2 font-mono text-xs">
        {lines.length === 0 ? (
          <span className="text-gray-500">等待日志…</span>
        ) : (
          lines.map((l, i) => (
            <div key={i} className={`whitespace-pre-wrap ${
              l.level === 'ERROR' ? 'text-red-400' :
              l.level === 'WARN'  ? 'text-yellow-400' :
                                     'text-green-300'
            }`}>
              <span className="text-gray-500 mr-2">{formatLocalTime(l.ts)}</span>
              {l.step_id && <span className="text-blue-400 mr-2">[{l.step_id}]</span>}
              {l.msg}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

// ─── Job detail drawer ─────────────────────────────────────────────────────────

type DrawerTab = 'trace' | 'logs';

function JobDrawer({ job, onClose }: { job: JobInstance; onClose: () => void }) {
  const style = JOB_STATUS_STYLE[job.status] ?? JOB_STATUS_STYLE.PENDING;
  const [tab, setTab] = useState<DrawerTab>('trace');
  return (
    <div className="fixed inset-y-0 right-0 z-50 w-96 bg-white shadow-2xl border-l flex flex-col">
      <div className="flex items-center justify-between px-4 py-3 border-b">
        <h2 className="font-semibold text-gray-900">Job #{job.id}</h2>
        <button onClick={onClose} className="p-1 rounded hover:bg-gray-100">
          <X className="w-5 h-5 text-gray-500" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Meta */}
        <div className="space-y-2 text-sm">
          <div className="flex justify-between">
            <span className="text-gray-500">设备</span>
            <span className="font-mono">{job.device_serial ?? `#${job.device_id}`}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">主机</span>
            <span className="font-mono">{job.host_id}</span>
          </div>
          <div className="flex justify-between">
            <span className="text-gray-500">状态</span>
            <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${style.bg} ${style.text}`}>
              {style.label}
            </span>
          </div>
          {job.status_reason && (
            <div className="flex justify-between">
              <span className="text-gray-500">原因</span>
              <span className="text-gray-700 text-xs max-w-40 text-right">{job.status_reason}</span>
            </div>
          )}
          <div className="flex justify-between">
            <span className="text-gray-500">开始时间</span>
            <span className="text-gray-700">{formatTime(job.created_at)}</span>
          </div>
        </div>

        {/* Tabs */}
        <div className="flex border-b">
          {(['trace', 'logs'] as DrawerTab[]).map(t => (
            <button
              key={t}
              className={`px-4 py-1.5 text-sm font-medium border-b-2 transition-colors ${
                tab === t ? 'border-blue-500 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'
              }`}
              onClick={() => setTab(t)}
            >
              {t === 'trace' ? 'Step 时间线' : '实时日志'}
            </button>
          ))}
        </div>

        {tab === 'trace' && <StepTimeline traces={job.step_traces ?? []} />}
        {tab === 'logs' && <JobLogStream jobId={job.id} />}
      </div>
    </div>
  );
}

// ─── Main page ─────────────────────────────────────────────────────────────────

export default function WorkflowRunMatrixPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selectedJob, setSelectedJob] = useState<JobInstance | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [wsStatus, setWsStatus] = useState<'connecting' | 'open' | 'closed'>('connecting');

  const { data: run, isLoading: runLoading } = useQuery({
    queryKey: ['workflow-run', runId],
    queryFn: () => api.execution.getRun(Number(runId)),
    enabled: !!runId,
    refetchInterval: (data) =>
      data && TERMINAL_STATUSES.includes(data.status) ? false : 15000,
  });

  const { data: jobs, isLoading: jobsLoading } = useQuery({
    queryKey: ['workflow-run-jobs', runId],
    queryFn: () => api.execution.getRunJobs(Number(runId)),
    enabled: !!runId,
    refetchInterval: () => {
      const runStatus = queryClient.getQueryData<WorkflowRun>(['workflow-run', runId])?.status;
      return runStatus && TERMINAL_STATUSES.includes(runStatus) ? false : 10000;
    },
  });

  // WebSocket for real-time job status updates
  useEffect(() => {
    if (!runId) return;
    const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let attempt = 0;

    const getReconnectDelay = () => Math.min(1000 * Math.pow(2, attempt), 30000);

    const connect = async () => {
      if (stopped) return;
      setWsStatus('connecting');

      const token = await ensureFreshAccessToken();
      if (import.meta.env.PROD && !token) {
        setWsStatus('closed');
        return;
      }
      const baseUrl = `${protocol}://${window.location.host}/ws/workflow-runs/${runId}`;
      const wsUrl = token ? upsertWsToken(baseUrl, token) : baseUrl;
      if (stopped) return;

      ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        attempt = 0;
        setWsStatus('open');
      };
      ws.onclose = () => {
        setWsStatus('closed');
        if (stopped) return;
        const runStatus = queryClient.getQueryData<WorkflowRun>(['workflow-run', runId])?.status;
        if (!runStatus || !TERMINAL_STATUSES.includes(runStatus)) {
          const delay = getReconnectDelay();
          attempt += 1;
          reconnectTimer = setTimeout(connect, delay);
        }
      };
      ws.onerror = () => ws?.close();

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'job_status') {
            queryClient.setQueryData<JobInstance[]>(['workflow-run-jobs', runId], (prev) =>
              prev?.map(j => j.id === msg.job_id ? { ...j, status: msg.status } : j)
            );
          } else if (msg.type === 'workflow_status') {
            queryClient.setQueryData<WorkflowRun>(['workflow-run', runId], (prev) =>
              prev ? { ...prev, status: msg.status } : prev
            );
            if (TERMINAL_STATUSES.includes(msg.status)) {
              ws?.close();
            }
          }
        } catch {}
      };
    };

    connect();

    return () => {
      stopped = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      ws?.close();
    };
  }, [runId]);

  const allJobs = jobs ?? [];
  const counts = {
    COMPLETED: allJobs.filter(j => j.status === 'COMPLETED').length,
    FAILED:    allJobs.filter(j => j.status === 'FAILED' || j.status === 'ABORTED').length,
    UNKNOWN:   allJobs.filter(j => j.status === 'UNKNOWN').length,
    RUNNING:   allJobs.filter(j => j.status === 'RUNNING').length,
    PENDING:   allJobs.filter(j => j.status === 'PENDING' || j.status === 'PENDING_TOOL').length,
  };
  const progress = allJobs.length > 0
    ? Math.round(((counts.COMPLETED + counts.FAILED + counts.UNKNOWN) / allJobs.length) * 100)
    : 0;

  if (runLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-10 w-64" />
        <Skeleton className="h-32 w-full" />
        <div className="grid grid-cols-8 gap-2">
          {Array.from({ length: 16 }).map((_, i) => <Skeleton key={i} className="h-16" />)}
        </div>
      </div>
    );
  }

  if (!run) {
    return (
      <div className="text-center py-12 text-gray-500">
        Run #{runId} 不存在
        <Button className="mt-4 block mx-auto" variant="outline" onClick={() => navigate(-1)}>
          返回
        </Button>
      </div>
    );
  }

  const wfBadge = WF_STATUS_BADGE[run.status] ?? 'bg-gray-100 text-gray-600';

  return (
    <div className="space-y-6">
      {selectedJob && (
        <JobDrawer job={selectedJob} onClose={() => setSelectedJob(null)} />
      )}

      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(-1)}>
            <ArrowLeft className="w-4 h-4 mr-1" />
            返回
          </Button>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-xl font-semibold text-gray-900">Run #{runId}</h1>
              <span className={`px-2 py-0.5 rounded-full text-xs font-medium ${wfBadge}`}>
                {run.status}
              </span>
              <span className={`w-2 h-2 rounded-full ${
                wsStatus === 'open' ? 'bg-green-400' : wsStatus === 'connecting' ? 'bg-yellow-400 animate-pulse' : 'bg-gray-300'
              }`} title={`WebSocket: ${wsStatus}`} />
            </div>
            <p className="text-sm text-gray-500">
              {formatTime(run.started_at)} → {formatTime(run.ended_at ?? null)}
              {run.ended_at && ` (${formatDuration(run.started_at, run.ended_at)})`}
            </p>
          </div>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => {
            queryClient.invalidateQueries({ queryKey: ['workflow-run', runId] });
            queryClient.invalidateQueries({ queryKey: ['workflow-run-jobs', runId] });
          }}
        >
          <RefreshCw className="w-4 h-4 mr-1" />
          刷新
        </Button>
      </div>

      {/* Progress bar */}
      {run.status === 'RUNNING' && allJobs.length > 0 && (
        <div className="h-2 bg-gray-100 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 transition-all duration-500"
            style={{ width: `${progress}%` }}
          />
        </div>
      )}

      {/* Matrix */}
      <Card>
        <CardHeader>
          <CardTitle>设备矩阵 ({allJobs.length} 台)</CardTitle>
        </CardHeader>
        <CardContent>
          {jobsLoading ? (
            <div className="grid grid-cols-8 gap-2">
              {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-16" />)}
            </div>
          ) : allJobs.length === 0 ? (
            <div className="text-center py-8 text-gray-400 text-sm">暂无 Job 数据</div>
          ) : (
            <div className="grid grid-cols-4 sm:grid-cols-6 md:grid-cols-8 xl:grid-cols-10 gap-2">
              {allJobs.map(job => (
                <JobBlock
                  key={job.id}
                  job={job}
                  onClick={() => setSelectedJob(job)}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Aggregate stats */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: '完成', count: counts.COMPLETED, color: 'text-green-700 bg-green-50' },
          { label: '失败', count: counts.FAILED,    color: 'text-red-700 bg-red-50' },
          { label: '未知', count: counts.UNKNOWN,   color: 'text-yellow-700 bg-yellow-50' },
          { label: '运行中', count: counts.RUNNING,  color: 'text-blue-700 bg-blue-50' },
          { label: '等待中', count: counts.PENDING,  color: 'text-gray-600 bg-gray-50' },
        ].map(s => (
          <Card key={s.label} className={`border-0 ${s.color}`}>
            <CardContent className="flex flex-col items-center py-3">
              <span className="text-2xl font-bold">{s.count}</span>
              <span className="text-xs mt-0.5">{s.label}</span>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
