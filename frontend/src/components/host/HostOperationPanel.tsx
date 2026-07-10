import { useMemo, useState } from 'react';
import { ChevronDown, ChevronRight, Download, Loader2, X } from 'lucide-react';
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
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

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

  // Auto-expand first running / pending with console
  const effectiveExpanded = useMemo(() => {
    if (expanded.size > 0) return expanded;
    const auto = new Set<string>();
    for (const op of ops) {
      if (op.consoleRunId && (op.status === 'running' || op.status === 'pending')) {
        auto.add(op.hostId);
        break;
      }
    }
    if (auto.size === 0 && ops[0]) auto.add(ops[0].hostId);
    return auto;
  }, [expanded, ops]);

  const toggle = (hostId: string) => {
    setExpanded((prev) => {
      const base = prev.size ? new Set(prev) : new Set(effectiveExpanded);
      if (base.has(hostId)) base.delete(hostId);
      else base.add(hostId);
      return base;
    });
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
            </div>
          </DialogDescription>
        </DialogHeader>

        <div className="max-h-[70vh] space-y-2 overflow-y-auto">
          {ops.map((op) => {
            const isOpen = effectiveExpanded.has(op.hostId);
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

                {isOpen && op.consoleRunId && (
                  <div className="border-t p-2">
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
