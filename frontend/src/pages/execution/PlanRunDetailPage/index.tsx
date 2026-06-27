import { useState } from 'react';
import { useParams } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { PageContainer, PageHeaderV2 } from '@/components/layout';
import { usePlanRunDetailData } from '@/hooks/plan-run/usePlanRunDetailData';
import { isPlanRunTerminal } from '@/hooks/plan-run/planRunDetailUtils';
import { PlanRunMeta } from './PlanRunMeta';
import { RunStatusBanner } from './RunStatusBanner';
import { RunOverviewTab } from './RunOverviewTab';
import { RunDevicesTab } from './RunDevicesTab';
import { RunArtifactsTab } from './RunArtifactsTab';
import { RunLogsTab } from './RunLogsTab';
import { RunSignalsTab } from './RunSignalsTab';
import { RunTimelineTab } from './RunTimelineTab';
import { useToast } from '@/hooks/useToast';
import { api } from '@/utils/api';

export default function PlanRunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const id = Number(runId);
  const toast = useToast();
  const [activeTab, setActiveTab] = useState('overview');

  const { runQ, devicesQ, watcherQ, timelineQ, abortMut, retryDispatchMut } =
    usePlanRunDetailData(id, {
      deviceStatusFilter: 'all',
      deviceHostFilter: 'all',
      watcherTimeScope: 'all',
    });

  if (!id || Number.isNaN(id)) {
    return <div>无效 PlanRun ID</div>;
  }

  const run = runQ.data;
  const isTerminal = isPlanRunTerminal(run?.status);

  return (
    <PageContainer>
      <PageHeaderV2
        title={run?.plan_name || `Plan Run #${id}`}
        breadcrumbs={[
          { label: 'Plan Runs', path: '/execution/plan-runs' },
          { label: `#${id}` },
        ]}
        description={<PlanRunMeta run={run} />}
        actions={
          <>
            <Button
              variant="outline"
              size="sm"
              data-testid="plan-run-abort-btn"
              onClick={() => abortMut.mutate('aborted_by_user')}
              disabled={abortMut.isPending || isTerminal}
            >
              取消
            </Button>
            <Button
              size="sm"
              data-testid="plan-run-retry-btn"
              onClick={() => retryDispatchMut.mutate()}
              disabled={retryDispatchMut.isPending || run?.status === 'RUNNING'}
            >
              重试
            </Button>
            <Button
              variant="outline"
              size="sm"
              data-testid="plan-run-export-btn"
              onClick={async () => {
                try {
                  const blob = await api.planRuns.exportReport(id, 'markdown');
                  const url = URL.createObjectURL(blob);
                  const anchor = document.createElement('a');
                  anchor.href = url;
                  anchor.download = `plan-run-${id}-report.md`;
                  anchor.click();
                  URL.revokeObjectURL(url);
                  toast.success('报告已导出');
                } catch (err: unknown) {
                  const msg = err instanceof Error ? err.message : String(err);
                  toast.error(`导出失败: ${msg}`);
                }
              }}
            >
              下载报告
            </Button>
          </>
        }
      />

      <RunStatusBanner run={run} />

      <Tabs value={activeTab} onValueChange={setActiveTab} className="flex-1 flex flex-col min-h-0">
        <TabsList className="mx-4 mt-2 justify-start">
          <TabsTrigger value="overview">概览</TabsTrigger>
          <TabsTrigger value="devices">设备</TabsTrigger>
          <TabsTrigger value="artifacts">产物</TabsTrigger>
          <TabsTrigger value="logs">日志</TabsTrigger>
          <TabsTrigger value="signals">Signals</TabsTrigger>
          <TabsTrigger value="timeline">时间线</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="flex-1 overflow-auto m-0">
          <RunOverviewTab run={run} />
        </TabsContent>
        <TabsContent value="devices" className="flex-1 overflow-auto m-0">
          <RunDevicesTab
            runId={id}
            devices={devicesQ.data?.devices ?? []}
            isLoading={devicesQ.isLoading}
            error={devicesQ.error}
          />
        </TabsContent>
        <TabsContent value="artifacts" className="flex-1 overflow-auto m-0">
          <RunArtifactsTab runId={id} />
        </TabsContent>
        <TabsContent value="logs" className="flex-1 overflow-auto m-0">
          <RunLogsTab runId={id} />
        </TabsContent>
        <TabsContent value="signals" className="flex-1 overflow-auto m-0">
          <RunSignalsTab
            runId={id}
            summary={watcherQ.data}
            isLoading={watcherQ.isLoading}
            isError={watcherQ.isError}
            onRefresh={() => watcherQ.refetch()}
          />
        </TabsContent>
        <TabsContent value="timeline" className="flex-1 overflow-auto m-0">
          <RunTimelineTab
            timeline={timelineQ.data}
            isLoading={timelineQ.isLoading}
            isError={timelineQ.isError}
          />
        </TabsContent>
      </Tabs>
    </PageContainer>
  );
}
