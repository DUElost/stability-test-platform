import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import DispatchPreviewDialog from './DispatchPreviewDialog';

const { previewRun, run } = vi.hoisted(() => ({
  previewRun: vi.fn(),
  run: vi.fn(),
}));

vi.mock('@/utils/api', () => ({
  api: {
    orchestration: {
      previewRun,
      run,
    },
  },
}));

function renderDialog() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <DispatchPreviewDialog
        open
        workflowId={3}
        deviceIds={[11, 12]}
        failureThreshold={0.05}
        onClose={vi.fn()}
        onStarted={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

describe('DispatchPreviewDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    previewRun.mockResolvedValue({
      workflow_definition_id: 3,
      failure_threshold: 0.05,
      device_ids: [11, 12],
      device_count: 2,
      template_count: 1,
      job_count: 2,
      executable_steps_per_device: 1,
      templates: [
        {
          id: 1,
          name: 'default',
          total_steps: 1,
          disabled_steps: 0,
          executable_steps: 1,
          resolved_pipeline: {
            stages: {
              execute: [
                {
                  step_id: 'run',
                  action: 'builtin:run_shell_script',
                  timeout_seconds: 30,
                  retry: 0,
                  params: {},
                },
              ],
            },
          },
        },
      ],
    });
    run.mockResolvedValue({ id: 99 });
  });

  it('loads preview and dispatches with the same payload shape', async () => {
    renderDialog();

    expect(await screen.findByText('default')).toBeDefined();
    fireEvent.click(screen.getByRole('button', { name: '确认发起' }));

    expect(previewRun).toHaveBeenCalledWith(3, expect.objectContaining({
      device_ids: [11, 12],
      failure_threshold: 0.05,
    }));
    await waitFor(() => {
      expect(run).toHaveBeenCalledWith(3, expect.objectContaining({
        device_ids: [11, 12],
        failure_threshold: 0.05,
      }));
    });
  });
});
