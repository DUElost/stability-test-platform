import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import PipelineExecutionTimeline from './PipelineExecutionTimeline';
import type { PipelineDef } from '@/utils/api';

function pipeline(lifecycle: PipelineDef['lifecycle']): PipelineDef {
  return { lifecycle };
}

describe('PipelineExecutionTimeline', () => {
  it('renders setup task and teardown groups in execution order', () => {
    render(
      <PipelineExecutionTimeline
        setupPipeline={pipeline({ init: [{ step_id: 'setup', action: 'script:check_device', version: '1.0.0', timeout_seconds: 1 }], teardown: [] })}
        taskPipeline={pipeline({
          init: [{ step_id: 'task_init', action: 'script:check_device', version: '1.0.0', timeout_seconds: 1 }],
          patrol: { interval_seconds: 60, steps: [{ step_id: 'task_patrol', action: 'script:check_device', version: '1.0.0', timeout_seconds: 1 }] },
          teardown: [{ step_id: 'task_teardown', action: 'script:check_device', version: '1.0.0', timeout_seconds: 1 }],
        })}
        teardownPipeline={pipeline({ init: [], teardown: [{ step_id: 'teardown', action: 'script:check_device', version: '1.0.0', timeout_seconds: 1 }] })}
      />,
    );

    const labels = [
      screen.getByText('Setup Init'),
      screen.getByText('Task Init'),
      screen.getByText('Task Patrol'),
      screen.getByText('Task Teardown'),
      screen.getByText('Teardown'),
    ];

    for (let index = 0; index < labels.length - 1; index += 1) {
      expect(labels[index].compareDocumentPosition(labels[index + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByText('setup')).toBeDefined();
    expect(screen.getByText('task_patrol')).toBeDefined();
    expect(screen.getByText('teardown')).toBeDefined();
  });
});
