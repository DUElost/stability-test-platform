import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Layers, Plus, Search } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Card, CardContent } from '@/components/ui/card';
import { Input } from '@/components/ui/input';
import { PageContainer, PageHeader } from '@/components/layout';
import { EmptyState, SearchEmptyState } from '@/components/ui/empty-state';
import { ErrorState } from '@/components/ui/error-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { ProjectFilterSelect } from '@/components/project/ProjectFilterSelect';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { INTERACTIVE, TEXT } from '@/design-system';
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

  const renderRow = (suite: TestSuiteSummary) => (
    <button
      key={suite.id}
      type="button"
      data-testid={`suite-row-${suite.id}`}
      className={cn(
        'flex w-full items-center justify-between gap-3 rounded-lg border border-border px-4 py-3 text-left',
        INTERACTIVE.hover,
      )}
      onClick={() => navigate(`/test-suites/${suite.id}`)}
    >
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <span className={cn('font-medium', TEXT.heading)}>{suite.name}</span>
          {suite.display_name && (
            <span className={TEXT.subtitle}>{suite.display_name}</span>
          )}
          {suite.export_stale && (
            <Badge variant="outline" className="border-warning/40 text-warning">
              导出漂移
            </Badge>
          )}
        </div>
        <p className={cn('mt-1 text-xs', TEXT.subtitle)}>
          {suite.project_key ? `项目 ${suite.project_key} · ` : ''}
          {suite.enabled_case_count}/{suite.case_count} 用例启用
        </p>
      </div>
      <Layers className="h-4 w-4 shrink-0 text-muted-foreground" />
    </button>
  );

  return (
    <PageContainer width="content">
      <PageHeader
        title="用例套件"
        subtitle="MTBF 用例集管理（runtask.xml 导入 / 导出 / 校验）"
        action={isAdmin ? (
          <Button size="sm" data-testid="create-suite-btn" onClick={() => setCreateOpen(true)}>
            <Plus className="mr-2 h-4 w-4" />
            新建套件
          </Button>
        ) : undefined}
      />

      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-center">
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
          className="w-full sm:w-56"
        />
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
        <Card>
          <CardContent className="space-y-2 pt-6">
            {filtered.map(renderRow)}
          </CardContent>
        </Card>
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
