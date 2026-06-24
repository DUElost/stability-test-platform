import { Link } from 'react-router-dom';
import { Button } from '@/components/ui/button';
import { SURFACE, TEXT } from '@/design-system/tokens';

export default function NotFoundPage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
      <div className={`w-20 h-20 mb-6 rounded-full flex items-center justify-center ${SURFACE.subtle}`}>
        <span className={`text-3xl font-bold ${TEXT.caption}`}>404</span>
      </div>
      <h1 className={`text-2xl font-semibold mb-2 ${TEXT.heading}`}>页面未找到</h1>
      <p className={`text-sm mb-6 ${TEXT.subtitle}`}>您访问的页面不存在或已被移除。</p>
      <Button asChild>
        <Link to="/">返回首页</Link>
      </Button>
    </div>
  );
}
