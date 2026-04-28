import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import ScriptHistoryPage from './ScriptHistoryPage';

vi.mock('@/utils/api', () => ({
  api: {
    scriptBatches: {
      list: vi.fn().mockResolvedValue({
        items: [{ id: 42, device_serial: 'SERIAL001', status: 'RUNNING', started_at: '2026-04-26T01:00:00Z', step_count: 2, script_names: 'run_monkey' }],
        total: 1,
      }),
      get: vi.fn().mockResolvedValue({
        id: 42,
        device_id: 7,
        device_serial: 'SERIAL001',
        device_model: 'Pixel',
        host_id: '101',
        host_name: '实验室主机',
        status: 'RUNNING',
        on_failure: 'stop',
        log_signal_count: 5,
        runs: [
          { id: 200, batch_id: 42, item_index: 0, script_name: 'run_monkey', script_version: '1.0.0', params_json: {}, status: 'RUNNING', exit_code: null, stdout: null, stderr: null, metrics_json: null, started_at: null, ended_at: null },
        ],
      }),
      rerun: vi.fn().mockResolvedValue({ id: 43, device_id: 7, device_serial: 'SERIAL001', runs: [] }),
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
    <MemoryRouter initialEntries={['/history?batch=42']}>
      <QueryClientProvider client={queryClient}>
        <ScriptHistoryPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('ScriptHistoryPage', () => {
  it('renders batch execution history', async () => {
    renderPage();

    expect(screen.getByRole('heading', { name: '执行记录' })).toBeInTheDocument();
    expect(await screen.findByText('Batch #42')).toBeInTheDocument();
    expect(await screen.findByText('SERIAL001')).toBeInTheDocument();
    expect(await screen.findByText(/1 步/)).toBeInTheDocument();
    expect(await screen.findByText(/crash 信号 5 个/)).toBeInTheDocument();
    expect(await screen.findByText(/run_monkey/)).toBeInTheDocument();
  });
});
