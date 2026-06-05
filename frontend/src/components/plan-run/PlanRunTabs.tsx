import { NavLink } from 'react-router-dom';

function tabCls(active: boolean): string {
  return `inline-flex items-center border-b-2 px-3 py-2 text-sm font-medium transition ${
    active
      ? 'border-blue-600 text-blue-700'
      : 'border-transparent text-gray-500 hover:text-gray-700'
  }`;
}

interface Props {
  runId: number;
  active: 'overview' | 'logs';
}

/** PlanRun 详情 / 日志 两个视图间的切换 tab(运行详情仪表盘 ╳ Patrol 日志流)。 */
export default function PlanRunTabs({ runId, active }: Props) {
  const base = `/execution/plan-runs/${runId}`;
  return (
    <div
      data-testid="plan-run-tabs"
      className="flex items-center gap-x-1"
    >
      <span className="mr-2 text-sm font-semibold text-gray-800">PlanRun #{runId}</span>
      <NavLink to={base} end data-testid="plan-run-tab-overview" className={tabCls(active === 'overview')}>
        概览
      </NavLink>
      <NavLink to={`${base}/logs`} data-testid="plan-run-tab-logs" className={tabCls(active === 'logs')}>
        日志
      </NavLink>
    </div>
  );
}
