import { describe, expect, it } from 'vitest';
import type { PipelineDef, TaskTemplateEntry } from '@/utils/api';
import {
  createTemplateName,
  hasDuplicateTemplateNames,
  initLocalTaskTemplates,
  sortTemplates,
  toTemplatePayload,
} from './workflowTemplateState';

const EMPTY: PipelineDef = { stages: { prepare: [], execute: [], post_process: [] } };

describe('workflowTemplateState', () => {
  it('sorts templates by sort_order then name', () => {
    expect(sortTemplates([
      { name: 'b', sort_order: 2 },
      { name: 'a', sort_order: 1 },
      { name: 'c', sort_order: 1 },
    ])).toEqual([
      { name: 'a', sort_order: 1 },
      { name: 'c', sort_order: 1 },
      { name: 'b', sort_order: 2 },
    ]);
  });

  it('creates unique template names', () => {
    expect(createTemplateName(['task', 'task_2'], 'task')).toBe('task_3');
    expect(createTemplateName(['default'], 'task')).toBe('task');
  });

  it('initializes local templates from API entries', () => {
    const templates: TaskTemplateEntry[] = [
      {
        id: 1,
        workflow_definition_id: 9,
        name: 'monkey',
        sort_order: 2,
        pipeline_def: EMPTY,
      },
    ];

    expect(initLocalTaskTemplates(templates, EMPTY)).toEqual([
      {
        key: '1',
        id: 1,
        name: 'monkey',
        sort_order: 0,
        pipeline_def: EMPTY,
      },
    ]);
  });

  it('detects duplicate template names after trimming', () => {
    expect(hasDuplicateTemplateNames([
      { name: 'monkey' },
      { name: ' monkey ' },
    ])).toBe(true);
  });

  it('builds sorted save payload', () => {
    expect(toTemplatePayload([
      { key: 'b', name: 'second', sort_order: 0, pipeline_def: EMPTY },
      { key: 'a', name: 'first', sort_order: 0, pipeline_def: EMPTY },
    ])).toEqual([
      { name: 'second', sort_order: 0, pipeline_def: EMPTY },
      { name: 'first', sort_order: 1, pipeline_def: EMPTY },
    ]);
  });
});
