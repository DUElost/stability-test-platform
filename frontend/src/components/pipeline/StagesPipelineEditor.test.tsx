import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import StagesPipelineEditor from './StagesPipelineEditor';
import type { PipelineDef, PipelineStep } from '@/utils/api';

function makePipeline(steps: PipelineStep[]): PipelineDef {
  return {
    stages: {
      prepare: steps,
      execute: [],
      post_process: [],
    },
  };
}

function StatefulEditor({
  initialValue,
  onChange,
}: {
  initialValue: PipelineDef;
  onChange: (value: PipelineDef) => void;
}) {
  const [value, setValue] = useState(initialValue);
  return (
    <StagesPipelineEditor
      value={value}
      onChange={(next) => {
        onChange(next);
        setValue(next);
      }}
    />
  );
}

describe('StagesPipelineEditor', () => {
  it('renders script actions using script metadata', () => {
    const value: PipelineDef = {
      stages: {
        prepare: [
          {
            step_id: 'push_bundle',
            action: 'script:push_bundle',
            version: '2.0.0',
            params: { bundle_name: 'audio' },
            timeout_seconds: 600,
          },
        ],
        execute: [],
        post_process: [],
      },
    };

    render(
      <StagesPipelineEditor
        value={value}
        onChange={vi.fn()}
        scriptOptions={[
          {
            id: 1,
            name: 'push_bundle',
            version: '2.0.0',
            category: 'resource',
            script_type: 'python',
            param_schema: { bundle_name: { type: 'string', required: true } },
            is_active: true,
          },
        ]}
      />,
    );

    expect(screen.getByText('script:push_bundle')).toBeDefined();
  });

  it('edits simple step fields inline', () => {
    const onChange = vi.fn();
    render(
      <StatefulEditor
        initialValue={makePipeline([
          {
            step_id: 'check',
            action: 'builtin:check_device',
            params: {},
            timeout_seconds: 30,
            retry: 0,
          },
        ])}
        onChange={onChange}
      />,
    );

    fireEvent.change(screen.getByLabelText('Step ID check'), { target: { value: 'check_phone' } });
    fireEvent.change(screen.getByLabelText('Timeout check_phone'), { target: { value: '45' } });
    fireEvent.change(screen.getByLabelText('Retry check_phone'), { target: { value: '2' } });

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      stages: expect.objectContaining({
        prepare: [
          expect.objectContaining({
            step_id: 'check_phone',
            timeout_seconds: 45,
            retry: 2,
          }),
        ],
      }),
    }));
  });

  it('duplicates and disables a step', () => {
    const onChange = vi.fn();
    render(
      <StatefulEditor
        initialValue={makePipeline([
          {
            step_id: 'push_resources',
            action: 'builtin:push_resources',
            params: { bundle: 'a.tar.gz' },
            timeout_seconds: 300,
            retry: 0,
          },
        ])}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByLabelText('复制 Step push_resources'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      stages: expect.objectContaining({
        prepare: [
          expect.objectContaining({ step_id: 'push_resources' }),
          expect.objectContaining({ step_id: 'push_resources_copy', enabled: true }),
        ],
      }),
    }));

    fireEvent.click(screen.getByLabelText('禁用 Step push_resources'));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      stages: expect.objectContaining({
        prepare: [
          expect.objectContaining({ step_id: 'push_resources', enabled: false }),
          expect.objectContaining({ step_id: 'push_resources_copy', enabled: true }),
        ],
      }),
    }));
  });

  it('moves steps up and down without opening the drawer', () => {
    const onChange = vi.fn();
    render(
      <StatefulEditor
        initialValue={makePipeline([
          {
            step_id: 'first',
            action: 'builtin:check_device',
            params: {},
            timeout_seconds: 30,
            retry: 0,
          },
          {
            step_id: 'second',
            action: 'builtin:ensure_root',
            params: {},
            timeout_seconds: 30,
            retry: 0,
          },
        ])}
        onChange={onChange}
      />,
    );

    fireEvent.click(screen.getByLabelText('下移 Step first'));

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      stages: expect.objectContaining({
        prepare: [
          expect.objectContaining({ step_id: 'second' }),
          expect.objectContaining({ step_id: 'first' }),
        ],
      }),
    }));
  });
});
