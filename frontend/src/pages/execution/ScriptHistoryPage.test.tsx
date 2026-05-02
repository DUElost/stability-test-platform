import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import ScriptHistoryPage from './ScriptHistoryPage';

vi.mock('@/utils/api', () => ({
  api: {
    scriptExecutions: {
      list: vi.fn().mockResolvedValue({
        items: [{
          workflow_run_id: 42,
          status: 'RUNNING',
          started_at: '2026-04-26T01:00:00Z',
          step_count: 2,
          script_names: 'run_monkey → collect_logs',
          device_count: 1,
          device_serials: ['SERIAL001'],
          host_name: '实验室主机',
        }],
        total: 1,
        skip: 0,
        limit: 50,
      }),
      get: vi.fn().mockResolvedValue({
        workflow_run_id: 42,
        mode: 'script_execution',
        status: 'RUNNING',
        items: [
          { script_name: 'run_monkey', version: '1.0.0', params: {}, timeout_seconds: 120, retry: 0 },
          { script_name: 'collect_logs', version: '1.0.0', params: {}, timeout_seconds: 60, retry: 0 },
        ],
        on_failure: 'stop',
        jobs: [{
          id: 200,
          device_id: 7,
          device_serial: 'SERIAL001',
          device_model: 'Pixel',
          host_id: '101',
          host_name: '实验室主机',
          status: 'RUNNING',
          watcher_capability: 'aee',
          log_signal_count: 5,
          started_at: '2026-04-26T01:00:00Z',
          steps: [
            { step_id: 's1', script_name: 'run_monkey', status: 'RUNNING', params: {}, retry: 0, output: null, error_message: null },
            { step_id: 's2', script_name: 'collect_logs', status: 'PENDING', params: {}, retry: 0, output: null, error_message: null },
          ],
          artifacts: [],
        }],
      }),
      rerun: vi.fn().mockResolvedValue({ workflow_run_id: 43, job_ids: [2], device_count: 1, step_count: 2 }),
    },
  },
}));

vi.mock('@/components/ui/toast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={['/history?run=42']}>
      <QueryClientProvider client={queryClient}>
        <ScriptHistoryPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('ScriptHistoryPage', () => {
  it('renders script execution history', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '执行记录' })).toBeInTheDocument();
    expect(await screen.findAllByText('Run #42')).toHaveLength(2); // sidebar + detail header
    expect(await screen.findByText('SERIAL001')).toBeInTheDocument();
    // summarizeDetail: 2 steps, 0 success, 0 failed, 5 signals
    expect(await screen.findByText(/2 步/)).toBeInTheDocument();
    expect(await screen.findByText(/crash 信号 5 个/)).toBeInTheDocument();
    expect(await screen.findAllByText(/run_monkey/)).toHaveLength(3); // sidebar, detail header, step 1
  });
});
