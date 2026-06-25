import { Settings, Database, Globe, Bell } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { PANEL, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';

const rowDivider = 'flex items-center justify-between py-3 border-b border-border last:border-0';

export default function SettingsPage() {
  return (
    <PageContainer width="narrow">
      <PageHeader title="系统设置" subtitle="管理平台全局配置" />

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
              <span className={cn('text-sm', TEXT.subtitle)}>稳定性测试平台</span>
            </div>
            <div className={rowDivider}>
              <div>
                <p className={cn('text-sm font-medium', TEXT.body)}>时区</p>
                <p className={cn('text-xs', TEXT.subtitle)}>影响日志和任务的时间显示</p>
              </div>
              <span className={cn('text-sm', TEXT.subtitle)}>Asia/Shanghai (UTC+8)</span>
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
              <span className={cn('text-sm', TEXT.subtitle)}>PostgreSQL</span>
            </div>
            <div className={rowDivider}>
              <div>
                <p className={cn('text-sm font-medium', TEXT.body)}>连接状态</p>
              </div>
              <span className="inline-flex items-center gap-1.5 text-sm text-success">
                <span className="w-1.5 h-1.5 rounded-full bg-success" />
                已连接
              </span>
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
              <span className={cn('text-sm', TEXT.subtitle)}>30 秒</span>
            </div>
            <div className={rowDivider}>
              <div>
                <p className={cn('text-sm font-medium', TEXT.body)}>离线判定阈值</p>
                <p className={cn('text-xs', TEXT.subtitle)}>超过该时间未收到心跳则判定离线</p>
              </div>
              <span className={cn('text-sm', TEXT.subtitle)}>90 秒</span>
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
                <p className={cn('text-xs', TEXT.subtitle)}>设备离线时发送通知</p>
              </div>
              <span className={cn('text-sm', TEXT.subtitle)}>已启用</span>
            </div>
            <div className={rowDivider}>
              <div>
                <p className={cn('text-sm font-medium', TEXT.body)}>任务失败通知</p>
                <p className={cn('text-xs', TEXT.subtitle)}>任务执行失败时发送通知</p>
              </div>
              <span className={cn('text-sm', TEXT.subtitle)}>已启用</span>
            </div>
          </div>
        </div>
      </div>
    </PageContainer>
  );
}
