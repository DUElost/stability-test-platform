import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { FolderKanban } from 'lucide-react';
import { SearchableSelect, type SearchableSelectOption } from '@/components/ui/searchable-select';
import { api } from '@/utils/api';
import { projectKeys } from '@/utils/api/queryKeys';

interface ProjectFilterSelectProps {
  /** undefined = 全部项目（页面级筛选，无全局上下文——D8 挂起） */
  value: string | undefined;
  onChange: (value: string | undefined) => void;
  className?: string;
  testId?: string;
  /** ADR-0029 P0：设备页专用——追加「未归属」选项（选中传 '__unassigned__'） */
  showUnassigned?: boolean;
}

/** 未归属筛选的哨兵值（DevicesPage 用它切换 ?unassigned=true，不发给后端当 project_key）。 */
export const UNASSIGNED_FILTER_VALUE = '__unassigned__';

/**
 * ADR-0029 P2 — 页面级项目筛选下拉（设备 / Plan / PlanRun / 结果页共用）。
 * 选中的 key 传后端 ?project_key= 过滤（未知 key 后端 404，页面按错误态渲染）。
 * 不做跨页跟随：页面刷新后回到「全部」，不读 URL / localStorage。
 */
export function ProjectFilterSelect({
  value,
  onChange,
  className,
  testId = 'project-filter',
  showUnassigned = false,
}: ProjectFilterSelectProps) {
  const { data: projects } = useQuery({
    queryKey: projectKeys.active(),
    queryFn: () => api.projects.listActive(),
  });

  const options = useMemo<SearchableSelectOption[]>(() => {
    const items: SearchableSelectOption[] = [];
    if (showUnassigned) {
      items.push({ value: UNASSIGNED_FILTER_VALUE, label: '未归属' });
    }
    if (projects) {
      for (const project of projects) {
        items.push({
          value: project.project_key,
          label: `${project.display_name}（${project.project_key}）`,
        });
      }
    }
    return items;
  }, [projects, showUnassigned]);

  return (
    <SearchableSelect
      value={value ?? 'all'}
      onChange={(next) => onChange(next === 'all' ? undefined : next)}
      options={options}
      allLabel="全部项目"
      searchPlaceholder="搜索项目名 / key..."
      icon={<FolderKanban />}
      ariaLabel="按项目筛选"
      className={className}
      testId={testId}
    />
  );
}

/** 项目 key 标签（列表行内小 badge，复用于五个页面，含 PlanRun 详情 Hero）。 */
export function ProjectKeyBadge({
  projectKey,
  className,
}: {
  projectKey?: string | null;
  className?: string;
}) {
  if (!projectKey) return null;
  return (
    <span
      className={`rounded-full bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground ${className ?? ''}`}
      title={`归属项目 ${projectKey}`}
    >
      {projectKey}
    </span>
  );
}
