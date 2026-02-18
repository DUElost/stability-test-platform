import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { PageContainer } from '../components/layout';
import { useRealtimeDashboard } from '../hooks/useRealtimeDashboard';
import { api } from '../utils/api';
import { WS_DASHBOARD_ENDPOINT } from '../config';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import { Wifi, WifiOff, Clock, Server, Smartphone, AlertCircle, BarChart3, Gauge, Cpu, HardDrive, MemoryStick, Zap } from 'lucide-react';
import { DeviceStatusChart, HostResourceChart, ActivityChart, CompletionTrendChart } from '@/components/charts';
import { type DeviceDTO, mapDeviceToViewModel } from '@/mappers';
import { CleanCard } from '../components/ui/clean-card';
import { useNavigate } from 'react-router-dom';

const deviceStatusMap: Record<string, 'idle' | 'testing' | 'offline' | 'error'> = {
  'ONLINE': 'idle',
  'BUSY': 'testing',
  'OFFLINE': 'offline',
  'ERROR': 'error'
};

function toComponentDevice(device: DeviceDTO) {
  const viewModel = mapDeviceToViewModel(device);
  return {
    serial: viewModel.serial,
    model: viewModel.model || 'Unknown',
    status: deviceStatusMap[viewModel.status] || 'offline',
    battery_level: viewModel.battery_level ?? 0,
    temperature: viewModel.temperature ?? 0,
    network_latency: viewModel.network_latency ?? null,
  };
}

interface Device {
  serial: string;
  model: string;
  status: 'idle' | 'testing' | 'offline' | 'error';
  battery_level: number;
  temperature: number;
  network_latency: number | null;
}

export default function Dashboard() {
  const navigate = useNavigate();

  const { data: hosts, isLoading: hostsLoading, error: hostsError } = useQuery({
    queryKey: ['hosts'],
    queryFn: () => api.hosts.list(0, 200).then(res => res.data.items),
    refetchInterval: 5000,
  });

  const {
    devices: rawDevices,
    isConnected: wsConnected,
    lastUpdateTime,
    isLoading: devicesLoading,
    isError: devicesError
  } = useRealtimeDashboard(WS_DASHBOARD_ENDPOINT);

  // Stats queries
  const { data: activityData, isLoading: activityLoading } = useQuery({
    queryKey: ['stats-activity'],
    queryFn: () => api.stats.activity(24).then(res => res.data),
    refetchInterval: 60000,
  });

  const { data: trendData, isLoading: trendLoading } = useQuery({
    queryKey: ['stats-completion-trend'],
    queryFn: () => api.stats.completionTrend(7).then(res => res.data),
    refetchInterval: 60000,
  });

  const devices = useMemo(() => {
    if (!rawDevices || rawDevices.length === 0) return [];
    return rawDevices.map(toComponentDevice);
  }, [rawDevices]);

  // 计算主机统计
  const hostStats = useMemo(() => {
    if (!hosts) return { total: 0, online: 0, offline: 0, degraded: 0 };
    const hostArray = hosts as any[];
    return {
      total: hostArray.length,
      online: hostArray.filter((h: any) => h.status === 'ONLINE').length,
      offline: hostArray.filter((h: any) => h.status === 'OFFLINE').length,
      degraded: hostArray.filter((h: any) => h.status === 'DEGRADED').length,
    };
  }, [hosts]);

  // 计算设备统计
  const stats = useMemo(() => ({
    total: devices.length,
    idle: devices.filter((d: Device) => d.status === 'idle').length,
    offline: devices.filter((d: Device) => d.status === 'offline').length,
    testing: devices.filter((d: Device) => d.status === 'testing').length,
    error: devices.filter((d: Device) => d.status === 'error').length,
    lowBattery: devices.filter((d: Device) => d.battery_level < 20).length,
    highTemp: devices.filter((d: Device) => d.temperature > 45).length,
  }), [devices]);

  // 计算告警数量
  const alertsCount = stats.error + stats.lowBattery + stats.highTemp;

  const isLoading = hostsLoading || devicesLoading;
  const hasError = hostsError || devicesError;

  // 准备图表数据
  const deviceStatusData = useMemo(() => ({
    idle: stats.idle,
    testing: stats.testing,
    offline: stats.offline,
    error: stats.error,
  }), [stats]);

  const hostResourceData = useMemo(() => {
    if (!hosts) return [];
    return (hosts as any[]).map((host) => ({
      ip: host.ip,
      cpu_load: (host.extra?.cpu_load as number) || 0,
      ram_usage: (host.extra?.ram_usage as number) || 0,
      disk_usage: (host.extra?.disk_usage as { usage_percent?: number })?.usage_percent || 0,
    }));
  }, [hosts]);

  if (hasError) {
    return (
      <PageContainer>
        <div className="space-y-6">
          <div>
            <h2 className="text-2xl font-semibold text-gray-900 mb-1">仪表盘</h2>
            <p className="text-sm text-gray-400">系统运行状态总览</p>
          </div>
          <Card className="border-red-200 bg-red-50">
            <CardContent className="p-6">
              <div className="flex items-center gap-3 text-red-600">
                <AlertCircle size={24} />
                <div>
                  <h3 className="font-semibold">数据加载失败</h3>
                  <p className="text-sm text-red-600/80">请检查后端服务连接。</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <div className="space-y-6">
        {/* Page Header */}
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">仪表盘</h2>
          <p className="text-sm text-gray-400">系统运行状态总览</p>
        </div>

        {/* Connection Status Bar */}
        <div className="flex justify-between items-center text-sm">
          <div className="flex items-center gap-2">
            <Badge
              variant={wsConnected ? 'default' : 'destructive'}
              className="gap-1.5 bg-emerald-50 text-emerald-600 border-emerald-200 hover:bg-emerald-50"
            >
              {wsConnected ? <Wifi size={12} /> : <WifiOff size={12} />}
              {wsConnected ? '实时连接' : '已断开'}
            </Badge>
          </div>
          <div className="flex items-center gap-2 text-gray-400">
            <Clock size={14} />
            <span>更新于: {lastUpdateTime.toLocaleTimeString()}</span>
          </div>
        </div>

        {/* Stats Grid - 简洁版 */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          {/* 主机统计 */}
          <CleanCard
            className="p-4 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => navigate('/hosts')}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wider">主机总数</p>
                <div className="flex items-baseline gap-1 mt-1">
                  {isLoading ? <Skeleton className="h-8 w-12" /> : (
                    <>
                      <span className="text-2xl font-bold text-gray-900">{hostStats.total}</span>
                      <span className="text-xs text-gray-400">
                        (在线{hostStats.online})
                      </span>
                    </>
                  )}
                </div>
              </div>
              <div className="w-12 h-12 rounded-xl bg-gray-50 flex items-center justify-center">
                <Server className="w-6 h-6 text-gray-600" />
              </div>
            </div>
          </CleanCard>

          {/* 设备统计 */}
          <CleanCard
            className="p-4 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => navigate('/devices')}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wider">设备总数</p>
                <div className="flex items-baseline gap-1 mt-1">
                  {isLoading ? <Skeleton className="h-8 w-12" /> : (
                    <>
                      <span className="text-2xl font-bold text-gray-900">{stats.total}</span>
                      <span className="text-xs text-gray-400">
                        (空闲{stats.idle})
                      </span>
                    </>
                  )}
                </div>
              </div>
              <div className="w-12 h-12 rounded-xl bg-emerald-50 flex items-center justify-center">
                <Smartphone className="w-6 h-6 text-emerald-600" />
              </div>
            </div>
          </CleanCard>

          {/* 测试中 */}
          <CleanCard
            className="p-4 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => navigate('/tasks')}
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wider">测试中</p>
                <div className="flex items-baseline gap-1 mt-1">
                  {isLoading ? <Skeleton className="h-8 w-12" /> : (
                    <span className="text-2xl font-bold text-blue-600">{stats.testing}</span>
                  )}
                </div>
              </div>
              <div className="w-12 h-12 rounded-xl bg-blue-50 flex items-center justify-center">
                <Zap className="w-6 h-6 text-blue-600" />
              </div>
            </div>
          </CleanCard>

          {/* 告警 */}
          <CleanCard
            className="p-4 cursor-pointer hover:shadow-md transition-shadow"
          >
            <div className="flex items-center justify-between">
              <div>
                <p className="text-xs text-gray-500 uppercase tracking-wider">告警</p>
                <div className="flex items-baseline gap-1 mt-1">
                  {isLoading ? <Skeleton className="h-8 w-12" /> : (
                    <span className={`text-2xl font-bold ${alertsCount > 0 ? 'text-red-600' : 'text-emerald-600'}`}>
                      {alertsCount > 0 ? alertsCount : '无'}
                    </span>
                  )}
                </div>
              </div>
              <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${alertsCount > 0 ? 'bg-red-50' : 'bg-emerald-50'}`}>
                <AlertCircle className={`w-6 h-6 ${alertsCount > 0 ? 'text-red-600' : 'text-emerald-600'}`} />
              </div>
            </div>
          </CleanCard>
        </div>

        {/* Alert Details - 当有告警时显示 */}
        {alertsCount > 0 && (
          <CleanCard className="p-4">
            <div className="flex items-center gap-6">
              <div className="flex items-center gap-2">
                <AlertCircle className="w-5 h-5 text-red-500" />
                <span className="text-sm font-medium text-gray-700">告警详情</span>
              </div>
              <div className="flex items-center gap-4 text-xs">
                {stats.error > 0 && (
                  <span className="flex items-center gap-1 text-red-600">
                    <span className="w-2 h-2 rounded-full bg-red-500" />
                    错误: {stats.error}
                  </span>
                )}
                {stats.lowBattery > 0 && (
                  <span className="flex items-center gap-1 text-amber-600">
                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                    低电量: {stats.lowBattery}
                  </span>
                )}
                {stats.highTemp > 0 && (
                  <span className="flex items-center gap-1 text-orange-600">
                    <span className="w-2 h-2 rounded-full bg-orange-500" />
                    高温: {stats.highTemp}
                  </span>
                )}
              </div>
            </div>
          </CleanCard>
        )}

        {/* Charts Section */}
        <div>
          <div className="flex items-center gap-2 mb-4">
            <BarChart3 size={18} className="text-gray-600" />
            <h3 className="text-lg font-semibold text-gray-900">数据统计</h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <CleanCard className="p-4">
              <h4 className="text-sm font-medium text-gray-700 mb-3">设备状态分布</h4>
              {isLoading ? <Skeleton className="h-40" /> : <DeviceStatusChart data={deviceStatusData} />}
            </CleanCard>
            <CleanCard className="p-4">
              <h4 className="text-sm font-medium text-gray-700 mb-3">主机资源概览</h4>
              {isLoading ? <Skeleton className="h-40" /> : <HostResourceChart hosts={hostResourceData} />}
            </CleanCard>
            <CleanCard className="p-4">
              <h4 className="text-sm font-medium text-gray-700 mb-3">活动趋势 (24h)</h4>
              <ActivityChart data={activityData?.points} isLoading={activityLoading} />
            </CleanCard>
            <CleanCard className="p-4">
              <h4 className="text-sm font-medium text-gray-700 mb-3">完成趋势 (7天)</h4>
              <CompletionTrendChart data={trendData?.points} isLoading={trendLoading} />
            </CleanCard>
          </div>
        </div>

        {/* Quick Info Cards */}
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
          <CleanCard className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
                <Cpu className="w-5 h-5 text-gray-500" />
              </div>
              <div>
                <p className="text-xs text-gray-500">主机CPU平均</p>
                <p className="text-lg font-semibold text-gray-900">
                  {hostResourceData.length > 0
                    ? `${(hostResourceData.reduce((sum, h) => sum + h.cpu_load, 0) / hostResourceData.length).toFixed(1)}%`
                    : '-'}
                </p>
              </div>
            </div>
          </CleanCard>
          <CleanCard className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
                <MemoryStick className="w-5 h-5 text-gray-500" />
              </div>
              <div>
                <p className="text-xs text-gray-500">主机内存平均</p>
                <p className="text-lg font-semibold text-gray-900">
                  {hostResourceData.length > 0
                    ? `${(hostResourceData.reduce((sum, h) => sum + h.ram_usage, 0) / hostResourceData.length).toFixed(1)}%`
                    : '-'}
                </p>
              </div>
            </div>
          </CleanCard>
          <CleanCard className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
                <HardDrive className="w-5 h-5 text-gray-500" />
              </div>
              <div>
                <p className="text-xs text-gray-500">主机磁盘平均</p>
                <p className="text-lg font-semibold text-gray-900">
                  {hostResourceData.length > 0
                    ? `${(hostResourceData.reduce((sum, h) => sum + h.disk_usage, 0) / hostResourceData.length).toFixed(1)}%`
                    : '-'}
                </p>
              </div>
            </div>
          </CleanCard>
          <CleanCard className="p-4">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 rounded-lg bg-gray-50 flex items-center justify-center">
                <Gauge className="w-5 h-5 text-gray-500" />
              </div>
              <div>
                <p className="text-xs text-gray-500">在线率</p>
                <p className="text-lg font-semibold text-gray-900">
                  {stats.total > 0
                    ? `${((stats.idle + stats.testing) / stats.total * 100).toFixed(1)}%`
                    : '-'}
                </p>
              </div>
            </div>
          </CleanCard>
        </div>
      </div>
    </PageContainer>
  );
}
