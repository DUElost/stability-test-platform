/**
 * TypeScript types for pipeline definition editing.
 * Mirrors backend/schemas/pipeline_schema.json
 */

export interface PipelineStep {
  name: string;
  action: string; // "builtin:<name>" | "tool:<id>" | "shell:<command>"
  params: Record<string, any>;
  timeout: number;
  on_failure: 'stop' | 'continue' | 'retry';
  max_retries: number;
}

export interface PipelinePhase {
  name: string;
  parallel: boolean;
  steps: PipelineStep[];
}

export interface PipelineDef {
  version: 1;
  phases: PipelinePhase[];
}

/** Create an empty step with defaults */
export function createEmptyStep(name = ''): PipelineStep {
  return {
    name,
    action: 'builtin:check_device',
    params: {},
    timeout: 300,
    on_failure: 'stop',
    max_retries: 0,
  };
}

/** Create an empty phase with defaults */
export function createEmptyPhase(name = ''): PipelinePhase {
  return {
    name,
    parallel: false,
    steps: [createEmptyStep('step_1')],
  };
}

/** Create an empty pipeline def */
export function createEmptyPipeline(): PipelineDef {
  return {
    version: 1,
    phases: [createEmptyPhase('prepare')],
  };
}

/** Extract action type prefix (builtin/tool/shell) */
export function getActionPrefix(action: string): string {
  const idx = action.indexOf(':');
  return idx > 0 ? action.substring(0, idx) : '';
}

/** Extract action name (after prefix) */
export function getActionName(action: string): string {
  const idx = action.indexOf(':');
  return idx > 0 ? action.substring(idx + 1) : action;
}
