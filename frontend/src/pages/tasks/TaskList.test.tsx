import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

// Mock api
vi.mock('../../utils/api', () => ({
  api: {
    tasks: {
      list: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      cancel: vi.fn().mockResolvedValue({}),
      retry: vi.fn().mockResolvedValue({}),
      batchCancel: vi.fn().mockResolvedValue({ data: { success: [], failed: [], total: 0 } }),
      batchRetry: vi.fn().mockResolvedValue({ data: { success: [], failed: [], total: 0 } }),
    },
  },
}));

// Mock toast
vi.mock('../../components/ui/toast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  }),
}));

// Mock confirm
vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

// Mock TaskDataTable
vi.mock('../../components/task/TaskDataTable', () => ({
  TaskDataTable: ({ tasks, onViewDetail, onCancelTask, onRetryTask }: any) => (
    <div data-testid="task-data-table">
      {tasks.map((t: any) => (
        <div key={t.id} data-testid={`task-row-${t.id}`}>
          <span>{t.name}</span>
          <span data-testid={`task-status-${t.id}`}>{t.status}</span>
          <button data-testid={`view-${t.id}`} onClick={() => onViewDetail(t)}>View</button>
          <button data-testid={`cancel-${t.id}`} onClick={() => onCancelTask(t.id)}>Cancel</button>
          <button data-testid={`retry-${t.id}`} onClick={() => onRetryTask(t.id)}>Retry</button>
        </div>
      ))}
    </div>
  ),
}));

// Mock alert-dialog
vi.mock('../../components/ui/alert-dialog', () => ({
  AlertDialog: ({ children, open }: any) => open ? <div data-testid="alert-dialog">{children}</div> : null,
  AlertDialogContent: ({ children }: any) => <div>{children}</div>,
  AlertDialogHeader: ({ children }: any) => <div>{children}</div>,
  AlertDialogTitle: ({ children }: any) => <h2>{children}</h2>,
  AlertDialogDescription: ({ children }: any) => <p>{children}</p>,
  AlertDialogFooter: ({ children }: any) => <div>{children}</div>,
  AlertDialogAction: ({ children }: any) => <div>{children}</div>,
  AlertDialogCancel: ({ children }: any) => <div>{children}</div>,
}));

// Mock button
vi.mock('../../components/ui/button', () => ({
  Button: ({ children, onClick, ...props }: any) => (
    <button onClick={onClick} {...props}>{children}</button>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...classes: any[]) => classes.filter(Boolean).join(' '),
}));

const mockTasks = [
  { id: 1, name: 'Monkey Test #1', type: 'MONKEY', status: 'RUNNING', priority: 1, target_device_id: 1, created_at: '2026-01-01T00:00:00' },
  { id: 2, name: 'MTBF Test #2', type: 'MTBF', status: 'COMPLETED', priority: 2, target_device_id: 2, created_at: '2026-01-01T01:00:00' },
  { id: 3, name: 'DDR Test #3', type: 'DDR', status: 'FAILED', priority: 1, target_device_id: 3, created_at: '2026-01-01T02:00:00' },
  { id: 4, name: 'GPU Test #4', type: 'GPU', status: 'PENDING', priority: 1, target_device_id: 4, created_at: '2026-01-01T03:00:00' },
];

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        {children}
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe('TaskList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders page title and create button', async () => {
    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('任务管理');
    expect(screen.getByText('查看和管理稳定性测试任务')).toBeInTheDocument();
    expect(screen.getByText('新建工作流')).toBeInTheDocument();
  });

  it('renders loading state initially', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockReturnValue(new Promise(() => {}));

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    expect(screen.getByText('任务管理')).toBeInTheDocument();
  });

  it('renders error state on failure', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockRejectedValue(new Error('network error'));

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText(/Error loading tasks/i);
  });

  it('renders stats filters with correct counts', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockResolvedValue({ data: { items: mockTasks, total: 4 } });

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('全部任务');
    expect(screen.getByText('等待中')).toBeInTheDocument();
    expect(screen.getByText('执行中')).toBeInTheDocument();
    expect(screen.getByText('已完成')).toBeInTheDocument();
    expect(screen.getByText('失败')).toBeInTheDocument();
  });

  it('renders task table with tasks', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockResolvedValue({ data: { items: mockTasks, total: 4 } });

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('Monkey Test #1');
    expect(screen.getByText('MTBF Test #2')).toBeInTheDocument();
    expect(screen.getByTestId('task-data-table')).toBeInTheDocument();
  });

  it('filters tasks by status when clicking filter buttons', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockResolvedValue({ data: { items: mockTasks, total: 4 } });

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('Monkey Test #1');

    // Click "已完成" filter
    fireEvent.click(screen.getByText('已完成'));

    // After filtering, only completed tasks should show
    expect(screen.getByText('MTBF Test #2')).toBeInTheDocument();
    expect(screen.queryByText('Monkey Test #1')).not.toBeInTheDocument();
  });

  it('shows cancel dialog when cancel action is triggered', async () => {
    const { api } = await import('../../utils/api');
    (api.tasks.list as any).mockResolvedValue({ data: { items: mockTasks, total: 4 } });

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('Monkey Test #1');

    // Click cancel on first task
    fireEvent.click(screen.getByTestId('cancel-1'));

    // Alert dialog should appear
    expect(screen.getByTestId('alert-dialog')).toBeInTheDocument();
    expect(screen.getByText('取消任务')).toBeInTheDocument();
  });

  it('"新建工作流" links to /orchestration/workflows', async () => {
    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('任务管理');

    const link = screen.getByText('新建工作流').closest('a');
    expect(link).toHaveAttribute('href', '/orchestration/workflows');
  });
});
