import { type KeyboardEvent, type ReactNode } from 'react';
import { TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';

interface ClickableRowProps {
  onClick: () => void;
  children: ReactNode;
  className?: string;
  /** 无障碍语义：默认 link（跳转），可改 button */
  role?: 'link' | 'button';
}

/**
 * 可点击表格行（C3 收敛点）。
 *
 * 与 ClickableCard 同思路：Enter / Space 触发点击 + role/tabIndex + 焦点样式，
 * 解决"整行 onClick 但键盘不可达"的问题。用于 Results 表格行 / 运行记录行等。
 */
export function ClickableRow({ onClick, children, className, role = 'link' }: ClickableRowProps) {
  const handleKeyDown = (e: KeyboardEvent<HTMLTableRowElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onClick();
    }
  };

  return (
    <TableRow
      className={cn(
        'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
        className,
      )}
      onClick={onClick}
      onKeyDown={handleKeyDown}
      tabIndex={0}
      role={role}
    >
      {children}
    </TableRow>
  );
}
