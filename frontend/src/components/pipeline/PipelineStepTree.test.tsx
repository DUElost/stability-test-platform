import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { PipelineStepTree, type StepUpdateMessage } from './PipelineStepTree';
import type { RunStep } from '@/utils/api';

function makeStep(overrides: Partial<RunStep> = {}): RunStep {
  return {
    id: 1,
    run_id: 100,
    phase: 'prepare',
    step_order: 0,
    name: 'check_device',
    action: 'builtin:check_device',
    params: {},
    status: 'PENDING',
    started_at: null,
    finished_at: null,
    exit_code: null,
    error_message: null,
    log_line_count: 0,
    created_at: '2026-01-01T00:00:00Z',
    ...overrides,
  };
}

const sampleSteps: RunStep[] = [
  makeStep({ id: 1, phase: 'prepare', step_order: 0, name: 'check_device', status: 'COMPLETED', started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:00:05Z' }),
  makeStep({ id: 2, phase: 'prepare', step_order: 1, name: 'ensure_root', status: 'COMPLETED', started_at: '2026-01-01T00:00:05Z', finished_at: '2026-01-01T00:00:08Z' }),
  makeStep({ id: 3, phase: 'execute', step_order: 0, name: 'start_process', status: 'RUNNING', started_at: '2026-01-01T00:00:10Z' }),
  makeStep({ id: 4, phase: 'execute', step_order: 1, name: 'monitor_process', status: 'PENDING' }),
  makeStep({ id: 5, phase: 'post_process', step_order: 0, name: 'collect_logs', status: 'PENDING' }),
];

describe('PipelineStepTree', () => {
  const onStepSelect = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders phase groups with step names', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // Phase names should be visible
    expect(screen.getByText('prepare')).toBeDefined();
    expect(screen.getByText('execute')).toBeDefined();
    expect(screen.getByText('post_process')).toBeDefined();
  });

  it('auto-expands phase with RUNNING step', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // The execute phase has a RUNNING step, so its steps should be visible
    expect(screen.getByText('start_process')).toBeDefined();
    expect(screen.getByText('monitor_process')).toBeDefined();
  });

  it('calls onStepSelect when a step is clicked', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // Click on a visible step (in the auto-expanded execute phase)
    fireEvent.click(screen.getByText('start_process'));
    expect(onStepSelect).toHaveBeenCalledWith(3);
  });

  it('toggles phase expansion on click', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // post_process phase is collapsed by default (no RUNNING step)
    expect(screen.queryByText('collect_logs')).toBeNull();

    // Click to expand
    fireEvent.click(screen.getByText('post_process'));
    expect(screen.getByText('collect_logs')).toBeDefined();

    // Click to collapse
    fireEvent.click(screen.getByText('post_process'));
    expect(screen.queryByText('collect_logs')).toBeNull();
  });

  it('applies step updates from WebSocket', () => {
    const updates: StepUpdateMessage[] = [
      {
        type: 'STEP_UPDATE',
        step_id: 4,
        status: 'RUNNING',
        started_at: '2026-01-01T00:01:00Z',
      },
    ];

    const { rerender } = render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
        stepUpdates={[]}
      />,
    );

    // Apply updates
    rerender(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
        stepUpdates={updates}
      />,
    );

    // monitor_process should now be visible (execute phase auto-expanded)
    expect(screen.getByText('monitor_process')).toBeDefined();
  });

  it('shows duration for completed steps', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // Expand prepare phase to see completed steps
    fireEvent.click(screen.getByText('prepare'));

    // check_device took 5 seconds
    expect(screen.getByText('5s')).toBeDefined();
  });

  it('renders empty state when no steps', () => {
    render(
      <PipelineStepTree
        steps={[]}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    expect(screen.getByText('No pipeline steps')).toBeDefined();
  });

  it('highlights selected step', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={3}
        onStepSelect={onStepSelect}
      />,
    );

    const stepButton = screen.getByText('start_process').closest('button');
    expect(stepButton?.className).toContain('bg-slate-700');
  });
});
