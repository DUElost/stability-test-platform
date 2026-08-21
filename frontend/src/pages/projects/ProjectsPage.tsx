import { useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { FolderKanban, Layers, Smartphone, Activity } from 'lucide-react';
import { Card, CardContent } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
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
import { api } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';
import type { ProjectSummary } from '@/utils/api/types';
import InventoryModelsTable from './components/InventoryModelsTable';

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
  useDocumentTitle('项目编组工作台');
  const navigate = useNavigate();
  const [facetFilters, setFacetFilters] = useState<Partial<Record<FacetField, string>>>({});

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
        title="项目编组工作台"
        subtitle="上方是设备心跳可读的型号事实；已映射项目需人工填写，不由 HONOR-MLD 等回填标签推断"
      />

      <div className="grid grid-cols-3 gap-4">
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{totals.projects}</p>
            <p className={STAT.label}>回填标签数</p>
          </CardContent>
        </Card>
        <Card>
          <CardContent className="py-4 text-center">
            <p className={STAT.value}>{totals.devices}</p>
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
        isLoading={inventoryQ.isLoading}
        isError={inventoryQ.isError}
        errorMessage={(inventoryQ.error as Error)?.message}
        onRetry={() => {
          void inventoryQ.refetch();
          void summaryQ.refetch();
        }}
      />

      <div>
        <h2 className={cn('text-sm font-medium', TEXT.heading)}>系统回填标签（非正式编组）</h2>
        <p className={cn('mt-1 text-xs', TEXT.subtitle)}>
          HONOR-MLD、ZTE-Z258 等来自 P1 脚本回填，方便按当时设备归属查看，
          既不能代表一个客户，也不能代表一个项目或机型。项目映射请在上方表格「已映射项目」列人工填写（后续开放编辑）。
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
          已应用 {activeFacetCount} 个 facet 筛选，命中 {filtered.length} 个回填标签
        </div>
      )}

      {isLoading ? (
        <PageSkeleton.Cards count={3} layout="grid" />
      ) : isError ? (
        <ErrorState
          title="加载回填标签失败"
          description={(error as Error)?.message || '请检查网络连接或稍后重试'}
          onRetry={() => void refetch()}
        />
      ) : (filtered.length === 0 && !projects?.length) ? (
        <EmptyState
          title="暂无回填标签"
          description="可先查看上方 Fleet 型号分布。项目编组与映射规则将在后续开放编辑"
          icon={<FolderKanban className="w-16 h-16" />}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          title="没有匹配的回填标签"
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
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <h3 className={cn('truncate font-medium', TEXT.heading)}>
                      {project.display_name}
                    </h3>
                    <p className={cn('font-mono text-xs', TEXT.subtitle)}>
                      {project.project_key}
                    </p>
                  </div>
                  <Badge variant="secondary" className="shrink-0 text-[11px] font-normal">
                    非正式回填
                  </Badge>
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
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </PageContainer>
  );
}
