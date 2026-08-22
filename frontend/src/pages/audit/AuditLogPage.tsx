import { useCallback, useState, useEffect } from 'react';
import { api } from '@/utils/api';
import { Shield } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';

interface AuditLogEntry {
  id: number;
  user_id?: number;
  username?: string;
  action: string;
  resource_type: string;
  resource_id?: number;
  details?: Record<string, unknown>;
  ip_address?: string;
  timestamp: string;
}

export default function AuditLogPage() {
  const [logs, setLogs] = useState<AuditLogEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(0);
  const pageSize = 50;

  // 哨兵值 'all' = 不过滤；Radix Select 的 SelectItem 不接受空字符串
  // （部分版本会告警/受控异常），API 侧不带该参数即全量
  const [filters, setFilters] = useState({
    resource_type: 'all',
    action: 'all',
    start_time: '',
    end_time: '',
  });

  const loadLogs = useCallback(async () => {
    if (filters.start_time && filters.end_time && filters.start_time > filters.end_time) return;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (filters.resource_type !== 'all') params.resource_type = filters.resource_type;
      if (filters.action !== 'all') params.action = filters.action;
      if (filters.start_time) params.start_time = filters.start_time;
      if (filters.end_time) params.end_time = filters.end_time;
      const res = await api.audit.list(page * pageSize, pageSize, params);
      setLogs(res.items as unknown as AuditLogEntry[]);
      setTotal(res.total);
    } catch {
      setError('加载失败，请检查网络连接或管理员权限');
      setLogs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [page, filters]);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- 拉取 effect 的同步 loading/分页置位
    loadLogs();
  }, [loadLogs]);

  return (
    <PageContainer width="content">
      <PageHeader title="操作日志" subtitle="查看系统操作审计记录（仅管理员）" />

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <Select value={filters.resource_type} onValueChange={(v) => { setFilters({ ...filters, resource_type: v }); setPage(0); }}>
          <SelectTrigger data-testid="audit-resource-filter">
            <SelectValue placeholder="全部资源" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部资源</SelectItem>
            <SelectItem value="plan">Plan</SelectItem>
            <SelectItem value="tool">工具</SelectItem>
            <SelectItem value="tool_category">工具分类</SelectItem>
            <SelectItem value="notification_channel">通知渠道</SelectItem>
            <SelectItem value="notification_rule">告警规则</SelectItem>
            <SelectItem value="schedule">定时任务</SelectItem>
            <SelectItem value="template">任务模板</SelectItem>
            <SelectItem value="host">主机</SelectItem>
            <SelectItem value="task">任务</SelectItem>
          </SelectContent>
        </Select>
        <Select value={filters.action} onValueChange={(v) => { setFilters({ ...filters, action: v }); setPage(0); }}>
          <SelectTrigger data-testid="audit-action-filter">
            <SelectValue placeholder="全部操作" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部操作</SelectItem>
            <SelectItem value="create">创建</SelectItem>
            <SelectItem value="update">更新</SelectItem>
            <SelectItem value="delete">删除</SelectItem>
            <SelectItem value="dispatch">分发</SelectItem>
            <SelectItem value="start">启动</SelectItem>
            <SelectItem value="cancel">取消</SelectItem>
          </SelectContent>
        </Select>
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>开始时间</span>
          <Input
            type="datetime-local"
            className="w-52"
            value={filters.start_time}
            onChange={(e) => { setFilters({ ...filters, start_time: e.target.value }); setPage(0); }}
          />
        </label>
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>结束时间</span>
          <Input
            type="datetime-local"
            className="w-52"
            value={filters.end_time}
            onChange={(e) => { setFilters({ ...filters, end_time: e.target.value }); setPage(0); }}
          />
        </label>
      </div>

      {error && !loading && (
        <InlineError message={error} onRetry={loadLogs} />
      )}

      {loading ? (
        <PageSkeleton>
          <PageSkeleton.Block size="md" />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      ) : error ? null : logs.length === 0 ? (
        <EmptyState
          title="暂无审计记录"
          description="操作日志将在此处记录"
          icon={<Shield />}
        />
      ) : (
        <>
          <div className={cn(PANEL.root, 'overflow-x-auto')}>
            <Table className="min-w-[640px]">
              <TableHeader>
                <TableRow className="border-b border-border bg-muted/50">
                  <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>时间</TableHead>
                  <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>用户</TableHead>
                  <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>操作</TableHead>
                  <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>资源</TableHead>
                  <TableHead className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>IP</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((log) => (
                  <TableRow key={log.id} className="border-b border-border/50 hover:bg-muted/50">
                    <TableCell className={cn('px-4 py-3 text-xs', TEXT.subtitle)}>
                      {formatDateTimeFull(log.timestamp)}
                    </TableCell>
                    <TableCell className={cn('px-4 py-3', TEXT.body)}>{log.username || '-'}</TableCell>
                    <TableCell className="px-4 py-3">
                      <span className={cn('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', STATUS_CHIP.primary)}>
                        {log.action}
                      </span>
                    </TableCell>
                    <TableCell className={cn('px-4 py-3', TEXT.subtitle)}>
                      {log.resource_type}{log.resource_id ? ` #${log.resource_id}` : ''}
                    </TableCell>
                    <TableCell className={cn('px-4 py-3 text-xs font-mono', TEXT.subtitle)}>{log.ip_address || '-'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <div className={cn('flex items-center justify-between text-sm', TEXT.subtitle)}>
            <span>共 {total} 条记录</span>
            <div className="flex gap-2 items-center">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(p => Math.max(0, p - 1))}
                disabled={page === 0}
              >
                上一页
              </Button>
              <span className="px-3 py-1">第 {page + 1} 页</span>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setPage(p => p + 1)}
                disabled={(page + 1) * pageSize >= total}
              >
                下一页
              </Button>
            </div>
          </div>
        </>
      )}
    </PageContainer>
  );
}
