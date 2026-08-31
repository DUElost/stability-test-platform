import { useId, type ReactNode } from 'react';
import { cn } from '@/lib/utils';
import { SEGMENTED, TEXT, tabLinkClass } from '@/design-system/tokens';

export interface StateTabItem {
  key: string;
  label: string;
  /** 对应 tabpanel 的 DOM id；消费方以同 id 渲染 role="tabpanel"（可选） */
  panelId?: string;
  testId?: string;
}

interface StateTabsProps {
  items: StateTabItem[];
  activeKey: string;
  onChange: (key: string) => void;
  /** underline：下划线（tabLinkClass，IssueTracker 风格）；segmented：pill（Notifications 风格） */
  variant?: 'underline' | 'segmented';
  /** 左侧标题（可选） */
  title?: ReactNode;
  className?: string;
  ariaLabel?: string;
  testId?: string;
}

/**
 * 状态型页签（无路由，setState 切换）。
 *
 * C2 收敛点：带 role="tablist"/tab + aria-selected/aria-controls 无障碍语义，
 * 两种视觉变体（underline / segmented），替代各页手写按钮。
 * 消费方渲染内容区时用 role="tabpanel" + item.panelId 关联。
 */
export function StateTabs({
  items,
  activeKey,
  onChange,
  variant = 'underline',
  title,
  className,
  ariaLabel = '视图切换',
  testId,
}: StateTabsProps) {
  const uid = useId();

  const btnClass = (active: boolean) =>
    variant === 'segmented'
      ? cn(
          'px-4 py-2 text-sm rounded-md transition-colors',
          active ? SEGMENTED.toggleActive : SEGMENTED.toggleIdle,
        )
      : tabLinkClass(active);

  const trackClass =
    variant === 'segmented'
      ? cn(SEGMENTED.track, 'w-fit text-sm bg-muted border-0 p-1')
      : 'flex overflow-x-auto scrollbar-hide -mb-px items-center border-b border-border';

  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      data-testid={testId}
      className={cn(trackClass, className)}
    >
      {title && (
        <span className={cn('mr-2 text-sm font-semibold', TEXT.heading)}>{title}</span>
      )}
      {items.map((item) => (
        <button
          key={item.key}
          id={`${uid}-tab-${item.key}`}
          type="button"
          role="tab"
          aria-selected={activeKey === item.key}
          aria-controls={item.panelId}
          data-testid={item.testId}
          className={btnClass(activeKey === item.key)}
          onClick={() => onChange(item.key)}
        >
          {item.label}
        </button>
      ))}
    </div>
  );
}
