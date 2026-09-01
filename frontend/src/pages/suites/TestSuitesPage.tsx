import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Layers, Plus, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { ClickableRow } from '@/components/ui/clickable-row';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { ProjectFilterSelect } from '@/components/project/ProjectFilterSelect';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { LAYOUT, TEXT } from '@/design-system';
import { cn } from '@/lib/utils';
import { api, toApiError } from '@/utils/api';
import { suiteKeys } from '@/utils/api/queryKeys';
import type { TestSuiteCreateInput, TestSuiteSummary } from '@/utils/api/types';
import CreateSuiteDialog from './components/CreateSuiteDialog';

function invalidateSuites(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({ queryKey: suiteKeys.allLists() });
  void queryClient.invalidateQueries({ queryKey: suiteKeys.planEditor() });
}

export default function TestSuitesPage() {
  useDocumentTitle('用例套件');
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';

  const [search, setSearch] = useState('');
  const [projectKey, setProjectKey] = useState<string | undefined>(undefined);
  const [createOpen, setCreateOpen] = useState(false);

  const { data: suites, isLoading, isError, error, refetch } = useQuery({
    queryKey: suiteKeys.list(projectKey, search || null),
    queryFn: () => api.suites.list({
      project_key: projectKey ?? undefined,
      is_active: true,
      q: search || undefined,
    }),
  });

  const filtered = useMemo(() => {
    if (!suites) return [];
    const q = search.trim().toLowerCase();
    if (!q) return suites;
    return suites.filter((s) =>
      s.name.toLowerCase().includes(q)
      || (s.display_name ?? '').toLowerCase().includes(q),
    );
  }, [suites, search]);

  const createMutation = useMutation({
    mutationFn: (payload: TestSuiteCreateInput) => api.suites.create(payload),
    onSuccess: (created) => {
      invalidateSuites(queryClient);
      setCreateOpen(false);
      toast.success(`已创建 ${created.name}`);
      navigate(`/test-suites/${created.id}`);
    },
    onError: (err: unknown) => {
      toast.error(`创建失败: ${toApiError(err).message}`);
    },
  });

  return (
    <PageContainer width="content" className={LAYOUT.pageGap}>
      <PageHeader
        title="用例套件"
        subtitle="MTBF 用例集管理（runtask.xml 导入 / 导出 / 校验）"
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
          <Input
            data-testid="suite-search"
            className="pl-9"
            placeholder="搜索套件名…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <ProjectFilterSelect
          value={projectKey}
          onChange={setProjectKey}
          className="w-full sm:w-44"
          testId="suite-project-filter"
        />
        {isAdmin && (
          <Button
            size="sm"
            data-testid="create-suite-btn"
            onClick={() => setCreateOpen(true)}
            className="shrink-0"
          >
            <Plus className="mr-2 h-4 w-4" />
            新建套件
          </Button>
        )}
      </div>

      {isLoading && (
        <PageSkeleton>
          <PageSkeleton.Cards count={3} />
        </PageSkeleton>
      )}
      {isError && (
        <ErrorState
          title="加载失败"
          description={toApiError(error).message}
          onRetry={() => void refetch()}
        />
      )}
      {!isLoading && !isError && filtered.length === 0 && (
        search ? (
          <SearchEmptyState keyword={search} />
        ) : (
          <EmptyState
            icon={<Layers className="h-8 w-8" />}
            title="暂无用例套件"
            description={isAdmin ? '创建套件后导入 runtask.xml 批量填充用例。' : '请联系管理员创建套件。'}
          />
        )
      )}
      {!isLoading && !isError && filtered.length > 0 && (
        <div className="overflow-x-auto rounded-lg border border-border">
          <Table className="min-w-[640px]">
            <TableHeader>
              <TableRow className="border-b text-left text-xs text-muted-foreground hover:bg-transparent">
                <TableHead className="h-9 px-3">套件</TableHead>
                <TableHead className="h-9 px-3">项目</TableHead>
                <TableHead className="h-9 px-3">用例</TableHead>
                <TableHead className="h-9 px-3">状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {filtered.map((suite: TestSuiteSummary) => (
                <ClickableRow
                  key={suite.id}
                  data-testid={`suite-row-${suite.id}`}
                  className="border-b transition-colors last:border-0 hover:bg-muted/50"
                  onClick={() => navigate(`/test-suites/${suite.id}`)}
                  role="button"
                >
                  <TableCell className="max-w-[280px] px-3 py-2.5">
                    <span className={cn('block truncate text-sm font-medium', TEXT.heading)}>
                      {suite.name}
                    </span>
                    {suite.display_name && (
                      <span className={cn('mt-0.5 block truncate text-xs', TEXT.caption)}>
                        {suite.display_name}
                      </span>
                    )}
                  </TableCell>
                  <TableCell className={cn('px-3 py-2.5 font-mono text-xs', TEXT.caption)}>
                    {suite.project_key || '—'}
                  </TableCell>
                  <TableCell className={cn('px-3 py-2.5 text-xs', TEXT.caption)}>
                    {suite.enabled_case_count}/{suite.case_count} 启用
                  </TableCell>
                  <TableCell className="px-3 py-2.5">
                    {suite.export_stale ? (
                      <Badge variant="outline" className="border-warning/40 text-warning">
                        导出漂移
                      </Badge>
                    ) : (
                      <span className={cn('text-xs', TEXT.caption)}>正常</span>
                    )}
                  </TableCell>
                </ClickableRow>
              ))}
            </TableBody>
          </Table>
        </div>
      )}

      <CreateSuiteDialog
        isOpen={createOpen}
        isSubmitting={createMutation.isPending}
        onClose={() => setCreateOpen(false)}
        onSubmit={(payload) => createMutation.mutate(payload)}
      />
    </PageContainer>
  );
}
