/**
 * TestCaseResultsCard — ADR-0030 P2 PlanRun 逐条用例结果。
 */
import { useQuery } from '@tanstack/react-query';
import { ListChecks, Loader2 } from 'lucide-react';
import { api } from '@/utils/api';
import type { TestCaseResultRow } from '@/utils/api/types';
import { planRunKeys } from '@/utils/api/queryKeys';
import { PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { InlineEmpty } from '@/components/ui/empty-state';
import { InlineError } from '@/components/ui/error-state';
import { cn } from '@/lib/utils';

interface Props {
  runId: number;
  isTerminal: boolean;
}

const STATUS_CLASS: Record<string, string> = {
  PASS: STATUS_CHIP.success,
  FAILURE: STATUS_CHIP.destructive,
  ERROR: STATUS_CHIP.warning,
};

export default function TestCaseResultsCard({ runId, isTerminal }: Props) {
  const q = useQuery({
    queryKey: planRunKeys.testCaseResults(runId),
    queryFn: () => api.planRuns.getTestCaseResults(runId, { limit: 500 }),
    enabled: !!runId && isTerminal,
    refetchInterval: false,
  });

  const summary = q.data?.summary;

  return (
    <section className={PANEL.root} data-testid="test-case-results-card">
      <div className="flex items-center justify-between border-b px-4 py-2">
        <span className={cn('flex items-center gap-1.5 text-sm font-semibold', TEXT.heading)}>
          <ListChecks className="h-4 w-4" />
          用例结果
        </span>
        {summary && summary.total > 0 && (
          <span className={cn('text-xs', TEXT.subtitle)}>
            通过 {summary.passed} · 失败 {summary.failed} · 错误 {summary.error}
          </span>
        )}
      </div>
      <div className="px-3 py-2.5">
        {!isTerminal ? (
          <p className={cn('text-sm', TEXT.subtitle)}>PlanRun 结束后展示逐条用例结果。</p>
        ) : q.isLoading ? (
          <div className={cn('flex items-center gap-2 text-sm', TEXT.subtitle)}>
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </div>
        ) : q.isError ? (
          <InlineError message="用例结果加载失败" onRetry={() => void q.refetch()} />
        ) : !q.data?.items.length ? (
          <InlineEmpty>暂无逐条用例结果（非 MTBF 专项或未跑 mtbf_finish）</InlineEmpty>
        ) : (
          <div className="max-h-80 overflow-auto">
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b text-left text-muted-foreground">
                  <th className="py-1 pr-2">用例</th>
                  <th className="py-1 pr-2">状态</th>
                  <th className="py-1 pr-2">设备</th>
                  <th className="py-1">详情</th>
                </tr>
              </thead>
              <tbody>
                {q.data.items.map((row: TestCaseResultRow) => (
                  <tr key={row.id} className="border-b border-border/50" data-testid={`tcr-row-${row.id}`}>
                    <td className="py-1.5 pr-2 font-mono">{row.case_name}</td>
                    <td className="py-1.5 pr-2">
                      <span className={cn('rounded px-1.5 py-0.5', STATUS_CLASS[row.status] ?? STATUS_CHIP.muted)}>
                        {row.status}
                      </span>
                    </td>
                    <td className="py-1.5 pr-2">{row.device_id ?? '—'}</td>
                    <td className="py-1.5 text-muted-foreground truncate max-w-[240px]" title={row.detail ?? ''}>
                      {row.detail ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
