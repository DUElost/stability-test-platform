import { useEffect, useMemo, useState } from 'react';
import {
  ChevronDown,
  ChevronRight,
  ChevronsDownUp,
  ChevronsUpDown,
  Download,
  Loader2,
  X,
} from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import LiveConsole from '@/components/console/LiveConsole';
import type { HostOpItem } from '@/hooks/useHostOperations';
import { STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

interface Props {
  open: boolean;
  ops: HostOpItem[];
  onClose: () => void;
  onTerminalStatus: (hostId: string, status: string) => void;
}

const TERMINAL = new Set(['SUCCESS', 'FAILED', 'CANCELED']);

function statusChip(status: HostOpItem['status']): string {
  switch (status) {
    case 'success':
      return STATUS_CHIP.success;
    case 'failed':
      return STATUS_CHIP.destructive;
    case 'running':
    case 'pending':
      return STATUS_CHIP.warning;
    default:
      return STATUS_CHIP.muted;
  }
}

function statusLabel(status: HostOpItem['status']): string {
  switch (status) {
    case 'pending':
      return '排队';
    case 'running':
      return '进行中';
    case 'success':
      return '成功';
    case 'failed':
      return '失败';
    case 'skipped':
      return '跳过';
    default:
      return status;
  }
}

export default function HostOperationPanel({
  open,
  ops,
  onClose,
  onTerminalStatus,
}: Props) {
  /** null = 自动模式；Set（可为空）= 用户手动控制，空集表示全部折叠 */
  const [expanded, setExpanded] = useState<Set<string> | null>(null);
  /** 一旦拿到 consoleRunId 就挂载 LiveConsole，折叠仅 CSS 隐藏，避免再展开空白 */
  const [mountedConsoles, setMountedConsoles] = useState<Set<string>>(() => new Set());

  useEffect(() => {
    setMountedConsoles((prev) => {
      const next = new Set(prev);
      let changed = false;
      for (const op of ops) {
        if (op.consoleRunId && !next.has(op.hostId)) {
          next.add(op.hostId);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [ops]);

  // 新一批操作时恢复自动展开
  const opsKey = ops.map((o) => o.hostId).join(',');
  useEffect(() => {
    setExpanded(null);
  }, [opsKey]);

  const summary = useMemo(() => {
    let running = 0;
    let success = 0;
    let failed = 0;
    for (const op of ops) {
      if (op.status === 'pending' || op.status === 'running') running += 1;
      else if (op.status === 'success') success += 1;
      else if (op.status === 'failed') failed += 1;
    }
    return { running, success, failed };
  }, [ops]);

  const autoExpanded = useMemo(() => {
    const auto = new Set<string>();
    for (const op of ops) {
      if (op.consoleRunId && (op.status === 'running' || op.status === 'pending')) {
        auto.add(op.hostId);
      }
    }
    if (auto.size === 0) {
      const withConsole = ops.find((o) => o.consoleRunId);
      if (withConsole) auto.add(withConsole.hostId);
      else if (ops[0]) auto.add(ops[0].hostId);
    }
    // 自动模式最多展开 2 个，减轻首屏压力；用户可「全部展开」
    if (auto.size > 2) {
      return new Set(Array.from(auto).slice(0, 2));
    }
    return auto;
  }, [ops]);

  const effectiveExpanded = expanded ?? autoExpanded;

  const toggle = (hostId: string) => {
    setExpanded((prev) => {
      const base = new Set(prev ?? effectiveExpanded);
      if (base.has(hostId)) base.delete(hostId);
      else base.add(hostId);
      return base; // 允许空 Set = 全部折叠
    });
  };

  const collapseAll = () => setExpanded(new Set());
  const expandAll = () => {
    setExpanded(
      new Set(ops.filter((o) => o.consoleRunId || o.status === 'running').map((o) => o.hostId)),
    );
  };

  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent
        data-testid="host-operation-panel"
        className="max-w-4xl gap-3"
        onPointerDownOutside={(e) => e.preventDefault()}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Download className="h-4 w-4" />
            主机操作进度
          </DialogTitle>
          <DialogDescription asChild>
            <div className={cn('flex flex-wrap items-center gap-3 text-sm', TEXT.subtitle)}>
              <span>
                进行中 <b className="font-mono text-foreground">{summary.running}</b>
              </span>
              <span>
                成功 <b className="font-mono text-foreground">{summary.success}</b>
              </span>
              <span>
                失败 <b className="font-mono text-foreground">{summary.failed}</b>
              </span>
              <span className="ml-auto flex gap-1">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  data-testid="host-op-expand-all"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={expandAll}
                >
                  <ChevronsUpDown className="h-3.5 w-3.5" />
                  全部展开
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  data-testid="host-op-collapse-all"
                  className="h-7 gap-1 px-2 text-xs"
                  onClick={collapseAll}
                >
                  <ChevronsDownUp className="h-3.5 w-3.5" />
                  全部折叠
                </Button>
              </span>
            </div>
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[70vh] space-y-2 overflow-y-auto">
          {ops.map((op) => {
            const isOpen = effectiveExpanded.has(op.hostId);
            const shouldMount = Boolean(op.consoleRunId) && mountedConsoles.has(op.hostId);
            return (
              <div
                key={op.hostId}
                data-testid={`host-op-row-${op.hostId}`}
                className="rounded-lg border border-border"
              >
                <button
                  type="button"
                  className="flex w-full items-center gap-2 px-3 py-2 text-left text-sm hover:bg-muted/40"
                  onClick={() => toggle(op.hostId)}
                >
                  {isOpen ? (
                    <ChevronDown className="h-3.5 w-3.5 shrink-0" />
                  ) : (
                    <ChevronRight className="h-3.5 w-3.5 shrink-0" />
                  )}
                  <span className="font-medium">{op.label}</span>
                  <span className={cn('text-[11px]', TEXT.subtle)}>
                    {op.kind === 'reinstall' ? '重新安装' : '首次安装'}
                  </span>
                  {(op.status === 'pending' || op.status === 'running') && (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" />
                  )}
                  <span
                    className={cn(
                      'ml-auto rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase',
                      statusChip(op.status),
                    )}
                  >
                    {statusLabel(op.status)}
                  </span>
                </button>

                {op.error && (
                  <div className={cn('border-t px-3 py-1.5 text-xs text-destructive')}>
                    {op.error}
                  </div>
                )}

                {/* 折叠用 hidden，不卸载 — 避免再展开空白 / 丢 socket */}
                {shouldMount && op.consoleRunId && (
                  <div className={cn('border-t p-2', !isOpen && 'hidden')}>
                    <LiveConsole
                      consoleRunId={op.consoleRunId}
                      height="280px"
                      onStatusChange={(st) => {
                        if (!TERMINAL.has(st)) return;
                        onTerminalStatus(op.hostId, st);
                      }}
                    />
                  </div>
                )}

                {isOpen && !op.consoleRunId && op.status === 'running' && (
                  <div
                    className={cn(
                      'flex items-center gap-2 border-t px-3 py-6 text-xs',
                      TEXT.subtitle,
                    )}
                  >
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    正在启动安装…
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <div className="flex justify-end">
          <Button variant="outline" size="sm" onClick={onClose} className="gap-1">
            <X className="h-3.5 w-3.5" />
            关闭
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
