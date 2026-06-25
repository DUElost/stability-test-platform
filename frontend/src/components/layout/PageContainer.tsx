import React from 'react';
import { LAYOUT, type PageWidth } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
  /** Content max-width preset (default: wide for list/management pages). */
  width?: PageWidth;
}

/**
 * 页面容器 — 统一间距、入场动画与可选最大宽度
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
  width = 'wide',
}) => {
  return (
    <div
      className={cn(
        LAYOUT.pageGap,
        LAYOUT.pageEnter,
        width !== 'full' && LAYOUT.pageWidth[width],
        className,
      )}
    >
      {children}
    </div>
  );
};

export default PageContainer;
