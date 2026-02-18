import { useState, useMemo } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  getFilteredRowModel,
  flexRender,
  type SortingState,
  type ColumnDef,
} from '@tanstack/react-table';
import { format } from 'date-fns';
import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Filter,
  X,
  MoreHorizontal,
  Eye,
  Ban,
  Play,
  CheckCircle2,
  Clock,
  Loader2,
  AlertCircle,
} from 'lucide-react';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
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

export type TaskStatus = 'PENDING' | 'QUEUED' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'CANCELED';
export type TaskType = 'MONKEY' | 'MTBF' | 'DDR' | 'GPU' | 'STANDBY' | 'AIMONKEY';

export interface Task {
  id: number;
  name: string;
  type: TaskType;
  status: TaskStatus;
  priority: number;
  target_device_id?: number | null;
  target_device_serial?: string;
  created_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  progress?: number;
}

interface TaskDataTableProps {
  tasks: Task[];
  onViewDetail?: (task: Task) => void;
  onCancelTask?: (taskId: number) => void;
  onRetryTask?: (taskId: number) => void;
  loading?: boolean;
  selectedIds?: Set<number>;
  onSelectionChange?: (ids: Set<number>) => void;
}

const statusConfig: Record<TaskStatus, { label: string; variant: 'default' | 'secondary' | 'destructive' | 'outline' | 'success' | 'warning'; icon: React.ReactNode }> = {
  PENDING: { label: '待处理', variant: 'secondary', icon: <Clock className="w-3 h-3" /> },
  QUEUED: { label: '排队中', variant: 'warning', icon: <Clock className="w-3 h-3" /> },
  RUNNING: { label: '运行中', variant: 'default', icon: <Loader2 className="w-3 h-3 animate-spin" /> },
  COMPLETED: { label: '已完成', variant: 'success', icon: <CheckCircle2 className="w-3 h-3" /> },
  FAILED: { label: '已失败', variant: 'destructive', icon: <AlertCircle className="w-3 h-3" /> },
  CANCELED: { label: '已取消', variant: 'outline', icon: <Ban className="w-3 h-3" /> },
};

const typeColors: Record<TaskType, string> = {
  MONKEY: 'bg-blue-500/10 text-blue-600 border-blue-500/20',
  MTBF: 'bg-purple-500/10 text-purple-600 border-purple-500/20',
  DDR: 'bg-orange-500/10 text-orange-600 border-orange-500/20',
  GPU: 'bg-green-500/10 text-green-600 border-green-500/20',
  STANDBY: 'bg-gray-500/10 text-gray-600 border-gray-500/20',
  AIMONKEY: 'bg-pink-500/10 text-pink-600 border-pink-500/20',
};

export function TaskDataTable({
  tasks,
  onViewDetail,
  onCancelTask,
  onRetryTask,
  loading = false,
  selectedIds,
  onSelectionChange,
}: TaskDataTableProps) {
  const [sorting, setSorting] = useState<SortingState>([{ id: 'created_at', desc: true }]);
  const [statusFilter, setStatusFilter] = useState<TaskStatus | 'ALL'>('ALL');
  const [typeFilter, setTypeFilter] = useState<TaskType | 'ALL'>('ALL');
  const [pagination, setPagination] = useState({ pageIndex: 0, pageSize: 10 });
  const selectable = !!onSelectionChange;

  const filteredData = useMemo(() => {
    return tasks.filter((task) => {
      if (statusFilter !== 'ALL' && task.status !== statusFilter) return false;
      if (typeFilter !== 'ALL' && task.type !== typeFilter) return false;
      return true;
    });
  }, [tasks, statusFilter, typeFilter]);

  const columns = useMemo<ColumnDef<Task>[]>(
    () => [
      ...(selectable ? [{
        id: 'select',
        header: () => (
          <input
            type="checkbox"
            checked={selectedIds ? selectedIds.size === filteredData.length && filteredData.length > 0 : false}
            onChange={() => {
              if (!onSelectionChange || !selectedIds) return;
              if (selectedIds.size === filteredData.length) {
                onSelectionChange(new Set());
              } else {
                onSelectionChange(new Set(filteredData.map(t => t.id)));
              }
            }}
            className="rounded border-gray-300"
          />
        ),
        cell: ({ row }: { row: any }) => (
          <input
            type="checkbox"
            checked={selectedIds?.has(row.original.id) ?? false}
            onChange={() => {
              if (!onSelectionChange || !selectedIds) return;
              const next = new Set(selectedIds);
              if (next.has(row.original.id)) next.delete(row.original.id);
              else next.add(row.original.id);
              onSelectionChange(next);
            }}
            className="rounded border-gray-300"
          />
        ),
        enableSorting: false,
      } as ColumnDef<Task>] : []),
      {
        accessorKey: 'name',
        header: '任务名称',
        cell: ({ row }) => (
          <div className="flex flex-col">
            <span className="font-medium text-sm">{row.original.name}</span>
            <span className="text-xs text-muted-foreground">ID: {row.original.id}</span>
          </div>
        ),
      },
      {
        accessorKey: 'type',
        header: '类型',
        cell: ({ row }) => (
          <Badge variant="outline" className={cn('text-xs', typeColors[row.original.type])}>
            {row.original.type}
          </Badge>
        ),
      },
      {
        accessorKey: 'status',
        header: '状态',
        cell: ({ row }) => {
          const config = statusConfig[row.original.status];
          return (
            <Badge variant={config.variant} className="flex items-center gap-1 text-xs">
              {config.icon}
              {config.label}
            </Badge>
          );
        },
      },
      {
        accessorKey: 'priority',
        header: '优先级',
        cell: ({ row }) => (
          <div className="flex items-center gap-1">
            <span className={cn(
              'text-xs font-medium',
              row.original.priority >= 5 ? 'text-destructive' :
              row.original.priority >= 3 ? 'text-warning' : 'text-muted-foreground'
            )}>
              {row.original.priority}
            </span>
            {row.original.priority >= 5 && <span className="text-destructive">!</span>}
          </div>
        ),
      },
      {
        accessorKey: 'target_device_serial',
        header: '目标设备',
        cell: ({ row }) => (
          <span className="text-xs font-mono text-muted-foreground">
            {row.original.target_device_serial || '自动分配'}
          </span>
        ),
      },
      {
        accessorKey: 'created_at',
        header: '创建时间',
        cell: ({ row }) => (
          <span className="text-xs text-muted-foreground">
            {format(new Date(row.original.created_at), 'MM-dd HH:mm')}
          </span>
        ),
      },
      {
        accessorKey: 'progress',
        header: '进度',
        cell: ({ row }) => {
          const progress = row.original.progress;
          if (progress === undefined || progress === null) return <span className="text-xs text-muted-foreground">-</span>;
          return (
            <div className="flex items-center gap-2 w-24">
              <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
                <div
                  className={cn(
                    'h-full rounded-full transition-all',
                    row.original.status === 'FAILED' ? 'bg-destructive' :
                    row.original.status === 'COMPLETED' ? 'bg-success' : 'bg-primary'
                  )}
                  style={{ width: `${progress}%` }}
                />
              </div>
              <span className="text-xs text-muted-foreground w-8">{progress}%</span>
            </div>
          );
        },
      },
      {
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const task = row.original;
          const canCancel = ['PENDING', 'QUEUED', 'RUNNING'].includes(task.status);
          const canRetry = ['FAILED', 'CANCELED'].includes(task.status);

          return (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem onClick={() => onViewDetail?.(task)}>
                  <Eye className="mr-2 h-4 w-4" />
                  查看详情
                </DropdownMenuItem>
                {canCancel && (
                  <DropdownMenuItem onClick={() => onCancelTask?.(task.id)} className="text-destructive">
                    <Ban className="mr-2 h-4 w-4" />
                    取消任务
                  </DropdownMenuItem>
                )}
                {canRetry && (
                  <DropdownMenuItem onClick={() => onRetryTask?.(task.id)}>
                    <Play className="mr-2 h-4 w-4" />
                    重试任务
                  </DropdownMenuItem>
                )}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        },
      },
    ],
    [onViewDetail, onCancelTask, onRetryTask, selectable, selectedIds, onSelectionChange, filteredData]
  );

  const table = useReactTable({
    data: filteredData,
    columns,
    state: {
      sorting,
      pagination,
    },
    onSortingChange: setSorting,
    onPaginationChange: setPagination,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });

  const hasActiveFilters = statusFilter !== 'ALL' || typeFilter !== 'ALL';

  return (
    <div className="space-y-4">
      {/* Filters */}
      <div className="flex flex-wrap items-center gap-3">
        <div className="flex items-center gap-2">
          <Filter className="h-4 w-4 text-muted-foreground" />
          <span className="text-sm text-muted-foreground">筛选:</span>
        </div>

        <Select value={statusFilter} onValueChange={(v) => setStatusFilter(v as TaskStatus | 'ALL')}>
          <SelectTrigger className="w-32 h-8 text-xs">
            <SelectValue placeholder="状态" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部状态</SelectItem>
            {Object.entries(statusConfig).map(([status, config]) => (
              <SelectItem key={status} value={status}>
                <div className="flex items-center gap-2">
                  {config.icon}
                  {config.label}
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={typeFilter} onValueChange={(v) => setTypeFilter(v as TaskType | 'ALL')}>
          <SelectTrigger className="w-32 h-8 text-xs">
            <SelectValue placeholder="类型" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="ALL">全部类型</SelectItem>
            {Object.keys(typeColors).map((type) => (
              <SelectItem key={type} value={type}>
                {type}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        {hasActiveFilters && (
          <Button
            variant="ghost"
            size="sm"
            className="h-8 text-xs"
            onClick={() => {
              setStatusFilter('ALL');
              setTypeFilter('ALL');
            }}
          >
            <X className="mr-1 h-3 w-3" />
            清除
          </Button>
        )}

        <div className="ml-auto text-xs text-muted-foreground">
          {filteredData.length} 条任务
        </div>
      </div>

      {/* Table */}
      <div className="rounded-md border">
        <Table>
          <TableHeader>
            {table.getHeaderGroups().map((headerGroup) => (
              <TableRow key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <TableHead key={header.id} className="text-xs">
                    {header.isPlaceholder ? null : (
                      <button
                        className="flex items-center gap-1 hover:text-foreground transition-colors"
                        onClick={header.column.getToggleSortingHandler()}
                        disabled={!header.column.getCanSort()}
                      >
                        {flexRender(header.column.columnDef.header, header.getContext())}
                        {header.column.getCanSort() && (
                          <span className="text-muted-foreground">
                            {header.column.getIsSorted() === 'asc' ? (
                              <ArrowUp className="h-3 w-3" />
                            ) : header.column.getIsSorted() === 'desc' ? (
                              <ArrowDown className="h-3 w-3" />
                            ) : (
                              <ArrowUpDown className="h-3 w-3" />
                            )}
                          </span>
                        )}
                      </button>
                    )}
                  </TableHead>
                ))}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {loading ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-32 text-center">
                  <div className="flex items-center justify-center gap-2 text-muted-foreground">
                    <Loader2 className="h-4 w-4 animate-spin" />
                    加载中...
                  </div>
                </TableCell>
              </TableRow>
            ) : table.getRowModel().rows.length === 0 ? (
              <TableRow>
                <TableCell colSpan={columns.length} className="h-32 text-center text-muted-foreground">
                  暂无任务
                </TableCell>
              </TableRow>
            ) : (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id} className="hover:bg-muted/50">
                  {row.getVisibleCells().map((cell) => (
                    <TableCell key={cell.id} className="py-3">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      {/* Pagination */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span>第 {table.getState().pagination.pageIndex + 1} / {table.getPageCount()} 页</span>
          <span className="text-muted-foreground/50">|</span>
          <span>共 {table.getFilteredRowModel().rows.length} 条</span>
        </div>

        <div className="flex items-center gap-1">
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => table.setPageIndex(0)}
            disabled={!table.getCanPreviousPage()}
          >
            <ChevronsLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>

          <div className="flex items-center gap-1 mx-2">
            {Array.from({ length: Math.min(5, table.getPageCount()) }, (_, i) => {
              const pageIndex = i;
              const isActive = table.getState().pagination.pageIndex === pageIndex;
              return (
                <Button
                  key={pageIndex}
                  variant={isActive ? 'default' : 'outline'}
                  size="sm"
                  className="h-8 w-8 p-0 text-xs"
                  onClick={() => table.setPageIndex(pageIndex)}
                >
                  {pageIndex + 1}
                </Button>
              );
            })}
          </div>

          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
          <Button
            variant="outline"
            size="sm"
            className="h-8 w-8 p-0"
            onClick={() => table.setPageIndex(table.getPageCount() - 1)}
            disabled={!table.getCanNextPage()}
          >
            <ChevronsRight className="h-4 w-4" />
          </Button>
        </div>

        <Select
          value={String(table.getState().pagination.pageSize)}
          onValueChange={(v) => table.setPageSize(Number(v))}
        >
          <SelectTrigger className="w-20 h-8 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {[10, 20, 50].map((size) => (
              <SelectItem key={size} value={String(size)}>
                {size} 条/页
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
    </div>
  );
}
