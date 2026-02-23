import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { PipelineEditor } from './PipelineEditor';
import type { PipelineDef } from './pipelineTypes';
import { createEmptyPipeline, createEmptyPhase, createEmptyStep } from './pipelineTypes';

function makePipeline(overrides: Partial<PipelineDef> = {}): PipelineDef {
  return {
    version: 1,
    phases: [
      {
        name: 'prepare',
        parallel: false,
        steps: [
          { name: 'check_device', action: 'builtin:check_device', params: {}, timeout: 30, on_failure: 'stop', max_retries: 0 },
          { name: 'clean_env', action: 'builtin:clean_env', params: { clear_logs: true }, timeout: 60, on_failure: 'continue', max_retries: 0 },
        ],
      },
      {
        name: 'execute',
        parallel: false,
        steps: [
          { name: 'run_test', action: 'builtin:start_process', params: { command: 'monkey -v 1000' }, timeout: 3600, on_failure: 'stop', max_retries: 0 },
        ],
      },
    ],
    ...overrides,
  };
}

describe('PipelineEditor', () => {
  let onChange: (def: PipelineDef) => void;
  let onChangeMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    onChangeMock = vi.fn();
    onChange = onChangeMock as unknown as (def: PipelineDef) => void;
  });

  it('renders phase cards with phase names', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // Phase names should be visible as input values
    const inputs = screen.getAllByDisplayValue(/prepare|execute/);
    expect(inputs.length).toBeGreaterThanOrEqual(2);
  });

  it('renders step count summary', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // Should show "2 phases, 3 steps"
    expect(screen.getByText(/2 phases, 3 steps/)).toBeDefined();
  });

  it('shows step names in collapsed view', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    expect(screen.getByText('check_device')).toBeDefined();
    expect(screen.getByText('clean_env')).toBeDefined();
    expect(screen.getByText('run_test')).toBeDefined();
  });

  it('adds a new phase when clicking Add Phase', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    fireEvent.click(screen.getByText('Add Phase'));
    expect(onChangeMock).toHaveBeenCalledTimes(1);

    const newDef = onChangeMock.mock.calls[0][0] as PipelineDef;
    expect(newDef.phases.length).toBe(3);
    expect(newDef.phases[2].name).toBe('phase_3');
  });

  it('adds a new step when clicking Add Step', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // There should be two "Add Step" buttons (one per phase)
    const addStepButtons = screen.getAllByText('Add Step');
    expect(addStepButtons.length).toBe(2);

    fireEvent.click(addStepButtons[0]); // Add to first phase
    expect(onChangeMock).toHaveBeenCalledTimes(1);

    const newDef = onChangeMock.mock.calls[0][0] as PipelineDef;
    expect(newDef.phases[0].steps.length).toBe(3);
  });

  it('removes a phase when clicking remove button', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // Find remove phase buttons (Trash2 icons in phase headers)
    const removeButtons = screen.getAllByTitle('Remove Phase');
    expect(removeButtons.length).toBe(2);

    fireEvent.click(removeButtons[1]); // Remove the second phase
    expect(onChangeMock).toHaveBeenCalledTimes(1);

    const newDef = onChangeMock.mock.calls[0][0] as PipelineDef;
    expect(newDef.phases.length).toBe(1);
    expect(newDef.phases[0].name).toBe('prepare');
  });

  it('does not remove the last phase', () => {
    const singlePhase = makePipeline({
      phases: [makePipeline().phases[0]],
    });

    render(<PipelineEditor value={singlePhase} onChange={onChange} />);

    const removeButton = screen.getByTitle('Remove Phase');
    expect(removeButton).toBeDefined();
    // The button should be disabled when there's only one phase
    expect(removeButton.hasAttribute('disabled')).toBe(true);
  });

  it('toggles JSON preview', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // JSON button
    fireEvent.click(screen.getByText('JSON'));
    expect(screen.getByText(/Pipeline JSON/)).toBeDefined();

    // Should show the JSON content
    expect(screen.getByText(/"version": 1/)).toBeDefined();
  });

  it('shows on_failure badge for non-stop steps', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // clean_env has on_failure: "continue"
    expect(screen.getByText('continue')).toBeDefined();
  });

  it('expands step to show details on click', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    // Click on a step row to expand it
    fireEvent.click(screen.getByText('check_device'));

    // Should see parameter labels after expansion
    expect(screen.getByText('Name')).toBeDefined();
    expect(screen.getByText('Action')).toBeDefined();
    expect(screen.getByText(/Timeout/)).toBeDefined();
  });

  it('duplicates a phase', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} />);

    const dupButtons = screen.getAllByTitle('Duplicate Phase');
    fireEvent.click(dupButtons[0]); // Duplicate first phase

    const newDef = onChangeMock.mock.calls[0][0] as PipelineDef;
    expect(newDef.phases.length).toBe(3);
    expect(newDef.phases[1].name).toBe('prepare_copy');
  });

  it('validates pipeline and shows errors in JSON preview', () => {
    const invalid: PipelineDef = {
      version: 1,
      phases: [
        {
          name: '',
          parallel: false,
          steps: [],
        },
      ],
    };

    render(<PipelineEditor value={invalid} onChange={onChange} />);

    // Open JSON preview
    fireEvent.click(screen.getByText('JSON'));

    // Should show validation errors
    expect(screen.getByText(/name is required/)).toBeDefined();
    expect(screen.getByText(/at least one step is required/)).toBeDefined();
  });

  it('renders in readOnly mode without edit controls', () => {
    render(<PipelineEditor value={makePipeline()} onChange={onChange} readOnly />);

    // Should not have Add Phase or Add Step buttons
    expect(screen.queryByText('Add Phase')).toBeNull();
    expect(screen.queryByText('Add Step')).toBeNull();
  });

  it('creates valid empty pipeline from factory', () => {
    const pipeline = createEmptyPipeline();
    expect(pipeline.version).toBe(1);
    expect(pipeline.phases.length).toBe(1);
    expect(pipeline.phases[0].steps.length).toBe(1);
  });

  it('creates valid empty phase from factory', () => {
    const phase = createEmptyPhase('test');
    expect(phase.name).toBe('test');
    expect(phase.parallel).toBe(false);
    expect(phase.steps.length).toBe(1);
  });

  it('creates valid empty step from factory', () => {
    const step = createEmptyStep('my_step');
    expect(step.name).toBe('my_step');
    expect(step.action).toBe('builtin:check_device');
    expect(step.timeout).toBe(300);
    expect(step.on_failure).toBe('stop');
  });
});
