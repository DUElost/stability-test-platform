import { ChevronDown } from 'lucide-react';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import type { PrecheckState } from '@/utils/api/types';

/** Compact precheck summary row shown above the dispatch gate collapsible. */
export default function PrecheckSummaryRow({
  precheck,
  expanded,
  onToggle,
  gateFailed,
}: {
  precheck: PrecheckState;
  expanded: boolean;
  onToggle: () => void;
  gateFailed: boolean;
}) {
  const hosts = precheck.hosts ?? {};
  const hostEntries = Object.entries(hosts);
  const phase = precheck.phase;
  const mixedWatcherFailure =
    precheck.gate_failure?.code === 'MIXED_WATCHER_ACTIVITY'
      ? precheck.gate_failure
      : null;

  const { verified, total } = hostEntries.reduce(
    (acc, [, h]) => {
      const scripts = h.scripts ?? [];
      acc.total += scripts.length;
      acc.verified += scripts.filter((s) => s.ok).length;
      return acc;
    },
    { verified: 0, total: 0 },
  );

  const statusText =
    phase === 'ready'
      ? '通过'
      : phase === 'failed'
        ? '失败'
        : phase === 'syncing'
          ? '同步中'
          : phase === 'verifying' || phase === 'reverifying'
            ? '校验中'
            : phase;

  return (
    <button
      type="button"
      data-testid="precheck-row"
      onClick={onToggle}
      className="mx-1 flex w-full items-start gap-2 rounded-lg border border-border bg-card px-3 py-2 text-left shadow-sm hover:bg-muted/50"
    >
      <ChevronDown
        className={cn(
          'mt-0.5 h-3.5 w-3.5 shrink-0 text-muted-foreground transition-transform',
          !expanded && '-rotate-90',
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className={cn('rounded px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wider', STATUS_CHIP.primary)}>
            预检
          </span>
          <span className={cn('flex-1 text-xs font-semibold', TEXT.heading)}>健康预检</span>
          <span
            className={`text-xs font-semibold ${
              phase === 'ready'
                ? STATUS_TEXT_COLORS.success
                : phase === 'failed'
                  ? STATUS_TEXT_COLORS.error
                  : STATUS_TEXT_COLORS.warning
            }`}
          >
            {statusText}
          </span>
          {gateFailed && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-destructive" />}
        </div>
        <div className={cn('mt-0.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px]', TEXT.subtitle)}>
          <span>
            <b className={cn('font-semibold', TEXT.heading)}>{hostEntries.length}</b> 主机
          </span>
          {total > 0 && (
            <span>
              <b className={cn('font-semibold', TEXT.heading)}>
                {verified}/{total}
              </b>{' '}
              脚本
            </span>
          )}
          {hostEntries.map(([hid]) => (
            <span key={hid} className="font-mono">{hid}</span>
          ))}
          {mixedWatcherFailure && (
            <span className={`basis-full ${STATUS_TEXT_COLORS.error}`}>
              {mixedWatcherFailure.message}
            </span>
          )}
          {mixedWatcherFailure &&
            mixedWatcherFailure.inactive_host_ids.length > 0 && (
              <span className="basis-full font-mono text-destructive">
                不激活节点ID：{mixedWatcherFailure.inactive_host_ids.join(', ')}
              </span>
            )}
        </div>
      </div>
    </button>
  );
}
