import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import {
  AlertCircle,
  BarChart3,
  Clock,
  Server,
  Smartphone,
  Wifi,
  Zap,
} from 'lucide-react';
import { DeviceStatusChart, HostResourceChart, ActivityChart, CompletionTrendChart } from '@/components/charts';
import { DashboardStatCard } from '@/components/dashboard/DashboardStatCard';
import { PageContainer, PageHeader } from '../components/layout';
import { Card } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { InlineError } from '@/components/ui/error-state';
import { useRealtimeDashboard } from '../hooks/useRealtimeDashboard';
import { api } from '../utils/api';
import { WS_DASHBOARD_ENDPOINT } from '../config';
import { ENTITY_STATUS_COLORS } from '@/design-system/colors';
import { CHART_SECTION, STAT, TEXT } from '@/design-system/tokens';

export default function Dashboard() {
  const navigate = useNavigate();

  const { data: summary, isLoading: summaryLoading, error: summaryError } = useQuery({
    queryKey: ['dashboard-summary'],
    queryFn: () => api.stats.dashboardSummary().then((res) => res.data),
    refetchInterval: 10000,
  });

  const { lastUpdateTime } = useRealtimeDashboard(WS_DASHBOARD_ENDPOINT);

  const { data: activityData, isLoading: activityLoading } = useQuery({
    queryKey: ['stats-activity'],
    queryFn: () => api.stats.activity(24).then((res) => res.data),
    refetchInterval: 60000,
  });

  const { data: trendData, isLoading: trendLoading } = useQuery({
    queryKey: ['stats-completion-trend'],
    queryFn: () => api.stats.completionTrend(7).then((res) => res.data),
    refetchInterval: 60000,
  });

  const hostStats = summary?.hosts ?? {
    total: 0,
    online: 0,
    offline: 0,
    degraded: 0,
    avg_cpu_load: 0,
    avg_ram_usage: 0,
    avg_disk_usage: 0,
    online_rate: 0,
  };

  const stats = summary?.devices ?? {
    total: 0,
    idle: 0,
    testing: 0,
    offline: 0,
    error: 0,
    low_battery: 0,
    high_temp: 0,
  };

  const alerts = summary?.alerts ?? { total: 0, low_battery: 0, high_temp: 0, error: 0 };
  const alertsCount = alerts.total;
  const isLoading = summaryLoading;

  const deviceStatusData = useMemo(
    () => ({
      idle: stats.idle,
      testing: stats.testing,
      offline: stats.offline,
      error: stats.error,
    }),
    [stats],
  );

  const hostResourceData = useMemo(
    () =>
      (summary?.host_resources ?? []).map((h) => ({
        ip: h.ip,
        cpu_load: h.cpu_load,
        ram_usage: h.ram_usage,
        disk_usage: h.disk_usage,
      })),
    [summary?.host_resources],
  );

  if (summaryError) {
    return (
      <PageContainer>
        <PageHeader title="仪表盘" subtitle="系统运行状态总览" />
        <InlineError message="数据加载失败，请检查后端服务连接。" />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <PageHeader title="仪表盘" subtitle="系统运行状态总览" />

      <div className={`flex justify-end items-center text-sm ${TEXT.caption}`}>
        <Clock size={14} aria-hidden />
        <span className="ml-2">更新于: {lastUpdateTime.toLocaleTimeString()}</span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 gap-4">
        <DashboardStatCard
          label="主机总数"
          value={hostStats.total}
          suffix={`(在线${hostStats.online})`}
          loading={isLoading}
          icon={<Server className="w-6 h-6" />}
          iconWellClassName={STAT.iconWellMuted}
          onClick={() => navigate('/hosts')}
          ariaLabel="查看主机列表"
        />
        <DashboardStatCard
          label="设备总数"
          value={stats.total}
          suffix={`(空闲${stats.idle})`}
          loading={isLoading}
          icon={<Smartphone className={`w-6 h-6 ${ENTITY_STATUS_COLORS.device.idle}`} />}
          iconWellClassName={STAT.iconWellSuccess}
          onClick={() => navigate('/devices')}
          ariaLabel="查看设备列表"
        />
        <DashboardStatCard
          label="测试中"
          value={stats.testing}
          loading={isLoading}
          valueClassName={`text-2xl font-bold ${ENTITY_STATUS_COLORS.execution.running}`}
          icon={<Zap className={`w-6 h-6 ${ENTITY_STATUS_COLORS.execution.running}`} />}
          iconWellClassName={STAT.iconWellPrimary}
          onClick={() => navigate('/execution/plan-runs')}
          ariaLabel="查看执行记录"
        />
        <Link
            to="/notifications"
            className="block focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 rounded-xl"
            aria-label="查看告警通知"
          >
            <DashboardStatCard
              label="告警"
              value={alertsCount > 0 ? alertsCount : '无'}
              loading={isLoading}
              valueClassName={`text-2xl font-bold ${
                alertsCount > 0 ? ENTITY_STATUS_COLORS.alert.high : ENTITY_STATUS_COLORS.alert.none
              }`}
              icon={
                <AlertCircle
                  className={`w-6 h-6 ${
                    alertsCount > 0 ? ENTITY_STATUS_COLORS.alert.high : ENTITY_STATUS_COLORS.alert.none
                  }`}
                />
              }
              iconWellClassName={
                alertsCount > 0 ? STAT.iconWellDestructive : STAT.iconWellSuccess
              }
            />
          </Link>
      </div>

      {alertsCount > 0 && (
        <Card className="p-4">
          <div className="flex items-center gap-6 flex-wrap">
            <div className="flex items-center gap-2">
              <AlertCircle className={`w-5 h-5 ${ENTITY_STATUS_COLORS.alert.high}`} />
              <span className={`text-sm font-medium ${TEXT.heading}`}>告警详情</span>
            </div>
            <div className="flex items-center gap-4 text-xs">
              {alerts.error > 0 && (
                <span className={`flex items-center gap-1 ${ENTITY_STATUS_COLORS.alert.high}`}>
                  <span className="w-2 h-2 rounded-full bg-destructive" />
                  错误: {alerts.error}
                </span>
              )}
              {alerts.low_battery > 0 && (
                <span className={`flex items-center gap-1 ${ENTITY_STATUS_COLORS.alert.medium}`}>
                  <span className="w-2 h-2 rounded-full bg-warning" />
                  低电量: {alerts.low_battery}
                </span>
              )}
              {alerts.high_temp > 0 && (
                <span className={`flex items-center gap-1 ${ENTITY_STATUS_COLORS.alert.medium}`}>
                  <span className="w-2 h-2 rounded-full bg-warning" />
                  高温: {alerts.high_temp}
                </span>
              )}
            </div>
          </div>
        </Card>
      )}

      <div>
        <div className="flex items-center gap-2 mb-4">
          <BarChart3 size={18} className={CHART_SECTION.icon} />
          <h3 className={CHART_SECTION.title}>数据统计</h3>
        </div>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <Card className="p-4">
            <h4 className={`${CHART_SECTION.subtitle} mb-3`}>设备状态分布</h4>
            {isLoading ? <Skeleton className="h-[200px] w-full" /> : <DeviceStatusChart data={deviceStatusData} />}
          </Card>
          <Card className="p-4">
            <h4 className={`${CHART_SECTION.subtitle} mb-3`}>主机资源负载</h4>
            {isLoading ? <Skeleton className="h-[200px] w-full" /> : <HostResourceChart hosts={hostResourceData} />}
          </Card>
          <Card className="p-4">
            <h4 className={`${CHART_SECTION.subtitle} mb-3`}>任务活动趋势 (24h)</h4>
            {activityLoading ? (
              <Skeleton className="h-[200px] w-full" />
            ) : (
              <ActivityChart data={activityData?.points ?? []} />
            )}
          </Card>
          <Card className="p-4">
            <h4 className={`${CHART_SECTION.subtitle} mb-3`}>完成趋势 (7d)</h4>
            {trendLoading ? (
              <Skeleton className="h-[200px] w-full" />
            ) : (
              <CompletionTrendChart data={trendData?.points ?? []} />
            )}
          </Card>
        </div>
      </div>

      <Card className="p-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Wifi className={`w-5 h-5 ${CHART_SECTION.icon}`} />
            <span className={`text-sm font-medium ${TEXT.heading}`}>在线率</span>
          </div>
          <span className={`text-xl font-bold ${ENTITY_STATUS_COLORS.host.online}`}>
            {isLoading ? <Skeleton className="h-7 w-16" /> : `${(hostStats.online_rate * 100).toFixed(1)}%`}
          </span>
        </div>
      </Card>
    </PageContainer>
  );
}
