import { ReactNode } from 'react';
import { NavLink } from 'react-router-dom';
import { cn } from '@/lib/utils';
import { tabLinkClass, TEXT } from '@/design-system/tokens';

export interface UnderlineTabItem {
  key: string;
  label: string;
  to: string;
  end?: boolean;
  testId?: string;
}

interface UnderlineTabsProps {
  items: UnderlineTabItem[];
  activeKey: string;
  /** 左侧标题（可选） */
  title?: ReactNode;
  className?: string;
  testId?: string;
}

/**
 * 统一下划线 Tab 导航 — 用于 PlanRun 详情 / 日志等同层视图切换。
 */
export function UnderlineTabs({
  items,
  activeKey,
  title,
  className,
  testId = 'underline-tabs',
}: UnderlineTabsProps) {
  return (
    <div data-testid={testId} className={cn('flex items-center gap-x-1', className)}>
      {title && (
        <span className={cn('mr-2 text-sm font-semibold', TEXT.heading)}>{title}</span>
      )}
      {items.map((item) => (
        <NavLink
          key={item.key}
          to={item.to}
          end={item.end}
          data-testid={item.testId}
          className={tabLinkClass(activeKey === item.key)}
        >
          {item.label}
        </NavLink>
      ))}
    </div>
  );
}
