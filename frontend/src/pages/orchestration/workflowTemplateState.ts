import type { PipelineDef, TaskTemplateEntry } from '@/utils/api';

export interface LocalTaskTemplate {
  key: string;
  id?: number;
  name: string;
  sort_order: number;
  pipeline_def: PipelineDef;
}

export function sortTemplates<T extends { sort_order?: number; name: string }>(templates: T[]): T[] {
  return [...templates].sort((a, b) => {
    const order = (a.sort_order ?? 0) - (b.sort_order ?? 0);
    return order !== 0 ? order : a.name.localeCompare(b.name);
  });
}

export function createTemplateName(existing: string[], base = 'task'): string {
  const used = new Set(existing.map((name) => name.trim()).filter(Boolean));
  if (!used.has(base)) return base;
  let suffix = 2;
  while (used.has(`${base}_${suffix}`)) {
    suffix += 1;
  }
  return `${base}_${suffix}`;
}

export function hasDuplicateTemplateNames(templates: Array<{ name: string }>): boolean {
  const names = templates.map((template) => template.name.trim()).filter(Boolean);
  return new Set(names).size !== names.length;
}

export function initLocalTaskTemplates(
  templates: TaskTemplateEntry[] | undefined,
  emptyPipeline: PipelineDef,
): LocalTaskTemplate[] {
  const source = templates && templates.length > 0
    ? sortTemplates(templates)
    : [{ name: 'default', sort_order: 0, pipeline_def: emptyPipeline }];

  return source.map((template, index) => ({
    key: 'id' in template && template.id ? String(template.id) : `new-${index}`,
    id: 'id' in template ? template.id : undefined,
    name: template.name,
    sort_order: index,
    pipeline_def: template.pipeline_def,
  }));
}

export function toTemplatePayload(templates: LocalTaskTemplate[]): Array<{
  name: string;
  sort_order: number;
  pipeline_def: PipelineDef;
}> {
  return templates.map((template, index) => ({
    name: template.name.trim(),
    sort_order: index,
    pipeline_def: template.pipeline_def,
  }));
}
