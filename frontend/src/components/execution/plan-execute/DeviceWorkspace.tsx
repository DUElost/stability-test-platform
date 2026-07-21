import type { ReactNode } from 'react';
import { cn } from '@/lib/utils';

interface DeviceWorkspaceProps {
  nodeRail: ReactNode;
  stage: ReactNode;
  selectedRail: ReactNode;
  className?: string;
}

/**
 * 选机工作台三栏壳（对齐 mockup `.workspace`）：
 * 节点轨 | 候选池舞台 | 已选集。
 * 外层须处在 flex-1 min-h-0 链上以铺满视口剩余高度（类 hosts 全宽）。
 */
export function DeviceWorkspace({
  nodeRail,
  stage,
  selectedRail,
  className,
}: DeviceWorkspaceProps) {
  return (
    <div
      className={cn(
        'grid h-full min-h-0 flex-1 gap-3',
        'grid-cols-1 auto-rows-[minmax(280px,1fr)]',
        // ≥1100px 三栏：窄轨 + 中区吃满 + 右栏已选集
        'min-[1100px]:grid-cols-[200px_minmax(0,1fr)_280px] min-[1100px]:auto-rows-fr',
        className,
      )}
      data-plan-execute-layout="three-column"
      data-testid="device-workspace"
    >
      <div className="min-h-0 min-[1100px]:h-full">{nodeRail}</div>
      <div className="min-h-0 min-[1100px]:h-full">{stage}</div>
      <div className="min-h-0 min-[1100px]:h-full">{selectedRail}</div>
    </div>
  );
}
