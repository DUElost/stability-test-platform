import { useState, useCallback, useMemo } from 'react';

interface UsePaginationOptions {
  initialPage?: number;
  initialPageSize?: number;
}

interface UsePaginationReturn {
  page: number;
  pageSize: number;
  skip: number;
  limit: number;
  total: number;
  totalPages: number;
  setTotal: (total: number) => void;
  goToPage: (page: number) => void;
  nextPage: () => void;
  prevPage: () => void;
  changePageSize: (size: number) => void;
  canPreviousPage: boolean;
  canNextPage: boolean;
}

export function usePagination({
  initialPage = 1,
  initialPageSize = 20,
}: UsePaginationOptions = {}): UsePaginationReturn {
  const [page, setPage] = useState(initialPage);
  const [pageSize, setPageSize] = useState(initialPageSize);
  const [total, setTotal] = useState(0);

  const totalPages = useMemo(() => Math.max(1, Math.ceil(total / pageSize)), [total, pageSize]);

  const skip = useMemo(() => (page - 1) * pageSize, [page, pageSize]);
  const limit = pageSize;

  const canPreviousPage = page > 1;
  const canNextPage = page < totalPages;

  const goToPage = useCallback(
    (p: number) => {
      setPage(Math.max(1, Math.min(p, totalPages)));
    },
    [totalPages]
  );

  const nextPage = useCallback(() => {
    if (canNextPage) setPage((p) => p + 1);
  }, [canNextPage]);

  const prevPage = useCallback(() => {
    if (canPreviousPage) setPage((p) => p - 1);
  }, [canPreviousPage]);

  const changePageSize = useCallback((size: number) => {
    setPageSize(size);
    setPage(1); // Reset to first page when changing page size
  }, []);

  return {
    page,
    pageSize,
    skip,
    limit,
    total,
    totalPages,
    setTotal,
    goToPage,
    nextPage,
    prevPage,
    changePageSize,
    canPreviousPage,
    canNextPage,
  };
}
