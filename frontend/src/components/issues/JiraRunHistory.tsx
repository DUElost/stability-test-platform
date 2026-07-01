/**
 * JiraRunHistory — 批量提单历史记录列表 + 日志 replay。
 * 数据来自后端 jira_run 表（持久化），点击展开用 LiveConsole 只读 replay 日志。
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronDown, ChevronRight, ExternalLink, RefreshCw } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import { Badge } from '@/components/ui/badge';
import LiveConsole from '@/components/console/LiveConsole';
import { dedup } from '@/utils/api/dedup';
import { EmptyState } from '@/components/ui/empty-state';
import { InlineError } from '@/components/ui/error-state';
import { FORM, INTERACTIVE, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatLocalDateTime } from '@/utils/format';
import { History } from 'lucide-react';

const STATUS_VARIANT: Record<string, 'success' | 'destructive' | 'warning' | 'secondary'> = {
  SUCCESS: 'success',
  FAILED: 'destructive',
  RUNNING: 'warning',
  CANCELED: 'secondary',
};

const VENDORS = ['', 'transsion', 'tinno'];
const STATUSES = ['', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELED'];

export default function JiraRunHistory() {
  const [vendor, setVendor] = useState('');
  const [status, setStatus] = useState('');
  const [expandedId, setExpandedId] = useState<string | null>(null);

  const { data: runs, isLoading, isError, refetch, isFetching } = useQuery({
    queryKey: ['jira-runs', vendor, status],
    queryFn: () => dedup.listRuns({
      vendor: vendor || undefined,
      status: status || undefined,
      limit: 50,
    }),
  });

  const toggle = (id: string) => setExpandedId(prev => prev === id ? null : id);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle>历史提交记录</CardTitle>
          <div className="flex items-center gap-2">
            <select
              aria-label="按厂商过滤"
              className={cn(FORM.select, 'min-w-0 h-9')}
              value={vendor}
              onChange={e => setVendor(e.target.value)}
            >
              {VENDORS.map(v => <option key={v || 'all'} value={v}>{v || '全部厂商'}</option>)}
            </select>
            <select
              aria-label="按状态过滤"
              className={cn(FORM.select, 'min-w-0 h-9')}
              value={status}
              onChange={e => setStatus(e.target.value)}
            >
              {STATUSES.map(s => <option key={s || 'all'} value={s}>{s || '全部状态'}</option>)}
            </select>
            <Button variant="outline" size="sm" onClick={() => refetch()} disabled={isFetching}>
              <RefreshCw className={cn('h-4 w-4', isFetching && 'animate-spin')} />
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {isError ? (
          <InlineError message="历史记录加载失败，请检查后端服务连接。" />
        ) : isLoading ? (
          <div className="space-y-3">
            {Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16 w-full" />)}
          </div>
        ) : !runs || runs.length === 0 ? (
          <EmptyState
            title="暂无提单记录"
            description="在「批量提单」页签执行后，记录会出现在这里"
            icon={<History className="w-16 h-16" />}
          />
        ) : (
          <div className="space-y-2">
            {runs.map(run => {
              const isOpen = expandedId === run.console_run_id;
              return (
                <div key={run.console_run_id} className="rounded-lg border">
                  <button
                    type="button"
                    className={cn('flex w-full items-start gap-3 p-3 text-left transition-colors', INTERACTIVE.hover)}
                    onClick={() => toggle(run.console_run_id)}
                    data-testid={`jira-run-row-${run.console_run_id}`}
                  >
                    {isOpen
                      ? <ChevronDown className="mt-0.5 h-4 w-4 shrink-0" />
                      : <ChevronRight className="mt-0.5 h-4 w-4 shrink-0" />}
                    <div className="flex-1 min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="font-medium text-sm">{run.vendor}</span>
                        <Badge variant="outline" className="text-[10px]">
                          {run.stage === 'upload_list' ? '上传模板' : '建单'}
                        </Badge>
                        {run.dry_run && <Badge variant="secondary" className="text-[10px]">dry-run</Badge>}
                        <Badge variant={STATUS_VARIANT[run.status] ?? 'secondary'} className="text-[10px]">
                          {run.status}
                        </Badge>
                        {run.reporter && (
                          <span className={cn('text-xs', TEXT.caption)}>reporter: {run.reporter}</span>
                        )}
                      </div>
                      <div className={cn('mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs', TEXT.caption)}>
                        <span>{formatLocalDateTime(run.created_at)}</span>
                        {run.ended_at && <span>结束 {formatLocalDateTime(run.ended_at)}</span>}
                        {run.exit_code !== null && run.exit_code !== undefined && (
                          <span>exit={run.exit_code}</span>
                        )}
                        {run.issue_keys.length > 0 && (
                          <span>已建 {run.issue_keys.length} 条 issue</span>
                        )}
                      </div>
                      {run.error && (
                        <div className={cn('mt-1 truncate text-xs text-destructive', TEXT.caption)}>
                          {run.error}
                        </div>
                      )}
                    </div>
                  </button>

                  {isOpen && (
                    <div className="space-y-3 border-t bg-muted/30 p-3">
                      {run.issue_keys.length > 0 && (
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={cn('text-xs font-medium', TEXT.subtitle)}>Issue Keys:</span>
                          {run.issue_keys.map(k => (
                            <span
                              key={k}
                              className="inline-flex items-center gap-1 rounded bg-primary/10 px-2 py-0.5 font-mono text-[11px] text-primary"
                            >
                              {k}
                              <ExternalLink className="h-3 w-3 opacity-50" />
                            </span>
                          ))}
                        </div>
                      )}
                      <LiveConsole consoleRunId={run.console_run_id} height="320px" enableIssueCount />
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </CardContent>
    </Card>
  );
}