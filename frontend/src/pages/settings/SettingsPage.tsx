import { useQuery } from '@tanstack/react-query';
import { Settings, Database, Globe, Bell } from 'lucide-react';
import { api } from '@/utils/api';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

const rowDivider = 'flex items-center justify-between py-3 border-b border-border last:border-0';

function databaseTypeLabel(raw: string): string {
  if (raw === 'postgresql') return 'PostgreSQL';
  if (raw === 'sqlite') return 'SQLite';
  return raw;
}

export default function SettingsPage() {
  const { data: s, isLoading, isError, refetch } = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.settings.get(),
  });

  return (
    <PageContainer width="form">
      <PageHeader title="系统设置" subtitle="管理平台全局配置" />

      {isLoading ? (
        <div className="grid gap-4">
          <PageSkeleton.Block size="lg" />
          <PageSkeleton.Block size="lg" />
          <PageSkeleton.Block size="lg" />
          <PageSkeleton.Block size="lg" />
        </div>
      ) : isError || !s ? (
        <InlineError
          message="系统设置加载失败：无法从服务端读取运行时配置，请确认后端服务可用后重试。"
          onRetry={refetch}
        />
      ) : (
        <div className="grid gap-4">
          {/* 通用设置 */}
          <div className={cn(PANEL.root, 'p-6')}>
            <div className="flex items-center gap-2 mb-4">
              <Settings className={cn('w-5 h-5', TEXT.subtitle)} />
              <h3 className={cn('text-lg font-medium', TEXT.heading)}>通用设置</h3>
            </div>
            <div className="space-y-4">
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>平台名称</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>显示在页面标题和导航栏</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>{s.platform_name}</span>
              </div>
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>时区</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>影响日志和任务的时间显示</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>{s.timezone}</span>
              </div>
            </div>
          </div>

          {/* 数据库连接 */}
          <div className={cn(PANEL.root, 'p-6')}>
            <div className="flex items-center gap-2 mb-4">
              <Database className={cn('w-5 h-5', TEXT.subtitle)} />
              <h3 className={cn('text-lg font-medium', TEXT.heading)}>数据库</h3>
            </div>
            <div className="space-y-4">
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>数据库类型</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>{databaseTypeLabel(s.database_type)}</span>
              </div>
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>连接状态</p>
                </div>
                {s.database_connected ? (
                  <span className="inline-flex items-center gap-1.5 text-sm text-success">
                    <span className="w-1.5 h-1.5 rounded-full bg-success" />
                    已连接
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-sm text-destructive">
                    <span className="w-1.5 h-1.5 rounded-full bg-destructive" />
                    已断开
                  </span>
                )}
              </div>
            </div>
          </div>

          {/* Agent 配置 */}
          <div className={cn(PANEL.root, 'p-6')}>
            <div className="flex items-center gap-2 mb-4">
              <Globe className={cn('w-5 h-5', TEXT.subtitle)} />
              <h3 className={cn('text-lg font-medium', TEXT.heading)}>Agent 配置</h3>
            </div>
            <div className="space-y-4">
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>心跳间隔</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>Agent 上报心跳的时间间隔</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>{s.agent_heartbeat_interval_seconds} 秒</span>
              </div>
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>离线判定阈值</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>超过该时间未收到心跳则判定离线</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>{s.offline_threshold_seconds} 秒</span>
              </div>
            </div>
          </div>

          {/* 通知设置 */}
          <div className={cn(PANEL.root, 'p-6')}>
            <div className="flex items-center gap-2 mb-4">
              <Bell className={cn('w-5 h-5', TEXT.subtitle)} />
              <h3 className={cn('text-lg font-medium', TEXT.heading)}>通知设置</h3>
            </div>
            <div className="space-y-4">
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>设备离线通知</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>存在已启用的设备离线告警规则</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>
                  {s.device_offline_notification_enabled ? '已启用' : '未启用'}
                </span>
              </div>
              <div className={rowDivider}>
                <div>
                  <p className={cn('text-sm font-medium', TEXT.body)}>任务失败通知</p>
                  <p className={cn('text-xs', TEXT.subtitle)}>存在已启用的任务失败告警规则</p>
                </div>
                <span className={cn('text-sm', TEXT.subtitle)}>
                  {s.task_failure_notification_enabled ? '已启用' : '未启用'}
                </span>
              </div>
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
