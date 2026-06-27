import React from 'react';
import { LAYOUT, type PageWidth } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
  /** Content max-width preset. Ignored when fullBleed is true. */
  width?: PageWidth;
  /** Remove horizontal padding so lists/tables touch the viewport edges. */
  fullBleed?: boolean;
  /** Whether the container itself scrolls. Disable for editors that manage their own panels. */
  scrollable?: boolean;
}

/**
 * 页面容器 — 统一间距、入场动画与可选最大宽度。
 * 新页面应优先使用 fullBleed + PageHeaderV2，旧 width 预设保留兼容。
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
  width = 'wide',
  fullBleed = false,
  scrollable = true,
}) => {
  return (
    <div
      className={cn(
        'h-full flex flex-col',
        LAYOUT.pageEnter,
        scrollable && 'overflow-auto',
        fullBleed ? 'w-full' : [LAYOUT.pagePadding, LAYOUT.pageWidth[width]],
        className,
      )}
    >
      {children}
    </div>
  );
};

export default PageContainer;
