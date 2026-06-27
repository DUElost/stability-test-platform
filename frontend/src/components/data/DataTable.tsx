import React, { ReactNode } from 'react';
import {
  useReactTable,
  getCoreRowModel,
  flexRender,
  type ColumnDef,
  type RowSelectionState,
} from '@tanstack/react-table';
import { MoreHorizontal } from 'lucide-react';
import { Checkbox } from '@/components/ui/checkbox';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { DataEmptyState } from './DataEmptyState';
import { DataErrorState } from './DataErrorState';
import { DataSkeleton } from './DataSkeleton';
import { cn } from '@/lib/utils';

export interface RowAction<T> {
  label: string;
  onClick: (row: T) => void;
  destructive?: boolean;
}

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T>[];
  isLoading?: boolean;
  error?: Error | null;
  emptyState?: ReactNode;
  selection?: 'none' | 'multiple';
  selectedKeys?: Set<string>;
  onSelectionChange?: (keys: Set<string>) => void;
  header?: ReactNode;
  footer?: ReactNode;
  rowActions?: (row: T) => RowAction<T>[];
  className?: string;
  getRowId?: (row: T) => string;
}

export function DataTable<T>({
  data,
  columns,
  isLoading,
  error,
  emptyState,
  selection = 'none',
  selectedKeys,
  onSelectionChange,
  header,
  footer,
  rowActions,
  className,
  getRowId,
}: DataTableProps<T>) {
  const rowSelection: RowSelectionState = React.useMemo(() => {
    const map: RowSelectionState = {};
    selectedKeys?.forEach((key) => {
      map[key] = true;
    });
    return map;
  }, [selectedKeys]);

  const tableColumns: ColumnDef<T>[] = React.useMemo(() => {
    const base = [...columns];
    if (selection === 'multiple') {
      base.unshift({
        id: 'select',
        header: ({ table }) => (
          <Checkbox
            checked={table.getIsAllPageRowsSelected()}
            onCheckedChange={(value) => table.toggleAllPageRowsSelected(!!value)}
            aria-label="全选"
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(value) => row.toggleSelected(!!value)}
            aria-label="选择行"
          />
        ),
        size: 40,
      });
    }
    if (rowActions) {
      base.push({
        id: 'actions',
        header: '',
        cell: ({ row }) => {
          const actions = rowActions(row.original);
          if (!actions || actions.length === 0) return null;
          return (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="ghost" size="icon" aria-label="行操作" className="h-8 w-8">
                  <MoreHorizontal className="h-4 w-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                {actions.map((action, idx) => (
                  <DropdownMenuItem
                    key={idx}
                    onClick={() => action.onClick(row.original)}
                    className={cn(action.destructive && 'text-destructive focus:text-destructive')}
                  >
                    {action.label}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          );
        },
        size: 50,
      });
    }
    return base;
  }, [columns, selection, rowActions]);

  const table = useReactTable({
    data,
    columns: tableColumns,
    state: { rowSelection },
    enableRowSelection: selection === 'multiple',
    onRowSelectionChange: (updater) => {
      if (!onSelectionChange) return;
      const next = typeof updater === 'function' ? updater(rowSelection) : updater;
      onSelectionChange(new Set(Object.keys(next)));
    },
    getCoreRowModel: getCoreRowModel(),
    getRowId,
  });

  if (isLoading) {
    return (
      <div className={className}>
        {header}
        <DataSkeleton rows={5} />
      </div>
    );
  }

  if (error) {
    return (
      <div className={className}>
        {header}
        <DataErrorState description={error.message} />
      </div>
    );
  }

  if (data.length === 0) {
    return (
      <div className={className}>
        {header}
        {emptyState ?? <DataEmptyState title="暂无数据" />}
      </div>
    );
  }

  return (
    <div className={cn('space-y-3', className)}>
      {header}
      <div className="overflow-x-auto rounded-lg border">
        <table className="w-full text-sm">
          <thead className="bg-muted/50">
            {table.getHeaderGroups().map((headerGroup) => (
              <tr key={headerGroup.id}>
                {headerGroup.headers.map((header) => (
                  <th
                    key={header.id}
                    className="px-3 py-2 text-left text-xs font-medium text-muted-foreground"
                    style={{ width: header.getSize() }}
                  >
                    {header.isPlaceholder
                      ? null
                      : flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y">
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="hover:bg-muted/50 transition-colors">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-2 whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {footer}
    </div>
  );
}

export default DataTable;
