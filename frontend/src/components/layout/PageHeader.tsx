import React, { ReactNode, useEffect } from 'react';
import { ChevronRight, Home } from 'lucide-react';
import { Link } from 'react-router-dom';
import { useHeaderSlot } from '@/contexts/HeaderSlotContext';

interface BreadcrumbItem {
  label: string;
  path?: string;
}

interface PageHeaderProps {
  title: string;
  subtitle?: string;
  action?: ReactNode;
  breadcrumbs?: BreadcrumbItem[];
}

export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  subtitle,
  action,
  breadcrumbs,
}) => {
  const { setHeaderSlot, isDefault } = useHeaderSlot();

  // 真实 AppShell 内:把标题/副标题注入顶栏;
  // 无 Provider(如单元测试)时不注入,走下方回退渲染。
  useEffect(() => {
    if (isDefault) return;
    setHeaderSlot(
      <div className="flex min-w-0 flex-col justify-center gap-1">
        <h1 className="truncate text-lg font-semibold leading-tight tracking-tight text-gray-900">
          {title}
        </h1>
        {subtitle && (
          <span className="truncate text-xs leading-tight text-gray-400">
            {subtitle}
          </span>
        )}
      </div>,
    );
    return () => setHeaderSlot(null);
  }, [title, subtitle, isDefault, setHeaderSlot]);

  const breadcrumbsEl =
    breadcrumbs && breadcrumbs.length > 0 ? (
      <nav className="flex items-center text-sm text-gray-500">
        <Link
          to="/"
          className="flex items-center hover:text-gray-900 transition-colors"
        >
          <Home size={14} className="mr-1" />
          首页
        </Link>
        {breadcrumbs.map((item, index) => (
          <React.Fragment key={index}>
            <ChevronRight size={14} className="mx-2 text-gray-300" />
            {item.path ? (
              <Link
                to={item.path}
                className="hover:text-gray-900 transition-colors"
              >
                {item.label}
              </Link>
            ) : (
              <span className="text-gray-700 font-medium">{item.label}</span>
            )}
          </React.Fragment>
        ))}
      </nav>
    ) : null;

  // 真实 AppShell:标题已进顶栏,页面内只保留面包屑 + 操作按钮(右对齐)。
  if (!isDefault) {
    if (!breadcrumbsEl && !action) return null;
    return (
      <div className="space-y-3">
        {breadcrumbsEl}
        {action && (
          <div className="flex flex-shrink-0 items-center justify-end gap-2">
            {action}
          </div>
        )}
      </div>
    );
  }

  // 回退(无 Provider,如单元测试):页面内完整渲染,保持与原结构/断言兼容。
  return (
    <div className="space-y-3">
      {breadcrumbsEl}
      <div className="flex items-center justify-between gap-4">
        <div>
          <h2 className="text-2xl font-semibold text-gray-900 mb-1">{title}</h2>
          {subtitle && <p className="text-sm text-gray-400">{subtitle}</p>}
        </div>
        {action && (
          <div className="flex-shrink-0 flex items-center gap-2">{action}</div>
        )}
      </div>
    </div>
  );
};

export default PageHeader;
