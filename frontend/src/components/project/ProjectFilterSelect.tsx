import { useQuery } from '@tanstack/react-query';
import { FORM } from '@/design-system';
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
 * B4 决议：全站下拉统一原生 <select>（Radix ui/select 已删除）。
 */
export function ProjectFilterSelect({
  value,
  onChange,
  className,
  testId = 'project-filter',
  showUnassigned = false,
}: ProjectFilterSelectProps) {
  const { data: projects } = useQuery({
    queryKey: projectKeys.list(),
    queryFn: () => api.projects.list(),
  });

  return (
    <select
      value={value ?? 'all'}
      onChange={(e) => onChange(e.target.value === 'all' ? undefined : e.target.value)}
      className={`${FORM.select} ${className ?? ''}`}
      data-testid={testId}
    >
      <option value="all">全部项目</option>
      {showUnassigned && (
        <option value={UNASSIGNED_FILTER_VALUE}>未归属</option>
      )}
      {projects?.map((project) => (
        <option key={project.project_key} value={project.project_key}>
          {project.display_name}（{project.project_key}）
        </option>
      ))}
    </select>
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
      className={`rounded-full bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground ${className ?? ''}`}
      title={`归属项目 ${projectKey}`}
    >
      {projectKey}
    </span>
  );
}
