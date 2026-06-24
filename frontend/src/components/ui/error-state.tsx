import { ReactNode } from 'react';
import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { TEXT } from '@/design-system/tokens';

interface ErrorStateProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
  onRetry?: () => void;
}

/**
 * 统一的错误状态组件
 */
export function ErrorState({
  title = '出错了',
  description = '请稍后重试',
  action,
  icon,
  onRetry,
}: ErrorStateProps) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div className="w-16 h-16 mx-auto mb-4 rounded-full bg-destructive/10 flex items-center justify-center">
          {icon || <AlertCircle className="w-8 h-8 text-destructive" />}
        </div>
        <h3 className={`text-base font-medium ${TEXT.heading} mb-2`}>{title}</h3>
        <p className={`text-sm ${TEXT.subtitle} mb-6`}>{description}</p>
        {action || (onRetry && (
          <Button onClick={onRetry} variant="outline">
            <RefreshCw className="w-4 h-4 mr-2" />
            重试
          </Button>
        ))}
      </CardContent>
    </Card>
  );
}

/**
 * 内联错误提示（用于 Alert 风格）
 */
export function InlineError({
  message = '加载失败，请检查后端服务连接',
}: {
  message?: string;
}) {
  return (
    <div className="p-4 bg-destructive/10 text-destructive rounded-lg border border-destructive/20 flex items-start gap-3">
      <AlertCircle className="w-5 h-5 flex-shrink-0 mt-0.5" />
      <p className="text-sm">{message}</p>
    </div>
  );
}
