import { useState, useEffect } from 'react';
import { api } from '@/utils/api';
import { Loader2, Shield } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
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
  details?: Record<string, any>;
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

  const [filters, setFilters] = useState({
    resource_type: '',
    action: '',
    start_time: '',
    end_time: '',
  });

  const loadLogs = async () => {
    if (filters.start_time && filters.end_time && filters.start_time > filters.end_time) return;
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string> = {};
      if (filters.resource_type) params.resource_type = filters.resource_type;
      if (filters.action) params.action = filters.action;
      if (filters.start_time) params.start_time = filters.start_time;
      if (filters.end_time) params.end_time = filters.end_time;
      const res = await api.audit.list(page * pageSize, pageSize, params);
      setLogs(res.items);
      setTotal(res.total);
    } catch {
      setError('加载失败，请检查网络连接或管理员权限');
      setLogs([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadLogs(); }, [page, filters]);

  return (
    <PageContainer width="list">
      <PageHeader title="操作日志" subtitle="查看系统操作审计记录（仅管理员）" />

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={filters.resource_type}
          onChange={(e) => { setFilters({ ...filters, resource_type: e.target.value }); setPage(0); }}
          className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">全部资源</option>
          <option value="plan">Plan</option>
          <option value="tool">工具</option>
          <option value="tool_category">工具分类</option>
          <option value="notification_channel">通知渠道</option>
          <option value="notification_rule">告警规则</option>
          <option value="schedule">定时任务</option>
          <option value="template">任务模板</option>
          <option value="host">主机</option>
          <option value="task">任务</option>
        </select>
        <select
          value={filters.action}
          onChange={(e) => { setFilters({ ...filters, action: e.target.value }); setPage(0); }}
          className="h-9 rounded-md border border-input bg-background px-3 py-1 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          <option value="">全部操作</option>
          <option value="create">创建</option>
          <option value="update">更新</option>
          <option value="delete">删除</option>
          <option value="dispatch">分发</option>
          <option value="start">启动</option>
          <option value="cancel">取消</option>
        </select>
        <Input
          type="datetime-local"
          value={filters.start_time}
          onChange={(e) => { setFilters({ ...filters, start_time: e.target.value }); setPage(0); }}
        />
        <Input
          type="datetime-local"
          value={filters.end_time}
          onChange={(e) => { setFilters({ ...filters, end_time: e.target.value }); setPage(0); }}
        />
      </div>

      {error && !loading && (
        <InlineError message={error} />
      )}

      {loading ? (
        <div className="flex items-center justify-center h-64">
          <Loader2 className={cn('w-8 h-8 animate-spin', TEXT.subtitle)} />
        </div>
      ) : error ? null : logs.length === 0 ? (
        <div className={cn(PANEL.root, 'p-12 text-center')}>
          <Shield className={cn('w-12 h-12 mx-auto mb-3', TEXT.subtle)} />
          <h3 className={cn('text-lg font-medium mb-2', TEXT.heading)}>暂无审计记录</h3>
          <p className={cn('text-sm', TEXT.subtitle)}>操作日志将在此处记录</p>
        </div>
      ) : (
        <>
          <div className={cn(PANEL.root, 'overflow-hidden')}>
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border bg-muted/50">
                  <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>时间</th>
                  <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>用户</th>
                  <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>操作</th>
                  <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>资源</th>
                  <th className={cn('text-left px-4 py-3 font-medium', TEXT.subtitle)}>IP</th>
                </tr>
              </thead>
              <tbody>
                {logs.map((log) => (
                  <tr key={log.id} className="border-b border-border/50 hover:bg-muted/50">
                    <td className={cn('px-4 py-3 text-xs', TEXT.subtitle)}>
                      {formatDateTimeFull(log.timestamp)}
                    </td>
                    <td className={cn('px-4 py-3', TEXT.body)}>{log.username || '-'}</td>
                    <td className="px-4 py-3">
                      <span className={cn('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', STATUS_CHIP.primary)}>
                        {log.action}
                      </span>
                    </td>
                    <td className={cn('px-4 py-3', TEXT.subtitle)}>
                      {log.resource_type}{log.resource_id ? ` #${log.resource_id}` : ''}
                    </td>
                    <td className={cn('px-4 py-3 text-xs font-mono', TEXT.subtitle)}>{log.ip_address || '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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
