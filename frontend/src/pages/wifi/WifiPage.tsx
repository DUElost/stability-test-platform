import { CleanCard } from '@/components/ui/clean-card';

export default function WifiPage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">WiFi管理</h2>
        <p className="text-sm text-gray-400">配置和管理测试设备的WiFi网络</p>
      </div>

      <CleanCard className="p-8 text-center">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-50 flex items-center justify-center">
          <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M8.111 16.404a5.5 5.5 0 017.778 0M12 20h.01m-7.08-7.071c3.904-3.905 10.236-3.905 14.141 0M1.394 9.393c5.857-5.857 15.355-5.857 21.213 0" />
          </svg>
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">WiFi管理</h3>
        <p className="text-sm text-gray-400 mb-4">WiFi管理功能正在开发中...</p>
      </CleanCard>
    </div>
  );
}
