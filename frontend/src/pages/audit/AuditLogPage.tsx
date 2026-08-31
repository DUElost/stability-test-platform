import { useState, Fragment } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '@/utils/api';
import { Shield, ChevronRight } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { InlineError } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { PaginationBar } from '@/components/ui/pagination-bar';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { FORM, INTERACTIVE, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
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

/** 与筛选下拉的中文文案一致，避免表格里裸英文 action */
const ACTION_LABELS: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  dispatch: '分发',
  start: '启动',
  cancel: '取消',
};

export default function AuditLogPage() {
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(50);
  const [expandedIds, setExpandedIds] = useState<Set<number>>(new Set());

  const toggleExpand = (id: number) => {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  // 哨兵值 'all' = 不过滤；B4 决议后全站下拉为原生 <select>，
  // 哨兵同时避免了空字符串 value 的歧义，API 侧不带该参数即全量
  const [filters, setFilters] = useState({
    resource_type: 'all',
    action: 'all',
    start_time: '',
    end_time: '',
  });

  // C1：数据获取迁移 react-query（缓存/重试/去重与全站一致）；
  // 非法时间区间通过 enabled 禁发请求，UI 层显示静态错误（M3 语义保留）
  const invalidRange = Boolean(
    filters.start_time && filters.end_time && filters.start_time > filters.end_time,
  );

  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['audit-logs', page, pageSize, filters],
    queryFn: () => {
      const params: Record<string, string> = {};
      if (filters.resource_type !== 'all') params.resource_type = filters.resource_type;
      if (filters.action !== 'all') params.action = filters.action;
      if (filters.start_time) params.start_time = filters.start_time;
      if (filters.end_time) params.end_time = filters.end_time;
      return api.audit.list(page * pageSize, pageSize, params);
    },
    enabled: !invalidRange,
  });

  const logs = (data?.items as unknown as AuditLogEntry[] | undefined) ?? [];
  const total = data?.total ?? 0;

  return (
    <PageContainer width="content">
      <PageHeader title="操作日志" subtitle="查看系统操作审计记录（仅管理员）" />

      {/* Filters */}
      <div className="flex gap-3 flex-wrap">
        <select
          value={filters.resource_type}
          onChange={(e) => { setFilters({ ...filters, resource_type: e.target.value }); setPage(0); }}
          className={FORM.select}
          data-testid="audit-resource-filter"
        >
            <option value="all">全部资源</option>
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
          className={FORM.select}
          data-testid="audit-action-filter"
        >
            <option value="all">全部操作</option>
            <option value="create">创建</option>
            <option value="update">更新</option>
            <option value="delete">删除</option>
            <option value="dispatch">分发</option>
            <option value="start">启动</option>
            <option value="cancel">取消</option>
        </select>
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

      {invalidRange ? (
        <InlineError message="起始时间不能晚于结束时间" />
      ) : isError ? (
        <InlineError
          message="加载失败，请检查网络连接或管理员权限"
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <PageSkeleton>
          <PageSkeleton.Block size="md" />
          <PageSkeleton.Block size="lg" />
        </PageSkeleton>
      ) : logs.length === 0 ? (
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
                  <TableHead className="w-8 px-2 py-2" />
                  <TableHead className={cn('text-left px-4 py-2 font-medium', TEXT.subtitle)}>时间</TableHead>
                  <TableHead className={cn('text-left px-4 py-2 font-medium', TEXT.subtitle)}>用户</TableHead>
                  <TableHead className={cn('text-left px-4 py-2 font-medium', TEXT.subtitle)}>操作</TableHead>
                  <TableHead className={cn('text-left px-4 py-2 font-medium', TEXT.subtitle)}>资源</TableHead>
                  <TableHead className={cn('text-left px-4 py-2 font-medium', TEXT.subtitle)}>IP</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((log) => {
                  const hasDetails = log.details && Object.keys(log.details).length > 0;
                  const expanded = expandedIds.has(log.id);
                  return (
                    <Fragment key={log.id}>
                      <TableRow className="border-b border-border/50 hover:bg-muted/50">
                        <TableCell className="w-8 px-2 py-1.5">
                          {hasDetails ? (
                            <button
                              type="button"
                              onClick={() => toggleExpand(log.id)}
                              className={cn('rounded p-0.5', INTERACTIVE.iconButton)}
                              aria-label={expanded ? '收起详情' : '展开详情'}
                              aria-expanded={expanded}
                            >
                              <ChevronRight
                                className={cn('w-4 h-4 transition-transform', expanded && 'rotate-90')}
                              />
                            </button>
                          ) : null}
                        </TableCell>
                        <TableCell className={cn('px-4 py-1.5 text-xs', TEXT.subtitle)}>
                          {formatDateTimeFull(log.timestamp)}
                        </TableCell>
                        <TableCell className={cn('px-4 py-1.5', TEXT.body)}>{log.username || '-'}</TableCell>
                        <TableCell className="px-4 py-1.5">
                          <span className={cn('inline-flex items-center px-2 py-0.5 rounded-full text-xs font-medium', STATUS_CHIP.primary)}>
                            {ACTION_LABELS[log.action] ?? log.action}
                          </span>
                        </TableCell>
                        <TableCell className={cn('px-4 py-1.5', TEXT.subtitle)}>
                          {log.resource_type}{log.resource_id ? ` #${log.resource_id}` : ''}
                        </TableCell>
                        <TableCell className={cn('px-4 py-1.5 text-xs font-mono', TEXT.subtitle)}>{log.ip_address || '-'}</TableCell>
                      </TableRow>
                      {hasDetails && expanded && (
                        <TableRow className="border-b border-border/50 bg-muted/30">
                          <TableCell colSpan={6} className="px-6 py-2">
                            <pre className={cn('overflow-x-auto text-xs leading-relaxed', TEXT.subtitle)}>
                              {JSON.stringify(log.details, null, 2)}
                            </pre>
                          </TableCell>
                        </TableRow>
                      )}
                    </Fragment>
                  );
                })}
              </TableBody>
            </Table>
          </div>

          {/* Pagination */}
          <PaginationBar
            page={page + 1}
            totalPages={Math.max(1, Math.ceil(total / pageSize))}
            total={total}
            pageSize={pageSize}
            canPreviousPage={page > 0}
            canNextPage={(page + 1) * pageSize < total}
            onGoToPage={(p) => setPage(p - 1)}
            onNextPage={() => setPage((p) => p + 1)}
            onPrevPage={() => setPage((p) => Math.max(0, p - 1))}
            onChangePageSize={(size) => { setPageSize(size); setPage(0); }}
          />
        </>
      )}
    </PageContainer>
  );
}
