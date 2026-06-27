import React from 'react';
import { Skeleton } from '@/components/ui/skeleton';

interface DataSkeletonProps {
  rows?: number;
  columns?: number;
  className?: string;
}

export const DataSkeleton: React.FC<DataSkeletonProps> = ({
  rows = 5,
  columns = 1,
  className,
}) => {
  return (
    <div className={className}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} className="flex items-center gap-3 py-2">
          {Array.from({ length: columns }).map((_, j) => (
            <Skeleton key={j} className="h-10 w-full" />
          ))}
        </div>
      ))}
    </div>
  );
};

export default DataSkeleton;
