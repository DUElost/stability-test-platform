import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { PipelineStepTree, type StepUpdateMessage } from './PipelineStepTree';
import type { RunStep } from '@/utils/api';

function makeStep(overrides: Partial<RunStep> = {}): RunStep {
  return {
    id: 1,
    run_id: 100,
    phase: 'init',
    step_order: 0,
    name: 'check_device',
    action: 'script:check_device',
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
  makeStep({ id: 1, phase: 'init', step_order: 0, name: 'check_device', status: 'COMPLETED', started_at: '2026-01-01T00:00:00Z', finished_at: '2026-01-01T00:00:05Z' }),
  makeStep({ id: 2, phase: 'init', step_order: 1, name: 'ensure_root', status: 'COMPLETED', started_at: '2026-01-01T00:00:05Z', finished_at: '2026-01-01T00:00:08Z' }),
  makeStep({ id: 3, phase: 'patrol', step_order: 0, name: 'monkey_check', status: 'RUNNING', started_at: '2026-01-01T00:00:10Z' }),
  makeStep({ id: 4, phase: 'patrol', step_order: 1, name: 'check_device_again', status: 'PENDING' }),
  makeStep({ id: 5, phase: 'teardown', step_order: 0, name: 'monkey_teardown', status: 'PENDING' }),
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
    expect(screen.getByText('init')).toBeDefined();
    expect(screen.getByText('patrol')).toBeDefined();
    expect(screen.getByText('teardown')).toBeDefined();
  });

  it('renders known phases in pipeline order regardless of input order', () => {
    render(
      <PipelineStepTree
        steps={[
          makeStep({ id: 10, phase: 'patrol', step_order: 1, name: 'patrol_second' }),
          makeStep({ id: 11, phase: 'teardown', step_order: 0, name: 'teardown_second' }),
          makeStep({ id: 12, phase: 'init', step_order: 0, name: 'init_third' }),
          makeStep({ id: 13, phase: 'patrol', step_order: 0, name: 'patrol_first' }),
        ]}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    const init = screen.getByText('init');
    const patrol = screen.getByText('patrol');
    const teardown = screen.getByText('teardown');

    expect(init.compareDocumentPosition(patrol) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(patrol.compareDocumentPosition(teardown) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('auto-expands phase with RUNNING step', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // The patrol phase has a RUNNING step, so its steps should be visible
    expect(screen.getByText('monkey_check')).toBeDefined();
    expect(screen.getByText('check_device_again')).toBeDefined();
  });

  it('calls onStepSelect when a step is clicked', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // Click on a visible step (in the auto-expanded patrol phase)
    fireEvent.click(screen.getByText('monkey_check'));
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

    // teardown phase is collapsed by default (no RUNNING step)
    expect(screen.queryByText('monkey_teardown')).toBeNull();

    // Click to expand
    fireEvent.click(screen.getByText('teardown'));
    expect(screen.getByText('monkey_teardown')).toBeDefined();

    // Click to collapse
    fireEvent.click(screen.getByText('teardown'));
    expect(screen.queryByText('monkey_teardown')).toBeNull();
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

    // check_device_again should now be visible (patrol phase auto-expanded)
    expect(screen.getByText('check_device_again')).toBeDefined();
  });

  it('shows duration for completed steps', () => {
    render(
      <PipelineStepTree
        steps={sampleSteps}
        selectedStepId={null}
        onStepSelect={onStepSelect}
      />,
    );

    // Expand init phase to see completed steps
    fireEvent.click(screen.getByText('init'));

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

    const stepButton = screen.getByText('monkey_check').closest('button');
    expect(stepButton?.className).toContain('bg-accent');
  });
});
