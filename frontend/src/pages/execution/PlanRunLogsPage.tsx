import { useCallback, useEffect, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { ArrowLeft, AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import type { EventSeverity, EventStage, PlanRunStatus } from '@/utils/api/types';
import PlanRunTabs from '@/components/plan-run/PlanRunTabs';
import PlanRunEventStream from '@/components/plan-run/PlanRunEventStream';
import { PageContainer } from '@/components/layout';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';
import { TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

const PAGE_SIZE = 50;
const SLOW_REFETCH_MS = 30_000;
const TERMINAL: ReadonlyArray<PlanRunStatus> = [
  'SUCCESS',
  'PARTIAL_SUCCESS',
  'FAILED',
  'DEGRADED',
];

/** 巡检日志页面 — 阶段/严重度过滤 + 分页事件流(多源融合)。 */
export default function PlanRunLogsPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const navigate = useNavigate();
  const { setHeaderSlot } = useHeaderSlot();

  const [stageFilter, setStageFilter] = useState<EventStage | 'all'>('all');
  const [severityFilter, setSeverityFilter] = useState<EventSeverity | 'all'>('all');
  const [page, setPage] = useState(0); // 0-based,与 PlanRunEventStream 对齐

  const runQ = useQuery({
    queryKey: planRunKeys.detail(id),
    queryFn: () => api.planRuns.get(id),
    enabled: !!id,
  });
  const isTerminal = !!runQ.data && TERMINAL.includes(runQ.data.status);

  const eventsQ = useQuery({
    queryKey: planRunKeys.logs(id, stageFilter, severityFilter, page),
    queryFn: () =>
      api.planRuns.getEvents(id, {
        stage: stageFilter,
        severity: severityFilter,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      }),
    enabled: !!id,
    refetchInterval: isTerminal ? false : SLOW_REFETCH_MS,
  });

  // ── 将 "返回 / PlanRun # / 概览 / 日志" 注入 AppShell 顶栏 ──
  useEffect(() => {
    if (!id || Number.isNaN(id)) return;
    setHeaderSlot(
      <div className="flex min-w-0 items-center gap-3">
        <Button
          variant="ghost"
          size="sm"
          onClick={() => navigate('/execution/plan-runs')}
          className="-ml-2 text-xs text-muted-foreground"
        >
          <ArrowLeft className="mr-1 h-3.5 w-3.5" /> 返回执行列表
        </Button>
        <PlanRunTabs runId={id} active="logs" />
      </div>,
    );
    return () => setHeaderSlot(null);
  }, [id, navigate, setHeaderSlot]);

  const handleStageChange = useCallback((s: EventStage | 'all') => {
    setStageFilter(s);
    setPage(0);
  }, []);

  const handleSeverityChange = useCallback((s: EventSeverity | 'all') => {
    setSeverityFilter(s);
    setPage(0);
  }, []);

  if (!id || Number.isNaN(id)) {
    return (
      <div className={cn('flex h-64 items-center justify-center text-sm', TEXT.subtitle)}>
        <AlertCircle className="mr-2 h-4 w-4" /> 无效 PlanRun ID
      </div>
    );
  }

  return (
    <PageContainer width="logs">
      <PlanRunEventStream
        events={eventsQ.data}
        stageFilter={stageFilter}
        severityFilter={severityFilter}
        onStageFilterChange={handleStageChange}
        onSeverityFilterChange={handleSeverityChange}
        isLoading={eventsQ.isLoading}
        isError={eventsQ.isError}
        page={page}
        pageSize={PAGE_SIZE}
        onPageChange={setPage}
      />
    </PageContainer>
  );
}
