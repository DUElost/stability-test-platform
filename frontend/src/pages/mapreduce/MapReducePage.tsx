import { CleanCard } from '@/components/ui/clean-card';

export default function MapReducePage() {
  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">Map-Reduce</h2>
        <p className="text-sm text-gray-400">分布式日志处理与数据分析</p>
      </div>

      <CleanCard className="p-8 text-center">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-gray-50 flex items-center justify-center">
          <svg className="w-8 h-8 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
          </svg>
        </div>
        <h3 className="text-lg font-medium text-gray-900 mb-2">Map-Reduce</h3>
        <p className="text-sm text-gray-400 mb-4">分布式日志处理功能正在开发中...</p>
      </CleanCard>
    </div>
  );
}
