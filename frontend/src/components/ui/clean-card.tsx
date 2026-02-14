import { cn } from '@/lib/utils';

interface CleanCardProps {
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
}

/**
 * 极简纯净风卡片 - 源自 web 样板设计
 */
export function CleanCard({ children, className = '', onClick }: CleanCardProps) {
  return (
    <div
      className={cn(
        'bg-white rounded-xl shadow-[0_1px_3px_rgba(0,0,0,0.04)] border border-gray-100',
        onClick && 'cursor-pointer',
        className
      )}
      onClick={onClick}
    >
      {children}
    </div>
  );
}
