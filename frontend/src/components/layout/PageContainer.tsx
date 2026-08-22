import React from 'react';
import { LAYOUT, type PageWidth } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
  /**
   * 内容宽度档位，按内容**类型**选，判定树见
   * `docs/design/2026-08-21-frontend-page-shell-spec.md`。拿不准用 `content`。
   */
  width?: PageWidth;
  /** Whether the container itself scrolls. Disable for editors that manage their own panels. */
  scrollable?: boolean;
}

/**
 * 页面容器 —— 统一间距、入场动画与内容宽度。
 *
 * `bleed` 是宽度档位而**不是**独立开关：此前 `fullBleed` 布尔优先于 `width`，
 * 于是 `PlanRunLogsPage` 写的 `width="logs"` + `fullBleed` 里 `width` 被静默忽略、
 * `logs`(1480px) 那一档从未生效过也没人发现。收进枚举后这类
 * 「两个参数互相覆盖、错的那个不报错」在类型层面就不成立。
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
  width = 'content',
  scrollable = true,
}) => {
  const bleed = width === 'bleed';
  return (
    <div
      className={cn(
        'h-full flex flex-col',
        LAYOUT.pageEnter,
        scrollable && 'overflow-auto',
        LAYOUT.pageWidth[width],
        !bleed && LAYOUT.pagePadding,
        className,
      )}
    >
      {children}
    </div>
  );
};

export default PageContainer;
