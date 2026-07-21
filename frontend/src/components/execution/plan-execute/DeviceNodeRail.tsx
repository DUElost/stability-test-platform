import { Input } from '@/components/ui/input';
import { TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Layers3 } from 'lucide-react';

export interface DeviceNodeSummary {
  id: string;
  label: string;
  total: number;
  selected: number;
  available: number;
  online: boolean;
  busy: number;
  healthStatus: string | null;
  healthReasons: string[];
}

interface DeviceNodeRailProps {
  nodes: DeviceNodeSummary[];
  selectedHostId: string;
  onSelectHost: (hostId: string) => void;
  search: string;
  onSearchChange: (value: string) => void;
  allTotal: number;
  allAvailable: number;
  allSelected: number;
}

export function DeviceNodeRail({
  nodes,
  selectedHostId,
  onSelectHost,
  search,
  onSearchChange,
  allTotal,
  allAvailable,
  allSelected,
}: DeviceNodeRailProps) {
  return (
    <aside
      className="flex h-full min-h-0 flex-col overflow-hidden rounded-xl border bg-card shadow-sm"
      data-testid="plan-execute-node-rail"
    >
      <div className="shrink-0 border-b px-3 py-2.5 text-sm font-semibold">节点</div>
      <div className="min-h-0 flex-1 overflow-auto p-2">
        <div className={cn('px-2 pb-2 text-xs', TEXT.subtitle)}>
          已选 {allSelected} / {allAvailable} 台可用
        </div>
        <Input
          className="mb-2 h-8"
          value={search}
          onChange={(event) => onSearchChange(event.target.value)}
          placeholder="节点 IP / 名称"
        />
        <div className="space-y-1">
          <button
            type="button"
            onClick={() => onSelectHost('all')}
            className={cn(
              'w-full rounded-lg border px-3 py-2 text-left transition-colors',
              selectedHostId === 'all' ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-accent',
            )}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 truncate text-xs font-medium">
                <Layers3 className="h-3.5 w-3.5 shrink-0" />
                全部节点
              </span>
            </div>
            <div className={cn('mt-1 flex justify-between text-xs', TEXT.subtitle)}>
              <span>
                {allTotal} 台 · 已选 {allSelected}
              </span>
              <span>{allAvailable} 可用</span>
            </div>
          </button>
          {nodes.map((node) => {
            const unschedulable = node.healthStatus === 'UNSCHEDULABLE';
            const degraded = node.healthStatus === 'DEGRADED';
            const dotCls = !node.online || unschedulable
              ? 'bg-destructive'
              : degraded
                ? 'bg-warning'
                : 'bg-success';
            const dotTitle = node.healthReasons.length
              ? `${node.healthStatus}：${node.healthReasons.join('、')}`
              : node.online
                ? '在线'
                : '离线';
            return (
              <button
                key={node.id}
                type="button"
                onClick={() => onSelectHost(node.id)}
                className={cn(
                  'w-full rounded-lg border px-3 py-2 text-left transition-colors',
                  selectedHostId === node.id ? 'border-primary bg-primary/10' : 'border-transparent hover:bg-accent',
                )}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-xs">{node.label}</span>
                  <span title={dotTitle} className={cn('h-2 w-2 shrink-0 rounded-full', dotCls)} />
                </div>
                <div className={cn('mt-1 flex justify-between text-xs', TEXT.subtitle)}>
                  <span>
                    {node.total} 台 · 已选 {node.selected}
                    {node.busy > 0 ? ` · 忙 ${node.busy}` : ''}
                  </span>
                  <span>{node.available} 可用</span>
                </div>
              </button>
            );
          })}
        </div>
        {nodes.length === 0 && (
          <div className={cn('px-2 py-6 text-center text-xs', TEXT.subtitle)}>未找到匹配节点</div>
        )}
      </div>
    </aside>
  );
}
