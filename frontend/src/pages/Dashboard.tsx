import { useMemo } from 'react';

import { useQuery } from '@tanstack/react-query';

import { PageContainer, PageHeader } from '../components/layout';

import { useRealtimeDashboard } from '../hooks/useRealtimeDashboard';

import { api } from '../utils/api';

import { WS_DASHBOARD_ENDPOINT } from '../config';

import { Card, CardContent } from '@/components/ui/card';

import { Skeleton } from '@/components/ui/skeleton';

import { Wifi, Clock, Server, Smartphone, AlertCircle, BarChart3, Zap } from 'lucide-react';

import { DeviceStatusChart, HostResourceChart, ActivityChart, CompletionTrendChart } from '@/components/charts';

import { useNavigate } from 'react-router-dom';



export default function Dashboard() {

  const navigate = useNavigate();



  const { data: summary, isLoading: summaryLoading, error: summaryError } = useQuery({

    queryKey: ['dashboard-summary'],

    queryFn: () => api.stats.dashboardSummary().then(res => res.data),

    refetchInterval: 10000,

  });



  const { lastUpdateTime } = useRealtimeDashboard(WS_DASHBOARD_ENDPOINT);



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



  const hostStats = summary?.hosts ?? {

    total: 0, online: 0, offline: 0, degraded: 0,

    avg_cpu_load: 0, avg_ram_usage: 0, avg_disk_usage: 0, online_rate: 0,

  };



  const stats = summary?.devices ?? {

    total: 0, idle: 0, testing: 0, offline: 0, error: 0, low_battery: 0, high_temp: 0,

  };



  const alerts = summary?.alerts ?? { total: 0, low_battery: 0, high_temp: 0, error: 0 };



  const alertsCount = alerts.total;



  const isLoading = summaryLoading;



  const deviceStatusData = useMemo(() => ({

    idle: stats.idle,

    testing: stats.testing,

    offline: stats.offline,

    error: stats.error,

  }), [stats]);



  const hostResourceData = useMemo(() => {

    return (summary?.host_resources ?? []).map(h => ({

      ip: h.ip,

      cpu_load: h.cpu_load,

      ram_usage: h.ram_usage,

      disk_usage: h.disk_usage,

    }));

  }, [summary?.host_resources]);



  if (summaryError) {

    return (

      <PageContainer>

        <PageHeader title="仪表盘" subtitle="系统运行状态总览" />

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

      </PageContainer>

    );

  }



  return (

    <PageContainer>

      {/* Page Header */}

      <PageHeader title="仪表盘" subtitle="系统运行状态总览" />



        {/* 数据更新时间 */}

        <div className="flex justify-end items-center text-sm">

          <div className="flex items-center gap-2 text-gray-400">

            <Clock size={14} />

            <span>更新于: {lastUpdateTime.toLocaleTimeString()}</span>

          </div>

        </div>



        {/* Stats Grid */}

        <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">

          {/* 主机统计 */}

          <Card

            className="p-4 cursor-pointer hover:shadow-md transition-shadow"

            onClick={() => navigate('/hosts')}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate('/hosts'); } }}

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

          </Card>



          {/* 设备统计 */}

          <Card

            className="p-4 cursor-pointer hover:shadow-md transition-shadow"

            onClick={() => navigate('/devices')}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate('/devices'); } }}

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

          </Card>



          {/* 测试中 */}

          <Card

            className="p-4 cursor-pointer hover:shadow-md transition-shadow"

            onClick={() => navigate('/execution/plan-runs')}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate('/execution/plan-runs'); } }}

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

          </Card>



          {/* 告警 */}

          <Card className="p-4 cursor-pointer hover:shadow-md transition-shadow"
            onClick={() => navigate('/notifications')}
            tabIndex={0}
            role="button"
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); navigate('/notifications'); } }}
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

          </Card>

        </div>



        {/* Alert Details */}

        {alertsCount > 0 && (

          <Card className="p-4">

            <div className="flex items-center gap-6">

              <div className="flex items-center gap-2">

                <AlertCircle className="w-5 h-5 text-red-500" />

                <span className="text-sm font-medium text-gray-700">告警详情</span>

              </div>

              <div className="flex items-center gap-4 text-xs">

                {alerts.error > 0 && (

                  <span className="flex items-center gap-1 text-red-600">

                    <span className="w-2 h-2 rounded-full bg-red-500" />

                    错误: {alerts.error}

                  </span>

                )}

                {alerts.low_battery > 0 && (

                  <span className="flex items-center gap-1 text-amber-600">

                    <span className="w-2 h-2 rounded-full bg-amber-500" />

                    低电量: {alerts.low_battery}

                  </span>

                )}

                {alerts.high_temp > 0 && (

                  <span className="flex items-center gap-1 text-orange-600">

                    <span className="w-2 h-2 rounded-full bg-orange-500" />

                    高温: {alerts.high_temp}

                  </span>

                )}

              </div>

            </div>

          </Card>

        )}



        {/* Charts Section */}

        <div>

          <div className="flex items-center gap-2 mb-4">

            <BarChart3 size={18} className="text-gray-600" />

            <h3 className="text-lg font-semibold text-gray-900">数据统计</h3>

          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">

            <Card className="p-4">

              <h4 className="text-sm font-medium text-gray-700 mb-3">设备状态分布</h4>

              {isLoading ? (

                <Skeleton className="h-[200px] w-full" />

              ) : (

                <DeviceStatusChart data={deviceStatusData} />

              )}

            </Card>



            <Card className="p-4">

              <h4 className="text-sm font-medium text-gray-700 mb-3">主机资源负载</h4>

              {isLoading ? (

                <Skeleton className="h-[200px] w-full" />

              ) : (

                <HostResourceChart hosts={hostResourceData} />

              )}

            </Card>



            <Card className="p-4">

              <h4 className="text-sm font-medium text-gray-700 mb-3">任务活动趋势 (24h)</h4>

              {activityLoading ? (

                <Skeleton className="h-[200px] w-full" />

              ) : (

                <ActivityChart data={activityData?.points ?? []} />

              )}

            </Card>



            <Card className="p-4">

              <h4 className="text-sm font-medium text-gray-700 mb-3">完成趋势 (7d)</h4>

              {trendLoading ? (

                <Skeleton className="h-[200px] w-full" />

              ) : (

                <CompletionTrendChart data={trendData?.points ?? []} />

              )}

            </Card>

          </div>

        </div>



        {/* Online Rate */}

        <Card className="p-4">

          <div className="flex items-center justify-between">

            <div className="flex items-center gap-2">

              <Wifi className="w-5 h-5 text-gray-600" />

              <span className="text-sm font-medium text-gray-700">在线率</span>

            </div>

            <span className="text-xl font-bold text-emerald-600">

              {isLoading ? <Skeleton className="h-7 w-16" /> : `${(hostStats.online_rate * 100).toFixed(1)}%`}

            </span>

          </div>

        </Card>

    </PageContainer>

  );

}

