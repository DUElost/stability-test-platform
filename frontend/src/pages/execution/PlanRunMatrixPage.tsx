import { useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { api, type PlanJobInstance } from '@/utils/api';
import { useSocketIO } from '@/hooks/useSocketIO';
import {
  ArrowLeft, RefreshCw, Play, CheckCircle, XCircle, AlertTriangle,
  Clock, Activity,
} from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import {
  DRAWER,
  FORM,
  STEP_TRACE_DOT,
  SURFACE,
  TEXT,
  jobStatusCellClass,
} from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

const TERMINAL_STATUSES = ['SUCCESS', 'PARTIAL_SUCCESS', 'FAILED', 'DEGRADED'];

function JobBlock({ job, onClick }: { job: PlanJobInstance; onClick: () => void }) {
  const cellClass = jobStatusCellClass(job.status);
  const tooltipContent = (
    <div className="space-y-1">
      <p className="font-medium">Job #{job.id}</p>
      <p>设备: {job.device_serial || `Device#${job.device_id}`}</p>
      <p>状态: {job.status}</p>
      {job.started_at && <p className="text-xs opacity-75">开始: {formatDateTimeFull(job.started_at)}</p>}
    </div>
  );

  return (
    <TooltipProvider>
      <Tooltip delayDuration={300}>
        <TooltipTrigger asChild>
          <button
            onClick={onClick}
            className={cn(
              'min-w-[40px] min-h-[40px] w-full aspect-square rounded-lg border-2',
              'flex flex-col items-center justify-center text-primary-foreground text-xs font-medium',
              'transition-transform hover:scale-105',
              cellClass,
            )}
          >
            <span>{job.device_serial?.slice(-4) || `D${job.device_id}`}</span>
            <span className="text-[10px] opacity-75">{job.status}</span>
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          {tooltipContent}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

function stepTraceDotClass(eventType: string): string {
  if (eventType === 'COMPLETED') return STEP_TRACE_DOT.COMPLETED;
  if (eventType === 'FAILED') return STEP_TRACE_DOT.FAILED;
  if (eventType === 'RETRIED') return STEP_TRACE_DOT.RETRIED;
  return STEP_TRACE_DOT.default;
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
    <PageContainer width="default">
      <div>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
          className={cn('-ml-2 mb-2 text-xs', TEXT.subtitle)}
        >
          <ArrowLeft className="w-3.5 h-3.5 mr-1" /> 返回执行列表
        </Button>
        <PageHeader
          title={`PlanRun #${id}`}
          subtitle={
            run
              ? `Plan #${run.plan_id} · ${run.run_type} · ${run.status}`
              : undefined
          }
          action={
            <Button variant="outline" size="sm" onClick={() => { /* refresh handled by queryClient */ }}>
              <RefreshCw className="w-4 h-4 mr-1" /> 刷新
            </Button>
          }
        />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-5 gap-3">
        {[
          { label: '完成', key: 'COMPLETED', color: STATUS_TEXT_COLORS.success, icon: CheckCircle },
          { label: '失败', key: 'FAILED', color: STATUS_TEXT_COLORS.error, icon: XCircle },
          { label: '未知', key: 'UNKNOWN', color: STATUS_TEXT_COLORS.warning, icon: AlertTriangle },
          { label: '运行中', key: 'RUNNING', color: STATUS_TEXT_COLORS.primary, icon: Play },
          { label: '等待', key: 'PENDING', color: STATUS_TEXT_COLORS.muted, icon: Clock },
        ].map(s => (
          <Card key={s.key}>
            <CardContent className="py-3 text-center">
              <s.icon className={`w-4 h-4 mx-auto mb-1 ${s.color}`} />
              <p className="text-lg font-bold">{statusCounts[s.key] || 0}</p>
              <p className={cn('text-xs', TEXT.subtitle)}>{s.label}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center justify-between">
            <span>任务矩阵 ({jobs?.length ?? 0} Jobs)</span>
            <input
              type="text"
              placeholder="搜索 Job ID / Serial / Status..."
              value={jobFilter}
              onChange={e => setJobFilter(e.target.value)}
              className={cn('w-56 font-normal', FORM.inputSm)}
            />
          </CardTitle>
        </CardHeader>
        <CardContent>
          {!jobs ? (
            <Skeleton className="h-48 w-full" />
          ) : jobs.length === 0 ? (
            <p className={cn('text-sm py-8 text-center', TEXT.subtitle)}>暂未创建 Job</p>
          ) : (
            <div className="grid grid-cols-5 sm:grid-cols-6 md:grid-cols-8 xl:grid-cols-10 gap-2">
              {filteredJobs?.map(job => (
                <JobBlock key={job.id} job={job} onClick={() => setSelectedJob(job)} />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {selectedJob && (
        <>
          <div
            className={DRAWER.overlay}
            onClick={() => setSelectedJob(null)}
            aria-hidden
          />
          <div
            className={cn(DRAWER.panel, 'w-96')}
            onClick={e => e.stopPropagation()}
          >
            <div className="p-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className={cn('font-semibold', TEXT.heading)}>Job #{selectedJob.id}</h2>
                <Button
                  variant="ghost"
                  size="sm"
                  className={DRAWER.closeBtn}
                  onClick={() => setSelectedJob(null)}
                >
                  ✕
                </Button>
              </div>
              <div className="space-y-2 text-sm">
                {[
                  ['状态', selectedJob.status],
                  ['设备', selectedJob.device_serial || `Device #${selectedJob.device_id}`],
                  ['Host', selectedJob.host_id || '-'],
                  ...(selectedJob.status_reason ? [['原因', selectedJob.status_reason] as const] : []),
                  ['开始', selectedJob.started_at ? formatDateTimeFull(selectedJob.started_at) : '-'],
                  ['结束', selectedJob.ended_at ? formatDateTimeFull(selectedJob.ended_at) : '-'],
                ].map(([label, value]) => (
                  <div key={label} className="flex justify-between gap-2">
                    <span className={TEXT.subtitle}>{label}</span>
                    <span className={cn('text-right truncate max-w-[60%]', TEXT.body)} title={String(value)}>
                      {value}
                    </span>
                  </div>
                ))}
              </div>
              {selectedJob.step_traces && selectedJob.step_traces.length > 0 && (
                <div className="mt-4">
                  <h3 className={cn('text-sm font-medium mb-2', TEXT.heading)}>步骤追踪</h3>
                  <div className="space-y-1.5">
                    {selectedJob.step_traces.map(t => (
                      <div
                        key={t.id}
                        className={cn(
                          'flex items-center gap-2 text-xs py-1 px-2 rounded',
                          SURFACE.subtle,
                        )}
                      >
                        <span className={cn('w-1.5 h-1.5 rounded-full', stepTraceDotClass(t.event_type))} />
                        <span className="font-mono">{t.step_id}</span>
                        <span className={TEXT.subtitle}>{t.event_type}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <div className="mt-4">
                <Button
                  variant="outline"
                  size="sm"
                  className="w-full"
                  onClick={() => navigate(`/runs/${selectedJob.id}/report`)}
                >
                  <Activity className="w-3.5 h-3.5 mr-1.5" /> 查看运行报告
                </Button>
              </div>
            </div>
          </div>
        </>
      )}
    </PageContainer>
  );
}
