import React from 'react';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import { TEXT } from '@/design-system/tokens';

interface DataErrorStateProps {
  title?: string;
  description?: string;
  onRetry?: () => void;
  className?: string;
}

export const DataErrorState: React.FC<DataErrorStateProps> = ({
  title = '加载失败',
  description = '请检查网络连接或稍后重试',
  onRetry,
  className,
}) => {
  return (
    <div className={cn('flex flex-col items-center justify-center py-12 text-center', className)}>
      <AlertCircle className={cn('w-10 h-10 mb-3', TEXT.destructive)} />
      <h3 className={cn('text-sm font-medium', TEXT.heading)}>{title}</h3>
      <p className={cn('mt-1 text-sm max-w-sm', TEXT.subtitle)}>{description}</p>
      {onRetry && (
        <Button variant="outline" size="sm" className="mt-4" onClick={onRetry}>
          重试
        </Button>
      )}
    </div>
  );
};

export default DataErrorState;
