import React from 'react';

interface PageContainerProps {
  children: React.ReactNode;
  className?: string;
}

/**
 * 页面容器组件
 * 统一页面边距、最大宽度和间距
 */
export const PageContainer: React.FC<PageContainerProps> = ({
  children,
  className = '',
}) => {
  return (
    <div className={`space-y-6 ${className}`}>
      {children}
    </div>
  );
};

export default PageContainer;
