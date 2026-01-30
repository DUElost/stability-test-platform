import React, { ReactNode } from 'react';
import { ChevronRight, Home } from 'lucide-react';
import { Link } from 'react-router-dom';

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

/**
 * 页面头部组件
 * 统一标题、副标题、操作按钮和面包屑导航
 */
export const PageHeader: React.FC<PageHeaderProps> = ({
  title,
  subtitle,
  action,
  breadcrumbs,
}) => {
  return (
    <div className="space-y-3">
      {/* 面包屑导航 */}
      {breadcrumbs && breadcrumbs.length > 0 && (
        <nav className="flex items-center text-sm text-slate-500">
          <Link
            to="/"
            className="flex items-center hover:text-blue-600 transition-colors"
          >
            <Home size={14} className="mr-1" />
            Home
          </Link>
          {breadcrumbs.map((item, index) => (
            <React.Fragment key={index}>
              <ChevronRight size={14} className="mx-2 text-slate-300" />
              {item.path ? (
                <Link
                  to={item.path}
                  className="hover:text-blue-600 transition-colors"
                >
                  {item.label}
                </Link>
              ) : (
                <span className="text-slate-700 font-medium">{item.label}</span>
              )}
            </React.Fragment>
          ))}
        </nav>
      )}

      {/* 标题栏 */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4">
        <div>
          <h1 className="text-2xl font-bold text-slate-900">{title}</h1>
          {subtitle && (
            <p className="text-slate-500 mt-1">{subtitle}</p>
          )}
        </div>
        {action && (
          <div className="flex-shrink-0">{action}</div>
        )}
      </div>
    </div>
  );
};

export default PageHeader;
