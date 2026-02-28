import { Settings, Database, Globe, Bell } from 'lucide-react';

export default function SettingsPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">系统设置</h2>
        <p className="text-sm text-gray-400">管理平台全局配置</p>
      </div>

      <div className="grid gap-4 max-w-2xl">
        {/* 通用设置 */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Settings className="w-5 h-5 text-gray-400" />
            <h3 className="text-lg font-medium text-gray-900">通用设置</h3>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">平台名称</p>
                <p className="text-xs text-gray-400">显示在页面标题和导航栏</p>
              </div>
              <span className="text-sm text-gray-500">稳定性测试平台</span>
            </div>
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">时区</p>
                <p className="text-xs text-gray-400">影响日志和任务的时间显示</p>
              </div>
              <span className="text-sm text-gray-500">Asia/Shanghai (UTC+8)</span>
            </div>
          </div>
        </div>

        {/* 数据库连接 */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Database className="w-5 h-5 text-gray-400" />
            <h3 className="text-lg font-medium text-gray-900">数据库</h3>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">数据库类型</p>
              </div>
              <span className="text-sm text-gray-500">PostgreSQL</span>
            </div>
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">连接状态</p>
              </div>
              <span className="inline-flex items-center gap-1.5 text-sm text-green-600">
                <span className="w-1.5 h-1.5 rounded-full bg-green-500" />
                已连接
              </span>
            </div>
          </div>
        </div>

        {/* Agent 配置 */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Globe className="w-5 h-5 text-gray-400" />
            <h3 className="text-lg font-medium text-gray-900">Agent 配置</h3>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">心跳间隔</p>
                <p className="text-xs text-gray-400">Agent 上报心跳的时间间隔</p>
              </div>
              <span className="text-sm text-gray-500">30 秒</span>
            </div>
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">离线判定阈值</p>
                <p className="text-xs text-gray-400">超过该时间未收到心跳则判定离线</p>
              </div>
              <span className="text-sm text-gray-500">90 秒</span>
            </div>
          </div>
        </div>

        {/* 通知设置 */}
        <div className="bg-white rounded-xl border border-gray-200 p-6">
          <div className="flex items-center gap-2 mb-4">
            <Bell className="w-5 h-5 text-gray-400" />
            <h3 className="text-lg font-medium text-gray-900">通知设置</h3>
          </div>
          <div className="space-y-4">
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">设备离线通知</p>
                <p className="text-xs text-gray-400">设备离线时发送通知</p>
              </div>
              <span className="text-sm text-gray-500">已启用</span>
            </div>
            <div className="flex items-center justify-between py-3 border-b border-gray-100 last:border-0">
              <div>
                <p className="text-sm font-medium text-gray-700">任务失败通知</p>
                <p className="text-xs text-gray-400">任务执行失败时发送通知</p>
              </div>
              <span className="text-sm text-gray-500">已启用</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}