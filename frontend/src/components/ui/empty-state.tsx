import { ReactNode } from 'react';
import { FileText } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

/**
 * 空态有三种形态，**不要互相替代**：
 *
 * | 形态 | 用在哪 | 组件 |
 * |------|--------|------|
 * | 页面/整卡无数据 | 列表页拉到空结果 | `EmptyState`（本文件） |
 * | 面板内小区域无数据 | 卡片里的某个图表/分区 | `InlineEmpty`（本文件） |
 * | 表格已有表头、只是没有行 | `<tbody>` 内 | `TableEmptyRow`（`ui/table.tsx`） |
 *
 * 把 `EmptyState` 塞进表格 `<tbody>` 或塞进巴掌大的面板里都会失调 ——
 * 它带 `py-16` 的留白和 64px 图标，是给整页用的。
 *
 * **标题文案规则**（现网 12:1 已成立，改动请守住）：
 * - **带 CTA**（引导用户创建第一个）→ 「还没有X」，如「还没有主机」+ 添加按钮
 * - **不带 CTA**（只是陈述当前没有）→ 「暂无X」，如「暂无执行记录」
 */

interface EmptyStateProps {
  title?: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
}

/** 页面级空态：图标底座 + 标题 + 描述 + 可选 CTA。 */
export function EmptyState({
  title = '暂无数据',
  description = '',
  action,
  icon,
}: EmptyStateProps) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        {/* 圆形底座：裸描边图标在暗色画布上过轻，压不住整卡的留白 */}
        <div
          className={cn(
            'w-16 h-16 mx-auto mb-4 rounded-full flex items-center justify-center',
            'bg-muted [&>svg]:w-8 [&>svg]:h-8',
            TEXT.subtle,
          )}
        >
          {icon || <FileText />}
        </div>
        <p className={cn('text-base font-medium mb-2', TEXT.heading)}>{title}</p>
        {description && (
          <p className={cn('text-sm mb-6', TEXT.subtitle)}>{description}</p>
        )}
        {action}
      </CardContent>
    </Card>
  );
}

/** 搜索无结果：与 EmptyState 同形态，但语义是「筛掉了」而非「没有」。 */
export function SearchEmptyState({ keyword }: { keyword: string }) {
  return (
    <Card>
      <CardContent className="py-16 text-center">
        <div
          className={cn(
            'w-16 h-16 mx-auto mb-4 rounded-full flex items-center justify-center',
            'bg-muted',
            TEXT.subtle,
          )}
        >
          <svg className="w-8 h-8" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={1.5}
              d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z"
            />
          </svg>
        </div>
        <p className={cn('text-base font-medium mb-2', TEXT.heading)}>没有匹配的结果</p>
        <p className={cn('text-sm', TEXT.subtitle)}>
          尝试使用其他关键词搜索 &quot;{keyword}&quot;
        </p>
      </CardContent>
    </Card>
  );
}

interface InlineEmptyProps {
  children: ReactNode;
  /** 加虚线描边，用于本身没有边框的容器（如 WiFi 池网格）。 */
  bordered?: boolean;
  /**
   * 图表占位：固定 `h-32` 并垂直居中。
   * 图表区必须占住高度 —— 否则数据到达的瞬间整块面板会往下弹。
   */
  chart?: boolean;
  testId?: string;
  className?: string;
}

/**
 * 面板内空态：一行居中灰字，无图标、无留白堆砌。
 * 用在已经有标题和边框的卡片/面板内部 —— 那里再放一次图标和大标题就是套娃。
 */
export function InlineEmpty({
  children,
  bordered = false,
  chart = false,
  testId,
  className,
}: InlineEmptyProps) {
  return (
    <div
      data-testid={testId}
      className={cn(
        'text-center text-sm',
        chart ? 'flex h-32 items-center justify-center' : 'py-10',
        bordered && 'rounded-md border border-dashed',
        TEXT.subtitle,
        className,
      )}
    >
      {children}
    </div>
  );
}
