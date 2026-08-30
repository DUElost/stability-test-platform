import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import TestSuitesPage from './TestSuitesPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listSuites: vi.fn(),
  createSuite: vi.fn(),
  listProjects: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  authRole: 'admin' as string,
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({ data: { role: mocks.authRole } }),
}));

vi.mock('@/hooks/useToast', () => ({
  useToast: () => mocks.toast,
}));

vi.mock('@/utils/api', () => ({
  api: {
    suites: {
      list: mocks.listSuites,
      create: mocks.createSuite,
    },
    projects: {
      list: mocks.listProjects,
    },
  },
  toApiError: (err: unknown) => (err instanceof Error ? err : new Error(String(err))),
}));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <TestSuitesPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('TestSuitesPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.authRole = 'admin';
    mocks.listProjects.mockResolvedValue([]);
    mocks.listSuites.mockResolvedValue([
      {
        id: 1,
        name: 'mtbf_default',
        display_name: '默认套件',
        project_key: 'legacy',
        case_count: 130,
        enabled_case_count: 130,
        is_active: true,
        export_stale: false,
        created_at: '2026-08-01T00:00:00Z',
        updated_at: '2026-08-01T00:00:00Z',
      },
    ]);
  });

  it('renders suite list', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('suite-row-1')).toBeInTheDocument();
    });
    expect(screen.getByText('mtbf_default')).toBeInTheDocument();
    expect(screen.getByText('默认套件')).toBeInTheDocument();
  });

  it('navigates to detail on row click', async () => {
    const user = userEvent.setup();
    renderPage();
    await waitFor(() => expect(screen.getByTestId('suite-row-1')).toBeInTheDocument());
    await user.click(screen.getByTestId('suite-row-1'));
    expect(mocks.navigate).toHaveBeenCalledWith('/test-suites/1');
  });

  it('creates suite and navigates to detail', async () => {
    const user = userEvent.setup();
    mocks.createSuite.mockResolvedValue({
      id: 42,
      name: 'new_suite',
      case_count: 0,
      enabled_case_count: 0,
      is_active: true,
      export_stale: true,
      created_at: '2026-08-01T00:00:00Z',
      updated_at: '2026-08-01T00:00:00Z',
    });
    renderPage();
    await waitFor(() => expect(screen.getByTestId('create-suite-btn')).toBeInTheDocument());
    await user.click(screen.getByTestId('create-suite-btn'));
    await user.type(screen.getByTestId('create-suite-name'), 'new_suite');
    await user.click(screen.getByTestId('create-suite-submit'));
    await waitFor(() => {
      expect(mocks.createSuite).toHaveBeenCalledWith(expect.objectContaining({ name: 'new_suite' }));
    });
    expect(mocks.navigate).toHaveBeenCalledWith('/test-suites/42');
  });
});
