import type { ReactNode } from 'react';
import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { cn } from '@/lib/utils';

/**
 * 页面级加载骨架家族 —— 与 empty-state 家族对齐的具名形态：
 * - 首屏结构已知 → PageSkeleton（本文件，积木组合）
 * - 局部/短操作 → ui/skeleton 基础 Skeleton 或 Loader2 spinner（不在本文件）
 *
 * 积木不开 className 口子（防止各页再调出第四种画法）；
 * 高度/数量之外的诉求一律加具名变体，不加拉杆。
 * count 必须等于该页成品同区域的实际条目数（高度保真，防数据到达时下弹）。
 */

function CardSkeleton() {
  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <Skeleton className="h-5 w-3/4" />
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-2/3" />
      </CardContent>
    </Card>
  );
}

function ListItemSkeleton() {
  return (
    <div className="flex items-center gap-3 p-4">
      <Skeleton className="h-10 w-10 rounded-lg" />
      <div className="flex-1 space-y-2">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-3 w-2/3" />
      </div>
    </div>
  );
}

const BLOCK_SIZES = { md: 'h-32', lg: 'h-64' } as const;

export function PageSkeleton({ children }: { children: ReactNode }) {
  return <div className="space-y-4">{children}</div>;
}

/** 通用区块占位：md = 筛选/工具栏区，lg = 表格区 */
PageSkeleton.Block = function Block({ size = 'md' }: { size?: keyof typeof BLOCK_SIZES }) {
  return <div className={cn('bg-muted animate-pulse rounded-lg', BLOCK_SIZES[size])} />;
};

/** 卡片列表占位（原 LoadingGrid + CardSkeleton 的唯一在用组合） */
PageSkeleton.Cards = function Cards({ count }: { count: number }) {
  return (
    <div className="space-y-4">
      {Array.from({ length: count }).map((_, i) => (
        <CardSkeleton key={i} />
      ))}
    </div>
  );
};

/** 图标+双行文字的列表项占位（如通知渠道卡列表） */
PageSkeleton.List = function List({ count }: { count: number }) {
  return (
    <div className="space-y-3">
      {Array.from({ length: count }).map((_, i) => (
        <ListItemSkeleton key={i} />
      ))}
    </div>
  );
};
