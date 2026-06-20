import { ReactNode } from 'react';
import { FileText } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';

interface EmptyStateProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

/**
 * 统一的空状态组件
 *
 * @example
 * <EmptyState
 *   title="还没有 Plan"
 *   description="创建您的第一个测试计划"
 *   action={<Button onClick={onCreate}><Plus /> 新建 Plan</Button>}
 * />
 */
export function EmptyState({
  title = '暂无数据',
  description = '',
  action,
  icon,
}: EmptyStateProps) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div className="w-16 h-16 mx-auto mb-4 text-gray-300">
          {icon || <FileText className="w-16 h-16" />}
        </div>
        <p className="text-base font-medium text-gray-700 mb-2">{title}</p>
        {description && <p className="text-sm text-gray-500 mb-6">{description}</p>}
        {action}
      </CardContent>
    </Card>
  );
}

/**
 * 搜索无结果状态
 */
export function SearchEmptyState({ keyword }: { keyword: string }) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div className="w-16 h-16 mx-auto mb-4 text-gray-300">
          <svg className="w-16 h-16" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
          </svg>
        </div>
        <p className="text-base font-medium text-gray-700 mb-2">没有匹配的结果</p>
        <p className="text-sm text-gray-500">
          尝试使用其他关键词搜索 "{keyword}"
        </p>
      </CardContent>
    </Card>
  );
}
