import { Link } from 'react-router-dom';

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div className="w-20 h-20 mb-6 rounded-full bg-gray-100 flex items-center justify-center">
        <span className="text-3xl font-bold text-gray-400">404</span>
      </div>
      <h1 className="text-2xl font-semibold text-gray-900 mb-2">页面未找到</h1>
      <p className="text-sm text-gray-500 mb-6">您访问的页面不存在或已被移除。</p>
      <Link
        to="/"
        className="inline-flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 transition-colors"
      >
        返回首页
      </Link>
    </div>
  );
}
