import { useCallback, useState } from 'react';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { AlertCircle } from 'lucide-react';
import { api } from '@/utils/api';
import { planRunKeys } from '@/utils/api/queryKeys';
import type { EventSeverity, EventStage, PlanRunStatus } from '@/utils/api/types';
import PlanRunTabs from '@/components/plan-run/PlanRunTabs';
import PlanRunEventStream from '@/components/plan-run/PlanRunEventStream';
import { PageContainer, PageHeader } from '@/components/layout';
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
      <PageHeader
        title={`PlanRun #${id}`}
        breadcrumbs={[
          { label: 'Plan Runs', path: '/execution/plan-runs' },
          { label: `#${id}` },
        ]}
      />
      <div className="px-4 pb-2">
        <PlanRunTabs runId={id} active="logs" />
      </div>
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
