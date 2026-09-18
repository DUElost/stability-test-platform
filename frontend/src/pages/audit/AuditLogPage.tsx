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
import { FORM, INTERACTIVE, LAYOUT, PANEL, STATUS_CHIP, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { formatDateTimeFull } from '@/utils/format';
import { datetimeLocalInputToIso } from '@/utils/time';

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

/**
 * 中文展示名。#2629 之后它们**只是展示名**——可筛值来自 `GET /audit-logs/facets`
 * 的 distinct 结果，不再是这里的键集合。
 *
 * 这个区分就是本单的落点：原先 `ACTION_LABELS` 的键**同时充当**下拉选项，于是
 * 「一组硬编码中文标签」直连「一个精确等值的自由词表」，标签里的 `dispatch/start/cancel`
 * 与资源侧的 `tool/tool_category/template` 在写入侧根本不存在 ⇒ 选中即「共 0 条」。
 * 没有映射的值按原字面量展示（宁可看到 `job_instance`，也不要一个筛不出东西的中文死选项）。
 */
const ACTION_LABELS: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  dispatch: '分发',
  start: '启动',
  cancel: '取消',
};

/** 资源维度的展示名（同上：展示用，不充当选项来源）。 */
const RESOURCE_LABELS: Record<string, string> = {
  plan: 'Plan',
  plan_run: '计划运行',
  host: '主机',
  device: '设备',
  user: '用户',
  session: '会话/登录',
  job_instance: '作业实例',
  script: '脚本',
  script_catalog: '脚本目录',
  notification_channel: '通知渠道',
  notification_rule: '告警规则',
  schedule: '定时任务',
  task: '任务',
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
    // #2629：操作有 86 种字面量，下拉列不全也不该猜——改成与 username/IP/resource_id
    // 同一范式的「精确匹配 + datalist 补全」（候选仍来自真实写入值）。
    action: '',
    // #628：文本类筛选（用户名 / IP / 资源 ID）
    username: '',
    ip_address: '',
    resource_id: '',
    start_time: '',
    end_time: '',
  });

  // #628：文本输入不逐键发请求——本地草稿在 blur / Enter 时提交（select 仍即时生效）
  const [textDraft, setTextDraft] = useState({
    username: '',
    ip_address: '',
    resource_id: '',
    action: '',
  });

  const commitTextFilter = (
    key: 'username' | 'ip_address' | 'resource_id' | 'action',
    value: string,
  ) => {
    const next = value.trim();
    setTextDraft((draft) => ({ ...draft, [key]: next }));
    setFilters((prev) => (prev[key] === next ? prev : { ...prev, [key]: next }));
    setPage(0);
  };

  // #2629：两个筛选维度的候选值都来自**实际写入过的记录**（distinct + 条数），
  // 前端不再持有词表——所以「选中即 0 条」在结构上不再可能。请求失败时静默降级为
  // 「只剩全部资源 + 自由输入操作」，与用户名下拉同一取舍。
  const facetsQ = useQuery({
    queryKey: ['audit-facets', 'audit-filter-options'],
    queryFn: () => api.audit.facets(),
    staleTime: 60_000,
  });
  const resourceFacets = facetsQ.data?.resource_types ?? [];
  const actionFacets = facetsQ.data?.actions ?? [];

  // 用户名下拉候选（admin 页面；失败静默降级为自由输入）
  const usersQ = useQuery({
    queryKey: ['users', 'audit-filter-options'],
    queryFn: () => api.users.list(0, 200),
    staleTime: 60_000,
  });
  const usernames = usersQ.data?.items ?? [];

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
      if (filters.action) params.action = filters.action;
      if (filters.username) params.username = filters.username;
      if (filters.ip_address) params.ip_address = filters.ip_address;
      if (filters.resource_id) params.resource_id = filters.resource_id;
      if (filters.start_time) params.start_time = datetimeLocalInputToIso(filters.start_time);
      if (filters.end_time) params.end_time = datetimeLocalInputToIso(filters.end_time);
      return api.audit.list(page * pageSize, pageSize, params);
    },
    enabled: !invalidRange,
    // #2369 残余：全局 `refetchOnWindowFocus: false`（QueryProvider），切回前台只按域失效
    // plans/projects（useCrossClientSync）——审计日志既无轮询也不在该域，后台期间别处
    // 产生的操作（另一管理员/定时任务）在切回后不会回追。显式 opt-in：focus 与可见性
    // 语义一致，且只重取**活跃**查询（本页挂载中），不放大限流桶。
    refetchOnWindowFocus: true,
  });

  const logs = (data?.items as unknown as AuditLogEntry[] | undefined) ?? [];
  const total = data?.total ?? 0;

  return (
    // #750：scrollable={false} 只是不让页面充当主滚动容器（筛选/分页固定、表格内滚）；
    // 但外层必须保留溢出兜底——窄屏（如 1024×600）下页头+筛选行折行后可能高于视口，
    // 此时表格区会被 flex 压到 0 高，且 AppShell main 为 overflow-hidden，无兜底则整页不可达。
    <PageContainer
      width="content"
      scrollable={false}
      className={cn(LAYOUT.pageGap, 'min-h-0 overflow-auto')}
    >
      <PageHeader title="操作日志" subtitle="查看系统操作审计记录（仅管理员）" />

      {/* Filters */}
      <div className="flex shrink-0 flex-wrap gap-3">
        <select
          value={filters.resource_type}
          onChange={(e) => { setFilters({ ...filters, resource_type: e.target.value }); setPage(0); }}
          className={FORM.select}
          data-testid="audit-resource-filter"
        >
            <option value="all">全部资源</option>
            {resourceFacets.map((facet) => (
              <option key={facet.value} value={facet.value}>
                {`${RESOURCE_LABELS[facet.value] ?? facet.value}（${facet.count}）`}
              </option>
            ))}
        </select>
        {/* #2629：操作维度改成「精确匹配 + datalist」（86 种字面量列不全，硬列就是假阴性来源） */}
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>操作</span>
          <Input
            list="audit-action-options"
            className="w-56"
            placeholder="精确匹配"
            value={textDraft.action}
            onChange={(e) => setTextDraft((d) => ({ ...d, action: e.target.value }))}
            onBlur={(e) => commitTextFilter('action', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitTextFilter('action', e.currentTarget.value);
            }}
            data-testid="audit-action-filter"
          />
          <datalist id="audit-action-options">
            {actionFacets.map((facet) => (
              <option key={facet.value} value={facet.value}>
                {`${ACTION_LABELS[facet.value] ?? facet.value} · ${facet.count}`}
              </option>
            ))}
          </datalist>
        </label>
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
        {/* #628：文本类筛选，blur / Enter 提交，避免逐键请求 */}
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>用户</span>
          <Input
            list="audit-username-options"
            className="w-40"
            placeholder="用户名"
            value={textDraft.username}
            onChange={(e) => setTextDraft((d) => ({ ...d, username: e.target.value }))}
            onBlur={(e) => commitTextFilter('username', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitTextFilter('username', e.currentTarget.value);
            }}
            data-testid="audit-username-filter"
          />
          <datalist id="audit-username-options">
            {usernames.map((u) => (
              <option key={u.id} value={u.username} />
            ))}
          </datalist>
        </label>
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>IP 地址</span>
          <Input
            className="w-40"
            placeholder="如 192.0.2.1"
            value={textDraft.ip_address}
            onChange={(e) => setTextDraft((d) => ({ ...d, ip_address: e.target.value }))}
            onBlur={(e) => commitTextFilter('ip_address', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitTextFilter('ip_address', e.currentTarget.value);
            }}
            data-testid="audit-ip-filter"
          />
        </label>
        <label className="flex items-center gap-2">
          <span className={cn('whitespace-nowrap text-sm', TEXT.subtitle)}>资源 ID</span>
          <Input
            className="w-32"
            placeholder="精确匹配"
            value={textDraft.resource_id}
            onChange={(e) => setTextDraft((d) => ({ ...d, resource_id: e.target.value }))}
            onBlur={(e) => commitTextFilter('resource_id', e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') commitTextFilter('resource_id', e.currentTarget.value);
            }}
            data-testid="audit-resource-id-filter"
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
          {/* #750：min-h-[240px] 兜住塌缩（原先 min-h-0 允许被压到 0 高），
              与 PageContainer 的 overflow-auto 配合保证表格与分页始终可达。 */}
          <div className={cn(PANEL.root, 'min-h-[240px] flex-1 overflow-auto')}>
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

          <div className="shrink-0">
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
          </div>
        </>
      )}
    </PageContainer>
  );
}
