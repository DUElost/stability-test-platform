import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { api, type PlanJobInstance } from '@/utils/api';
import { useSocketIO } from '@/hooks/useSocketIO';
import {
  ArrowLeft, RefreshCw, Play, CheckCircle, XCircle, AlertTriangle,
  Clock, Activity,
} from 'lucide-react';

const TERMINAL_STATUSES = ['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DEGRADED'];

const JOB_STATUS_COLORS: Record<string, string> = {
  PENDING: 'bg-gray-200 border-gray-300',
  RUNNING: 'bg-blue-500 border-blue-600',
  COMPLETED: 'bg-green-500 border-green-600',
  FAILED: 'bg-red-500 border-red-600',
  ABORTED: 'bg-yellow-500 border-yellow-600',
  UNKNOWN: 'bg-purple-500 border-purple-600',
};

function JobBlock({ job, onClick }: { job: PlanJobInstance; onClick: () => void }) {
  const color = JOB_STATUS_COLORS[job.status] || 'bg-gray-300 border-gray-400';
  return (
    <button onClick={onClick}
      className={`w-full aspect-square rounded-lg border-2 flex flex-col items-center justify-center text-white text-xs font-medium transition-transform hover:scale-105 ${color}`}
      title={`Job #${job.id} ${job.status} — ${job.device_serial || 'Device#' + job.device_id}`}>
      <span>{job.device_serial?.slice(-4) || `D${job.device_id}`}</span>
      <span className="text-[10px] opacity-75">{job.status}</span>
    </button>
  );
}

export default function PlanRunMatrixPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const [selectedJob, setSelectedJob] = useState<PlanJobInstance | null>(null);
  const [jobFilter, setJobFilter] = useState('');

  const { data: run } = useQuery({
    queryKey: ['plan-run', id],
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
    refetchInterval: (data) => data && TERMINAL_STATUSES.includes(data.status) ? false : 10_000,
  });

  const { data: jobs } = useQuery({
    queryKey: ['plan-run-jobs', id],
    queryFn: () => api.planRuns.listJobs(id),
    enabled: !!id,
    refetchInterval: (data) => {
      if (!data) return 10_000;
      const allDone = data.every(j => TERMINAL_STATUSES.includes(j.status));
      return allDone ? false : 10_000;
    },
  });

  const runTerminal = run && TERMINAL_STATUSES.includes(run.status);

  // SocketIO real-time
  useSocketIO('/', {
    enabled: !!id && !runTerminal,
  });

  const filteredJobs = jobs?.filter(j =>
    !jobFilter || String(j.id).includes(jobFilter) ||
    (j.device_serial || '').toLowerCase().includes(jobFilter.toLowerCase()) ||
    j.status.toLowerCase().includes(jobFilter.toLowerCase())
  );

  const statusCounts = jobs?.reduce((acc, j) => {
    acc[j.status] = (acc[j.status] || 0) + 1;
    return acc;
  }, {} as Record<string, number>) ?? {};

  return (
    <div className="space-y-6 max-w-6xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate('/execution/plan-runs')}>
            <ArrowLeft className="w-4 h-4" />
          </Button>
          <div>
            <h1 className="text-2xl font-semibold text-gray-900">PlanRun #{id}</h1>
            {run && (
              <p className="text-sm text-gray-500">
                Plan #{run.plan_id} &middot; {run.run_type} &middot;
                <span className={runTerminal ? 'text-green-600' : 'text-blue-600'}> {run.status}</span>
              </p>
            )}
          </div>
        </div>
        <Button variant="outline" size="sm" onClick={() => { /* refresh handled by queryClient */ }}>
          <RefreshCw className="w-4 h-4 mr-1" /> 刷新
        </Button>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-5 gap-3">
        {[
          { label: '完成', key: 'COMPLETED', color: 'text-green-600', icon: CheckCircle },
          { label: '失败', key: 'FAILED', color: 'text-red-600', icon: XCircle },
          { label: '未知', key: 'UNKNOWN', color: 'text-purple-600', icon: AlertTriangle },
          { label: '运行中', key: 'RUNNING', color: 'text-blue-600', icon: Play },
          { label: '等待', key: 'PENDING', color: 'text-gray-500', icon: Clock },
        ].map(s => (
          <Card key={s.key}>
            <CardContent className="py-3 text-center">
              <s.icon className={`w-4 h-4 mx-auto mb-1 ${s.color}`} />
              <p className="text-lg font-bold">{statusCounts[s.key] || 0}</p>
              <p className="text-xs text-gray-500">{s.label}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Job Grid */}
      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span>任务矩阵 ({jobs?.length ?? 0} Jobs)</span>
            <input type="text" placeholder="搜索 Job ID / Serial / Status..." value={jobFilter}
              onChange={e => setJobFilter(e.target.value)}
              className="w-56 px-3 py-1 text-sm border rounded-lg focus:outline-none focus:ring-2 focus:ring-blue-500/20 font-normal" />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!jobs ? (
            <Skeleton className="h-48 w-full" />
          ) : jobs.length === 0 ? (
            <p className="text-sm text-gray-400 py-8 text-center">暂未创建 Job</p>
          ) : (
            <div className="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-8 xl:grid-cols-10 gap-2">
              {filteredJobs?.map(job => (
                <JobBlock key={job.id} job={job} onClick={() => setSelectedJob(job)} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* Selected Job Detail Drawer */}
      {selectedJob && (
        <div className="fixed inset-y-0 right-0 z-40 w-96 bg-white shadow-2xl border-l overflow-y-auto" onClick={e => e.stopPropagation()}>
          <div className="p-4">
            <div className="flex items-center justify-between mb-4">
              <h2 className="font-semibold">Job #{selectedJob.id}</h2>
              <Button variant="ghost" size="sm" onClick={() => setSelectedJob(null)}>✕</Button>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex justify-between"><span className="text-gray-500">状态</span><span>{selectedJob.status}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">设备</span><span className="font-mono">{selectedJob.device_serial || `Device #${selectedJob.device_id}`}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">Host</span><span>{selectedJob.host_id || '-'}</span></div>
              {selectedJob.status_reason && (
                <div className="flex justify-between"><span className="text-gray-500">原因</span><span>{selectedJob.status_reason}</span></div>
              )}
              <div className="flex justify-between"><span className="text-gray-500">开始</span><span>{selectedJob.started_at ? new Date(selectedJob.started_at).toLocaleString() : '-'}</span></div>
              <div className="flex justify-between"><span className="text-gray-500">结束</span><span>{selectedJob.ended_at ? new Date(selectedJob.ended_at).toLocaleString() : '-'}</span></div>
            </div>
            {selectedJob.step_traces && selectedJob.step_traces.length > 0 && (
              <div className="mt-4">
                <h3 className="text-sm font-medium mb-2">步骤追踪</h3>
                <div className="space-y-1.5">
                  {selectedJob.step_traces.map(t => (
                    <div key={t.id} className="flex items-center gap-2 text-xs py-1 px-2 bg-gray-50 rounded">
                      <span className={`w-1.5 h-1.5 rounded-full ${
                        t.event_type === 'COMPLETED' ? 'bg-green-500' :
                        t.event_type === 'FAILED' ? 'bg-red-500' :
                        t.event_type === 'RETRIED' ? 'bg-yellow-500' : 'bg-blue-500'
                      }`} />
                      <span className="font-mono">{t.step_id}</span>
                      <span className="text-gray-400">{t.event_type}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}
            <div className="mt-4">
              <Button variant="outline" size="sm" className="w-full"
                onClick={() => navigate(`/runs/${selectedJob.id}/report`)}>
                <Activity className="w-3.5 h-3.5 mr-1.5" /> 查看运行报告
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
