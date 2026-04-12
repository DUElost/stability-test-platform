import { render, screen } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const mockWorkflows = [
  { id: 1, name: 'Monkey Test #1', description: null, failure_threshold: 0.05, created_at: '2026-01-01T00:00:00', updated_at: '2026-01-01T00:00:00' },
  { id: 2, name: 'MTBF Test #2', description: 'MTBF suite', failure_threshold: 0.1, created_at: '2026-01-01T01:00:00', updated_at: '2026-01-01T01:00:00' },
  { id: 3, name: 'DDR Test #3', description: null, failure_threshold: 0.05, created_at: '2026-01-01T02:00:00', updated_at: '2026-01-01T02:00:00' },
  { id: 4, name: 'GPU Test #4', description: null, failure_threshold: 0.05, created_at: '2026-01-01T03:00:00', updated_at: '2026-01-01T03:00:00' },
];

vi.mock('../../utils/api', () => ({
  api: {
    orchestration: {
      list: vi.fn().mockResolvedValue(mockWorkflows),
    },
  },
}));

vi.mock('../../components/ui/toast', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    info: vi.fn(),
  }),
}));

vi.mock('../../hooks/useConfirm', () => ({
  useConfirm: () => vi.fn().mockResolvedValue(true),
}));

vi.mock('../../components/task/TaskDataTable', () => ({
  TaskDataTable: ({ tasks, onViewDetail }: any) => (
    <div data-testid="task-data-table">
      {tasks.map((t: any) => (
        <div key={t.id} data-testid={`task-row-${t.id}`}>
          <span>{t.name}</span>
          <span data-testid={`task-status-${t.id}`}>{t.status}</span>
          <button data-testid={`view-${t.id}`} onClick={() => onViewDetail(t)}>View</button>
        </div>
      ))}
    </div>
  ),
}));

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

vi.mock('../../components/ui/button', () => ({
  Button: ({ children, onClick, ...props }: any) => (
    <button onClick={onClick} {...props}>{children}</button>
  ),
}));

vi.mock('@/lib/utils', () => ({
  cn: (...classes: any[]) => classes.filter(Boolean).join(' '),
}));

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
    expect(screen.getByText('新建工作流')).toBeInTheDocument();
  });

  it('renders loading state initially', async () => {
    const { api } = await import('../../utils/api');
    (api.orchestration.list as any).mockReturnValue(new Promise(() => {}));

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    expect(screen.getByText('任务管理')).toBeInTheDocument();
  });

  it('renders error state on failure', async () => {
    const { api } = await import('../../utils/api');
    (api.orchestration.list as any).mockRejectedValue(new Error('network error'));

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('加载工作流失败，请检查后端连接。');
  });

  it('renders task table with workflows', async () => {
    const { api } = await import('../../utils/api');
    (api.orchestration.list as any).mockResolvedValue(mockWorkflows);

    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('Monkey Test #1');
    expect(screen.getByText('MTBF Test #2')).toBeInTheDocument();
    expect(screen.getByTestId('task-data-table')).toBeInTheDocument();
  });

  it('"新建工作流" links to /orchestration/workflows', async () => {
    const TaskList = (await import('./TaskList')).default;
    render(<TaskList />, { wrapper: createWrapper() });

    await screen.findByText('任务管理');

    const link = screen.getByText('新建工作流').closest('a');
    expect(link).toHaveAttribute('href', '/orchestration/workflows');
  });
});
