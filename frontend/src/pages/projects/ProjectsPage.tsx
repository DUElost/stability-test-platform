import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { FolderKanban, Layers, Plus, Link2, Smartphone, Activity } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
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
  const navigate = useNavigate();
  const toast = useToast();
  const queryClient = useQueryClient();
  const sessionQ = useAuthSession();
  const isAdmin = sessionQ.data?.role === 'admin';
  const [facetFilters, setFacetFilters] = useState<Partial<Record<FacetField, string>>>({});
  const [selectedModels, setSelectedModels] = useState<string[]>([]);
  const [createOpen, setCreateOpen] = useState(false);
  const [mapOpen, setMapOpen] = useState(false);
  const [mapPreview, setMapPreview] = useState<ProjectMapPreview | null>(null);

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
        subtitle="上方是设备心跳可读的型号事实；下方是人工创建的项目。HONOR-MLD 等回填标签不出现在本页。"
        action={
          isAdmin ? (
            <>
              <Button
                variant="outline"
                data-testid="map-models-open"
                disabled={selectedModels.length === 0}
                onClick={() => {
                  setMapPreview(null);
                  setMapOpen(true);
                }}
              >
                <Link2 className="mr-1.5 h-4 w-4" />
                映射所选型号
              </Button>
              <Button data-testid="create-project-open" onClick={() => setCreateOpen(true)}>
                <Plus className="mr-1.5 h-4 w-4" />
                新建项目
              </Button>
            </>
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

      <div>
        <h2 className={cn('text-sm font-medium', TEXT.heading)}>人工项目</h2>
        <p className={cn('mt-1 text-xs', TEXT.subtitle)}>
          项目按 ADR-0029 登记簿创建：客户 / 形态 / JIRA 等设备读不到的信息。型号映射需在上方表格勾选后填写。
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {FACET_FIELDS.map((field) => (
          <Select
            key={field}
            value={facetFilters[field] ?? 'all'}
            onValueChange={(v) =>
              setFacetFilters((prev) => ({
                ...prev,
                [field]: v === 'all' ? undefined : v,
              }))
            }
          >
            <SelectTrigger data-testid={`facet-${field}`}>
              <SelectValue placeholder={FACET_LABEL[field]} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">全部{FACET_LABEL[field]}</SelectItem>
              {facetOptions(projects ?? [], field).map((value) => (
                <SelectItem key={value} value={value}>
                  {value}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ))}
      </div>
      {activeFacetCount > 0 && (
        <div className={cn('text-xs', TEXT.subtitle)}>
          已应用 {activeFacetCount} 个 facet 筛选，命中 {filtered.length} 个项目
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
      ) : (filtered.length === 0 && !projects?.length) ? (
        <EmptyState
          title="暂无项目"
          description="管理员可新建项目，再把上方型号映射过来。系统不会根据 HONOR-MLD 之类的回填标签自动建项目。"
          icon={<FolderKanban className="w-16 h-16" />}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="没有匹配的项目"
          description="调整 facet 筛选条件后重试"
          icon={<Layers className="w-16 h-16" />}
        />
      ) : (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
          {filtered.map((project) => (
            <Card
              key={project.project_key}
              data-testid="project-card"
              className="cursor-pointer transition-shadow hover:shadow-md"
              onClick={() => navigate(`/projects/${project.project_key}`)}
            >
              <CardContent className="py-4">
                <div className="min-w-0">
                  <h3 className={cn('truncate font-medium', TEXT.heading)}>
                    {project.display_name}
                  </h3>
                  <p className={cn('font-mono text-xs', TEXT.subtitle)}>
                    {project.project_key}
                  </p>
                </div>

                <div className={cn('mt-3 flex flex-wrap gap-1.5', TEXT.subtitle)}>
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

                <div className={cn('mt-3 flex items-center gap-4 text-xs', TEXT.subtitle)}>
                  <span className="flex items-center gap-1">
                    <Smartphone className="h-3.5 w-3.5" />
                    {project.device_count} 台设备
                  </span>
                  <span className="flex items-center gap-1">
                    <Activity className="h-3.5 w-3.5" />
                    {project.running_run_count} 在跑
                  </span>
                </div>
                {project.match_models.length > 0 ? (
                  <p className={cn('mt-2 font-mono text-[11px]', TEXT.subtitle)}>
                    映射型号：{project.match_models.join(' · ')}
                  </p>
                ) : (
                  <p className={cn('mt-2 text-[11px]', TEXT.subtitle)}>尚未映射型号</p>
                )}
              </CardContent>
            </Card>
          ))}
        </div>
      )}

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
    </PageContainer>
  );
}
