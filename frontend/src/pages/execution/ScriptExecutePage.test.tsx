import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import ScriptExecutePage from './ScriptExecutePage';

vi.mock('@/utils/api', () => ({
  api: {
    scripts: {
      list: vi.fn().mockResolvedValue([
        {
          id: 1,
          name: 'run_monkey',
          display_name: 'Run Monkey',
          category: 'app',
          script_type: 'python',
          version: '1.0.0',
          nfs_path: '/nfs/run_monkey.py',
          param_schema: {
            duration: { type: 'number', label: '运行时长', required: true },
          },
          content_sha256: 'b'.repeat(64),
          is_active: true,
        },
      ]),
    },
    scriptSequences: {
      list: vi.fn().mockResolvedValue({
        items: [
          {
            id: 9,
            name: 'Monkey 模板',
            items: [{ script_name: 'run_monkey', version: '1.0.0', params: { duration: 60 }, timeout_seconds: 120, retry: 0 }],
            on_failure: 'stop',
            created_at: '2026-04-26T01:00:00Z',
            updated_at: '2026-04-26T01:00:00Z',
          },
        ],
        total: 1,
        skip: 0,
        limit: 100,
      }),
      create: vi.fn(),
      update: vi.fn(),
    },
    scriptExecutions: { create: vi.fn().mockResolvedValue({ workflow_run_id: 42, job_ids: [1], device_count: 1, step_count: 1 }) },
    devices: {
      list: vi.fn().mockResolvedValue({
        data: {
          items: [{ id: 7, serial: 'SERIAL001', model: 'Pixel', host_id: '101', status: 'ONLINE' }],
        },
      }),
    },
    hosts: {
      list: vi.fn().mockResolvedValue({
        data: {
          items: [{ id: '101', name: '实验室主机', ip: '10.0.0.1', status: 'ONLINE' }],
        },
      }),
    },
  },
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ScriptExecutePage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('ScriptExecutePage', () => {
  it('renders scripts and target devices', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '执行任务' })).toBeInTheDocument();
    expect(await screen.findByText('run_monkey')).toBeInTheDocument();
    expect(await screen.findByText('SERIAL001')).toBeInTheDocument();
    expect(await screen.findByText('实验室主机')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /加入/ }));

    expect(screen.getAllByText(/v1.0.0/).length).toBeGreaterThan(0);
    expect(screen.getByText(/python/)).toBeInTheDocument();
    expect(screen.getByLabelText(/运行时长/)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('combobox'), { target: { value: '9' } });
    expect(await screen.findByText(/基于模板：Monkey 模板/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /更新模板/ })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /另存为新模板/ })).toBeInTheDocument();
  });
});
