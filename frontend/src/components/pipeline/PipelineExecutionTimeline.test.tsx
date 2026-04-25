import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import PipelineExecutionTimeline from './PipelineExecutionTimeline';
import type { PipelineDef } from '@/utils/api';

function pipeline(stages: PipelineDef['stages']): PipelineDef {
  return { stages };
}

describe('PipelineExecutionTimeline', () => {
  it('renders setup task and teardown groups in execution order', () => {
    render(
      <PipelineExecutionTimeline
        setupPipeline={pipeline({ prepare: [{ step_id: 'setup', action: 'builtin:check_device', timeout_seconds: 1 }] })}
        taskPipeline={pipeline({
          prepare: [{ step_id: 'task_prepare', action: 'builtin:check_device', timeout_seconds: 1 }],
          execute: [{ step_id: 'task_execute', action: 'builtin:check_device', timeout_seconds: 1 }],
          post_process: [{ step_id: 'task_post', action: 'builtin:check_device', timeout_seconds: 1 }],
        })}
        teardownPipeline={pipeline({ post_process: [{ step_id: 'teardown', action: 'builtin:check_device', timeout_seconds: 1 }] })}
      />,
    );

    const labels = [
      screen.getByText('Setup Prepare'),
      screen.getByText('Task Prepare'),
      screen.getByText('Task Execute'),
      screen.getByText('Task Post Process'),
      screen.getByText('Teardown Post Process'),
    ];

    for (let index = 0; index < labels.length - 1; index += 1) {
      expect(labels[index].compareDocumentPosition(labels[index + 1]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(screen.getByText('setup')).toBeDefined();
    expect(screen.getByText('task_execute')).toBeDefined();
    expect(screen.getByText('teardown')).toBeDefined();
  });
});
