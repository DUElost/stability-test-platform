import {
  forwardRef,
  type HTMLAttributes,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import { TableRow } from '@/components/ui/table';
import { cn } from '@/lib/utils';

interface ClickableRowProps
  extends Omit<HTMLAttributes<HTMLTableRowElement>, 'onClick' | 'children' | 'role'> {
  onClick: () => void;
  children: ReactNode;
  /** 无障碍语义：默认 link（跳转），可改 button 或保留原生 row */
  role?: 'link' | 'button' | 'row';
}

/**
 * 可点击表格行（C3 收敛点）。
 *
 * Enter / Space 触发点击 + role/tabIndex + 焦点样式，
 * 解决"整行 onClick 但键盘不可达"的问题。用于 Results 表格行 / 运行记录行 / 设备行等。
 */
export const ClickableRow = forwardRef<HTMLTableRowElement, ClickableRowProps>(
  function ClickableRow(
    { onClick, children, className, role = 'link', onKeyDown, ...rest },
    ref,
  ) {
    const handleKeyDown = (e: KeyboardEvent<HTMLTableRowElement>) => {
      onKeyDown?.(e);
      if (e.defaultPrevented || e.target !== e.currentTarget) return;
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        onClick();
      }
    };

    return (
      <TableRow
        ref={ref}
        className={cn(
          'cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2',
          className,
        )}
        onClick={onClick}
        onKeyDown={handleKeyDown}
        tabIndex={0}
        role={role}
        {...rest}
      >
        {children}
      </TableRow>
    );
  },
);
