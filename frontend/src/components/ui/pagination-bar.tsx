import {
  ChevronLeft,
  ChevronRight,
  ChevronsLeft,
  ChevronsRight,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

interface PaginationBarProps {
  page: number;
  totalPages: number;
  total: number;
  pageSize: number;
  canPreviousPage: boolean;
  canNextPage: boolean;
  onGoToPage: (page: number) => void;
  onNextPage: () => void;
  onPrevPage: () => void;
  onChangePageSize: (size: number) => void;
  pageSizeOptions?: number[];
}

export function PaginationBar({
  page,
  totalPages,
  total,
  pageSize,
  canPreviousPage,
  canNextPage,
  onGoToPage,
  onNextPage,
  onPrevPage,
  onChangePageSize,
  pageSizeOptions = [10, 20, 50],
}: PaginationBarProps) {
  // Generate page numbers to display (max 5 centered around current page)
  const getVisiblePages = () => {
    const maxVisible = 5;
    if (totalPages <= maxVisible) {
      return Array.from({ length: totalPages }, (_, i) => i + 1);
    }
    let start = Math.max(1, page - Math.floor(maxVisible / 2));
    const end = Math.min(totalPages, start + maxVisible - 1);
    if (end - start + 1 < maxVisible) {
      start = Math.max(1, end - maxVisible + 1);
    }
    return Array.from({ length: end - start + 1 }, (_, i) => start + i);
  };

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>
          第 {page} / {totalPages} 页
        </span>
        <span className="text-muted-foreground/50">|</span>
        <span>共 {total} 条</span>
      </div>

      <div className="flex items-center gap-1">
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={() => onGoToPage(1)}
          disabled={!canPreviousPage}
        >
          <ChevronsLeft className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onPrevPage}
          disabled={!canPreviousPage}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>

        <div className="flex items-center gap-1 mx-2">
          {getVisiblePages().map((p) => (
            <Button
              key={p}
              variant={p === page ? 'default' : 'outline'}
              size="sm"
              className="h-8 w-8 p-0 text-xs"
              onClick={() => onGoToPage(p)}
            >
              {p}
            </Button>
          ))}
        </div>

        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onNextPage}
          disabled={!canNextPage}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={() => onGoToPage(totalPages)}
          disabled={!canNextPage}
        >
          <ChevronsRight className="h-4 w-4" />
        </Button>
      </div>

      <Select
        value={String(pageSize)}
        onValueChange={(v) => onChangePageSize(Number(v))}
      >
        <SelectTrigger className="w-24 h-8 text-xs">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {pageSizeOptions.map((size) => (
            <SelectItem key={size} value={String(size)}>
              {size} 条/页
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}
