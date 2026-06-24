import React from 'react';
import { LAYOUT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * 页面容器 — 统一间距与入场动画
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
}) => {
  return (
    <div className={cn(LAYOUT.pageGap, LAYOUT.pageEnter, className)}>
      {children}
    </div>
  );
};

export default PageContainer;
