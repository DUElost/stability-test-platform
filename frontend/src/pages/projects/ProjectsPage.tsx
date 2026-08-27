import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, FolderKanban, Layers, Plus, Link2, X } from 'lucide-react';
import { Card, CardContent, CardHeader } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { PageContainer, PageHeader } from '@/components/layout';
import { ErrorState } from '@/components/ui/error-state';
import { EmptyState } from '@/components/ui/empty-state';
import { PageSkeleton } from '@/components/ui/loading-skeleton';
import { STAT, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';
import { useDocumentTitle } from '@/hooks/useDocumentTitle';
import { useAuthSession } from '@/hooks/useAuthSession';
import { useToast } from '@/hooks/useToast';
import { api, toApiError } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';
import type { ProjectCreateInput, ProjectMapPreview, ProjectSummary } from '@/utils/api/types';
import InventoryModelsTable from './components/InventoryModelsTable';
import ProjectDetailSheet from './components/ProjectDetailSheet';
import CreateProjectDialog from './components/CreateProjectDialog';
import MapModelsDialog from './components/MapModelsDialog';

/** ADR-0029 facet：正交可组合筛选，选项从数据 distinct 提取。 */
const FACET_FIELDS = ['customer', 'platform', 'form_factor', 'product_line'] as const;
type FacetField = (typeof FACET_FIELDS)[number];

const FACET_LABEL: Record<FacetField, string> = {
  customer: '客户',
  platform: '平台',
  form_factor: '形态',
  product_line: '产品线',
};

function facetChipClass(active: boolean): string {
  return cn(
    'rounded-full border px-2.5 py-0.5 text-xs transition-colors',
    active
      ? 'border-primary/40 bg-accent font-medium text-foreground'
      : 'border-border text-muted-foreground hover:bg-accent hover:text-foreground',
  );
}

function facetOptions(projects: ProjectSummary[], field: FacetField): string[] {
  const values = new Set<string>();
  for (const p of projects) {
    const v = p[field];
    if (v) values.add(v);
  }
  return Array.from(values).sort();
}

export default function ProjectsPage() {
  useDocumentTitle('项目登记簿');
  const toast = useToast();
  const queryClient = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const [facetFilters, setFacetFilters] = useState<Partial<Record<FacetField, string>>>({});
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [mapPreview, setMapPreview] = useState<ProjectMapPreview | null>(null);
  const [inventoryOpen, setInventoryOpen] = useState(true);
  const [sheetKey, setSheetKey] = useState<string | null>(null);

  const { data: projects, isLoading, isError, error, refetch } = useQuery({
    queryKey: projectKeys.list(),
    queryFn: () => api.projects.list(),
  });

  const inventoryQ = useQuery({
    queryKey: projectKeys.inventoryModels(),
    queryFn: () => api.projects.inventoryModels(),
  });

  const summaryQ = useQuery({
    queryKey: projectKeys.inventorySummary(),
    queryFn: () => api.projects.inventorySummary(),
  });

  const invalidateProjects = () => {
    void queryClient.invalidateQueries({ queryKey: projectKeys.list() });
    void queryClient.invalidateQueries({ queryKey: projectKeys.inventoryModels() });
    void queryClient.invalidateQueries({ queryKey: projectKeys.inventorySummary() });
  };

  const createMutation = useMutation({
    mutationFn: (payload: ProjectCreateInput) => api.projects.create(payload),
    onSuccess: (created) => {
      invalidateProjects();
      setCreateOpen(false);
      toast.success(`已创建 ${created.project_key}`);
    },
    onError: (err: unknown) => {
      toast.error(`创建项目失败: ${toApiError(err).message}`);
    },
  });

  const previewMutation = useMutation({
    mutationFn: ({ projectKey, reassign }: { projectKey: string; reassign: boolean }) =>
      api.projects.mapPreview(projectKey, selectedModels, reassign),
    onSuccess: (preview) => setMapPreview(preview),
    onError: (err: unknown) => {
      toast.error(`预览失败: ${toApiError(err).message}`);
    },
  });

  const applyMutation = useMutation({
    mutationFn: ({ projectKey, reassign }: { projectKey: string; reassign: boolean }) =>
      api.projects.mapApply(projectKey, selectedModels, reassign),
    onSuccess: (preview) => {
      invalidateProjects();
      setMapOpen(false);
      setMapPreview(null);
      setSelectedModels([]);
      toast.success(`已映射 ${preview.models.join('、')}，归入 ${preview.will_assign} 台`);
    },
    onError: (err: unknown) => {
      toast.error(`映射失败: ${toApiError(err).message}`);
    },
  });

  const filtered = useMemo(() => {
    if (!projects) return [];
    return projects.filter((p) =>
      FACET_FIELDS.every((f) => !facetFilters[f] || p[f] === facetFilters[f]),
    );
  }, [projects, facetFilters]);

  const activeFacetCount = Object.values(facetFilters).filter(Boolean).length;

  const totals = useMemo(() => {
    if (!projects) return { projects: 0, devices: 0, running: 0 };
    return {
      projects: projects.length,
      devices: projects.reduce((sum, p) => sum + p.device_count, 0),
      running: projects.reduce((sum, p) => sum + p.running_run_count, 0),
    };
  }, [projects]);

  return (
    <PageContainer width="content">
      <PageHeader
        title="项目登记簿"
        subtitle="按客户与机型登记项目归属，维护 JIRA 集成。"
        action={
          isAdmin ? (
            <Button data-testid="create-project-open" onClick={() => setCreateOpen(true)}>
              <Plus className="mr-1.5 h-4 w-4" />
              新建项目
            </Button>
          ) : undefined
        }
      />

      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{totals.projects}</p>
            <p className={STAT.label}>人工项目</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{summaryQ.data?.total_devices ?? totals.devices}</p>
            <p className={STAT.label}>设备总数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{totals.running}</p>
            <p className={STAT.label}>在跑 Run</p>
          </CardContent>
        </Card>
      </div>

      {/* ── 主区块：人工项目 ───────────────────────────────── */}
      <section aria-label="人工项目" className="space-y-3">
        <div>
          <h2 className={cn('text-base font-semibold', TEXT.heading)}>项目</h2>
          <p className={cn('mt-0.5 text-xs', TEXT.subtitle)}>
            {(projects ?? []).length} 个项目 · 点击卡片查看详情，JIRA 项目键等字段在详情页维护
          </p>
        </div>

        {/* facet 筛选：chip 单选（点选中值过滤，选「全部」恢复） */}
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          {FACET_FIELDS.map((field) => (
            <div
              key={field}
              role="group"
              aria-label={`${FACET_LABEL[field]}筛选`}
              className="flex flex-wrap items-center gap-1.5"
            >
              <span className={cn('mr-0.5 text-xs', TEXT.subtitle)}>
                {FACET_LABEL[field]}
              </span>
              <button
                type="button"
                data-testid={`facet-${field}-all`}
                aria-pressed={!facetFilters[field]}
                onClick={() => setFacetFilters((prev) => ({ ...prev, [field]: undefined }))}
                className={facetChipClass(!facetFilters[field])}
              >
                全部
              </button>
              {facetOptions(projects ?? [], field).map((value) => (
                <button
                  key={value}
                  type="button"
                  data-testid={`facet-${field}-${value}`}
                  aria-pressed={facetFilters[field] === value}
                  onClick={() =>
                    setFacetFilters((prev) => ({
                      ...prev,
                      [field]: prev[field] === value ? undefined : value,
                    }))
                  }
                  className={facetChipClass(facetFilters[field] === value)}
                >
                  {value}
                </button>
              ))}
            </div>
          ))}
        </div>

        {activeFacetCount > 0 && (
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className={TEXT.subtitle}>命中 {filtered.length} 个：</span>
            {Object.entries(facetFilters)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <Badge key={k} variant="secondary" className="gap-1 py-1 pl-2.5 pr-1">
                  {FACET_LABEL[k as FacetField]}: {v}
                  <button
                    type="button"
                    aria-label={`清除${FACET_LABEL[k as FacetField]}筛选`}
                    data-testid={`facet-clear-${k}`}
                    className="rounded-full p-0.5 hover:bg-muted"
                    onClick={() => setFacetFilters((prev) => ({ ...prev, [k]: undefined }))}
                  >
                    <X className="h-3 w-3" />
                  </button>
                </Badge>
              ))}
            <button
              type="button"
              data-testid="facet-clear-all"
              onClick={() => setFacetFilters({})}
              className={cn(TEXT.subtitle, 'underline-offset-2 hover:underline')}
            >
              清空全部
            </button>
          </div>
        )}

        {isLoading ? (
          <PageSkeleton.Cards count={3} layout="grid" />
        ) : isError ? (
          <ErrorState
            title="加载项目失败"
            description={(error as Error)?.message || '请检查网络连接或稍后重试'}
            onRetry={() => void refetch()}
          />
        ) : filtered.length === 0 && !projects?.length ? (
          <EmptyState
            title="暂无项目"
            description="点击右上角「新建项目」创建第一个项目；再到下方「型号映射」把勾选的型号归入它。"
            icon={<FolderKanban className="w-16 h-16" />}
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            title="没有匹配的项目"
            description="点击筛选项的「全部」或清空全部后重试。"
            icon={<Layers className="w-16 h-16" />}
          />
        ) : (
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            {filtered.map((project) => (
              <Card
                key={project.project_key}
                data-testid="project-card"
                role="button"
                tabIndex={0}
                className="cursor-pointer transition-shadow hover:shadow-md focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setSheetKey(project.project_key)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' || event.key === ' ') {
                    event.preventDefault();
                    setSheetKey(project.project_key);
                  }
                }}
              >
                <CardContent className="py-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <h3 className={cn('truncate font-medium', TEXT.heading)}>
                        {project.display_name}
                      </h3>
                      <p className={cn('font-mono text-xs', TEXT.subtitle)}>
                        {project.project_key}
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <p className="text-xl font-bold leading-none text-foreground">
                        {project.device_count}
                      </p>
                      <p className={cn('mt-1 text-[11px]', TEXT.subtitle)}>台设备</p>
                    </div>
                  </div>

                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    {project.running_run_count > 0 ? (
                      <Badge variant="success" className="text-[11px] font-normal">
                        {project.running_run_count} 在跑
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="text-[11px] font-normal">
                        空闲
                      </Badge>
                    )}
                    {FACET_FIELDS.map((field) => {
                      const value = project[field];
                      if (!value) return null;
                      return (
                        <Badge key={field} variant="outline" className="text-[11px] font-normal">
                          {FACET_LABEL[field]}: {value}
                        </Badge>
                      );
                    })}
                  </div>

                  {(project.match_models ?? []).length > 0 ? (
                    <p
                      className={cn(
                        'mt-2 truncate font-mono text-[11px]',
                        TEXT.subtitle,
                      )}
                      title={(project.match_models ?? []).join(' · ')}
                    >
                      映射型号：{(project.match_models ?? []).join(' · ')}
                    </p>
                  ) : null}
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </section>

      {/* ── 次区块：型号映射（可折叠） ──────────────────────── */}
      <Card data-testid="inventory-section">
        <CardHeader className="pb-3">
          <div className="flex items-center justify-between gap-3">
            <button
              type="button"
              aria-expanded={inventoryOpen}
              data-testid="inventory-toggle"
              onClick={() => setInventoryOpen((open) => !open)}
              className="flex items-center gap-2 rounded-md px-1 py-0.5 -mx-1 hover:bg-accent"
            >
              <ChevronDown
                className={cn('h-4 w-4 transition-transform', !inventoryOpen && '-rotate-90')}
              />
              <span className="text-sm font-medium text-foreground">型号映射</span>
            </button>
            {isAdmin ? (
              <Button
                size="sm"
                variant="outline"
                data-testid="map-models-open"
                disabled={selectedModels.length === 0}
                onClick={() => {
                  setMapPreview(null);
                  setMapOpen(true);
                }}
              >
                <Link2 className="mr-1.5 h-4 w-4" />
                映射所选型号{selectedModels.length ? `（${selectedModels.length}）` : ''}
              </Button>
            ) : null}
          </div>
          <p className={cn('text-xs', TEXT.subtitle)}>
            设备心跳采集的型号事实，用于批量归入上方项目；归属只由人工登记决定。
          </p>
        </CardHeader>
        {inventoryOpen ? (
          <CardContent>
            <InventoryModelsTable
              models={inventoryQ.data}
              summary={summaryQ.data}
              selectedModels={selectedModels}
              onSelectedModelsChange={setSelectedModels}
              isLoading={inventoryQ.isLoading}
              isError={inventoryQ.isError}
              errorMessage={(inventoryQ.error as Error)?.message}
              onRetry={() => {
                void inventoryQ.refetch();
                void summaryQ.refetch();
              }}
            />
          </CardContent>
        ) : null}
      </Card>

      <CreateProjectDialog
        isOpen={createOpen}
        isSubmitting={createMutation.isPending}
        onClose={() => setCreateOpen(false)}
        onSubmit={(payload) => createMutation.mutate(payload)}
      />
      <MapModelsDialog
        isOpen={mapOpen}
        models={selectedModels}
        projects={projects ?? []}
        preview={mapPreview}
        isPreviewing={previewMutation.isPending}
        isSubmitting={applyMutation.isPending}
        onClose={() => {
          setMapOpen(false);
          setMapPreview(null);
        }}
        onInvalidatePreview={() => setMapPreview(null)}
        onPreview={(projectKey, reassign) =>
          previewMutation.mutate({ projectKey, reassign })
        }
        onApply={(projectKey, reassign) =>
          applyMutation.mutate({ projectKey, reassign })
        }
      />

      {/* 方向1 首个实践：卡片点击开启抽屉式详情，渐进披露 */}
      <ProjectDetailSheet
        projectKey={sheetKey}
        isOpen={!!sheetKey}
        onClose={() => setSheetKey(null)}
      />
    </PageContainer>
  );
}
