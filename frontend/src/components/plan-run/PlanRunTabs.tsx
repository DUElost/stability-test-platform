import { UnderlineTabs } from '@/components/ui/underline-tabs';

interface Props {
  runId: number;
  active: 'overview' | 'logs';
}

/** PlanRun 详情 / 日志 两个视图间的切换 tab(运行详情仪表盘 ╳ Patrol 日志流)。 */
export default function PlanRunTabs({ runId, active }: Props) {
  const base = `/execution/plan-runs/${runId}`;
  return (
    <UnderlineTabs
      testId="plan-run-tabs"
      title={`PlanRun #${runId}`}
      activeKey={active}
      items={[
        {
          key: 'overview',
          label: '概览',
          to: base,
          end: true,
          testId: 'plan-run-tab-overview',
        },
        {
          key: 'logs',
          label: '日志',
          to: `${base}/logs`,
          testId: 'plan-run-tab-logs',
        },
      ]}
    />
  );
}
