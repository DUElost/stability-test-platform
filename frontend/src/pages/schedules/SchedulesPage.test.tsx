import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mocks = vi.hoisted(() => ({
  schedulesList: vi.fn(),
  plansList: vi.fn(),
  schedulesCreate: vi.fn(),
  schedulesUpdate: vi.fn(),
  schedulesToggle: vi.fn(),
  schedulesRunNow: vi.fn(),
  schedulesDelete: vi.fn(),
  confirm: vi.fn().mockResolvedValue(false),
}));

vi.mock('@/utils/api', () => ({
  api: {
    schedules: {
      list: (...a: unknown[]) => mocks.schedulesList(...a),
      create: (...a: unknown[]) => mocks.schedulesCreate(...a),
      update: (...a: unknown[]) => mocks.schedulesUpdate(...a),
      toggle: (...a: unknown[]) => mocks.schedulesToggle(...a),
      runNow: (...a: unknown[]) => mocks.schedulesRunNow(...a),
      delete: (...a: unknown[]) => mocks.schedulesDelete(...a),
    },
    plans: {
      list: (...a: unknown[]) => mocks.plansList(...a),
    },
  },
  toApiError: (e: unknown) => ({ message: String(e) }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn() }),
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => mocks.confirm,
}));

import SchedulesPage from './SchedulesPage';

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, refetchOnWindowFocus: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <SchedulesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('SchedulesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.plansList.mockResolvedValue([]);
    mocks.schedulesList.mockResolvedValue({ items: [], total: 0 });
  });

  it('renders cron values from the cron_expr wire field', async () => {
    mocks.schedulesList.mockResolvedValue({
      items: [{
        id: 1,
        name: '夜跑',
        cron_expr: '0 2 * * *',
        plan_id: 7,
        device_ids: [1, 2],
        enabled: true,
        created_at: '2026-08-14T00:00:00Z',
      }],
      total: 1,
    });

    renderPage();

    expect(await screen.findByText('夜跑')).toBeInTheDocument();
    expect(screen.getByText('0 2 * * *')).toBeInTheDocument();
  });

  it('prefills the edit form from cron_expr without crashing', async () => {
    mocks.schedulesList.mockResolvedValue({
      items: [{
        id: 2,
        name: '早跑',
        cron_expr: '0 6 * * *',
        plan_id: 8,
        device_ids: [3],
        enabled: true,
        created_at: '2026-08-14T00:00:00Z',
      }],
      total: 1,
    });

    renderPage();

    const edit = await screen.findByRole('button', { name: '编辑' });
    fireEvent.click(edit);

    await waitFor(() => {
      expect(screen.getByDisplayValue('0 6 * * *')).toBeInTheDocument();
    });
  });

  it('ignores duplicate run-now clicks while request is in flight (#820)', async () => {
    let resolveRun!: (v: { plan_run_id: number }) => void;
    mocks.schedulesRunNow.mockImplementation(
      () => new Promise((resolve) => { resolveRun = resolve; }),
    );
    mocks.schedulesList.mockResolvedValue({
      items: [{
        id: 9,
        name: 'dup',
        cron_expr: '0 1 * * *',
        plan_id: 1,
        device_ids: [1],
        enabled: true,
        created_at: '2026-08-14T00:00:00Z',
      }],
      total: 1,
    });

    renderPage();
    const runBtn = await screen.findByRole('button', { name: '立即执行' });
    fireEvent.click(runBtn);
    fireEvent.click(runBtn);

    await waitFor(() => expect(mocks.schedulesRunNow).toHaveBeenCalledTimes(1));

    resolveRun({ plan_run_id: 42 });
    await waitFor(() => expect(mocks.schedulesRunNow).toHaveBeenCalledTimes(1));
  });

  it('新建表单的 Plan 选择器可搜索并回填（#627）', async () => {
    mocks.plansList.mockResolvedValue(
      Array.from({ length: 100 }, (_, i) => ({
        id: i + 1,
        name: `夜跑计划 ${i + 1}`,
        failure_threshold: 1,
        patrol_interval_seconds: null,
        timeout_seconds: null,
      })),
    );

    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: /新建定时任务/ }));

    // label htmlFor 关联后该按钮的可访问名为「Plan 蓝图」（a11y 期望如此），
    // 故按可访问名定位触发器，内容断言用 toHaveTextContent 看已选回填。
    fireEvent.click(await screen.findByRole('button', { name: 'Plan 蓝图' }));
    fireEvent.change(screen.getByLabelText('搜索 Plan'), { target: { value: '87' } });
    fireEvent.click(screen.getByRole('button', { name: '选择 Plan 夜跑计划 87' }));

    // 选择后收起并回到触发器展示已选
    expect(screen.queryByLabelText('搜索 Plan')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Plan 蓝图' })).toHaveTextContent(
      '夜跑计划 87 (#87)',
    );
    expect(
      screen.getByRole('button', { name: '清除已选 Plan 夜跑计划 87' }),
    ).toBeInTheDocument();
  });
});
