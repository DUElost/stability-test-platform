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

function StatCardSkeleton() {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between">
        <div className="space-y-2 flex-1">
          <Skeleton className="h-3 w-20" />
          <Skeleton className="h-8 w-16" />
        </div>
        <Skeleton className="h-12 w-12 rounded-xl" />
      </div>
    </Card>
  );
}

const BLOCK_SIZES = { md: 'h-32', lg: 'h-64' } as const;

// 与两表成品统计卡行的真实网格一致（hosts 4 卡 / devices 5 卡）；
// 新形态出现时在此加具名映射，不开放任意 grid。
const STATS_GRIDS = {
  4: 'grid-cols-2 xl:grid-cols-4',
  5: 'grid-cols-2 md:grid-cols-3 xl:grid-cols-5',
} as const;

export function PageSkeleton({ children }: { children: ReactNode }) {
  return <div className="space-y-4">{children}</div>;
}

/** 通用区块占位：md = 筛选/工具栏区，lg = 表格区 */
PageSkeleton.Block = function Block({ size = 'md' }: { size?: keyof typeof BLOCK_SIZES }) {
  return <div className={cn('bg-muted animate-pulse rounded-lg', BLOCK_SIZES[size])} />;
};

/** 统计卡行占位（Expandable*Table 首屏筛选卡，count=成品卡数） */
PageSkeleton.Stats = function Stats({ count }: { count: number }) {
  // count 是调用方硬编码的成品卡数，非用户输入；校验防超大 Array.from 分配
  // 与手滑传错。渲染上限 12：真实页面超出时应在此加具名网格映射而非硬传。
  if (!Number.isInteger(count) || count < 1 || count > 12) {
    throw new Error(`PageSkeleton.Stats: count 必须是 1-12 的整数，收到 ${count}`);
  }
  return (
    <div className={cn('grid gap-3', STATS_GRIDS[count as keyof typeof STATS_GRIDS] ?? 'grid-cols-2 md:grid-cols-3 xl:grid-cols-5')}>
      {Array.from({ length: count }).map((_, i) => (
        <StatCardSkeleton key={i} />
      ))}
    </div>
  );
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
