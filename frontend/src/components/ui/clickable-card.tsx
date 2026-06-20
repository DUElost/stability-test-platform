import { ReactNode, KeyboardEvent } from 'react';
import { Card } from '@/components/ui/card';
import { cn } from '@/lib/utils';

interface ClickableCardProps {
  onClick: () => void;
  children: ReactNode;
  className?: string;
  ariaLabel?: string;
}

/**
 * 可点击卡片组件，支持键盘导航
 *
 * 自动处理：
 * - Enter / Space 键触发点击
 * - 键盘焦点样式
 * - 无障碍属性
 *
 * @example
 * <ClickableCard onClick={() => navigate('/hosts')} ariaLabel="查看主机列表">
 *   <StatCard title="主机" value={10} />
 * </ClickableCard>
 */
export function ClickableCard({
  onClick,
  children,
  className,
  ariaLabel,
}: ClickableCardProps) {
  const handleKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick();
    }
  };

  return (
    <Card
      className={cn(
        'cursor-pointer hover:shadow-md transition-shadow',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
        className
      )}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role="button"
      aria-label={ariaLabel}
    >
      {children}
    </Card>
  );
}
