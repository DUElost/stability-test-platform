import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import ProjectsPage from './ProjectsPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listProjects: vi.fn(),
  inventoryModels: vi.fn(),
  inventorySummary: vi.fn(),
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return {
    ...actual,
    useNavigate: () => mocks.navigate,
  };
});

vi.mock('@/utils/api', () => ({
  api: {
    projects: {
      list: mocks.listProjects,
      inventoryModels: mocks.inventoryModels,
      inventorySummary: mocks.inventorySummary,
    },
  },
}));

function makeProject(overrides: Record<string, unknown> = {}) {
  return {
    project_key: 'proj-a',
    display_name: 'Project A',
    jira_project_key: null,
    product_line: 'Sonic',
    customer: 'CustA',
    platform: 'MTK',
    form_factor: 'PHONE',
    status: 'ACTIVE',
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    device_count: 3,
    running_run_count: 1,
    ...overrides,
  };
}

function makeInventoryRow(overrides: Record<string, unknown> = {}) {
  return {
    model: 'MLD_LX2',
    device_count: 2,
    platforms: ['MTK'],
    backfill_project_keys: ['HONOR-MLD'],
    mapped_project_keys: [],
    legacy_device_count: 0,
    null_device_count: 0,
    ...overrides,
  };
}

const emptySummary = {
  total_devices: 0,
  mapped_devices: 0,
  legacy_devices: 0,
  null_devices: 0,
  distinct_models: 0,
  unmapped_models: [],
};

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <ProjectsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('ProjectsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listProjects.mockResolvedValue([
      makeProject(),
      makeProject({
        project_key: 'LEGACY',
        display_name: 'Legacy',
        customer: null,
        platform: null,
        form_factor: null,
        product_line: null,
        device_count: 0,
        running_run_count: 0,
      }),
    ]);
    mocks.inventoryModels.mockResolvedValue([]);
    mocks.inventorySummary.mockResolvedValue(emptySummary);
  });

  it('renders project cards with facet badges and counts', async () => {
    renderPage();

    expect(await screen.findByText('Project A')).toBeInTheDocument();
    expect(screen.getByText('proj-a')).toBeInTheDocument();
    expect(screen.getByText('客户: CustA')).toBeInTheDocument();
    expect(screen.getByText('平台: MTK')).toBeInTheDocument();
    expect(screen.getByText('3 台设备')).toBeInTheDocument();
    expect(screen.getByText('Legacy')).toBeInTheDocument();
    expect(await screen.findByText('2')).toBeInTheDocument();
  });

  it('filters cards by facet selection', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('Project A');
    await user.click(screen.getByTestId('facet-customer'));
    await user.click(await screen.findByText('CustA'));

    await waitFor(() => {
      expect(screen.getByText('Project A')).toBeInTheDocument();
      expect(screen.queryByText('Legacy')).not.toBeInTheDocument();
    });
  });

  it('navigates to project detail on card click', async () => {
    const user = userEvent.setup();
    renderPage();

    const card = (await screen.findByText('Project A')).closest('[data-testid="project-card"]');
    expect(card).not.toBeNull();
    await user.click(card as HTMLElement);

    expect(mocks.navigate).toHaveBeenCalledWith('/projects/proj-a');
  });

  it('shows empty state when no projects exist', async () => {
    mocks.listProjects.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('暂无回填标签')).toBeInTheDocument();
    expect(screen.getByTestId('inventory-models')).toBeInTheDocument();
  });

  it('shows no-match state when facet filters out everything', async () => {
    const user = userEvent.setup();
    mocks.listProjects.mockResolvedValue([
      makeProject(),
      makeProject({
        project_key: 'proj-b',
        display_name: 'Project B',
        customer: 'CustB',
        platform: 'QCOM',
      }),
    ]);
    renderPage();

    await screen.findByText('Project A');
    await user.click(screen.getByTestId('facet-customer'));
    await user.click(await screen.findByRole('option', { name: 'CustA' }));
    await user.click(screen.getByTestId('facet-platform'));
    await user.click(await screen.findByRole('option', { name: 'QCOM' }));

    expect(await screen.findByText('没有匹配的回填标签')).toBeInTheDocument();
  });

  it('shows backfill keys as informal labels and mapping as pending', async () => {
    mocks.inventoryModels.mockResolvedValue([
      makeInventoryRow({ model: 'MLD_LX2', device_count: 260 }),
      makeInventoryRow({ model: 'MLD_LX3', device_count: 32 }),
      makeInventoryRow({
        model: 'MYSTERY_X',
        device_count: 1,
        backfill_project_keys: ['LEGACY'],
        legacy_device_count: 1,
      }),
    ]);
    mocks.listProjects.mockResolvedValue([
      makeProject({
        project_key: 'HONOR-MLD',
        display_name: '荣耀 MLD 系列',
        device_count: 292,
      }),
    ]);

    renderPage();

    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();
    expect(screen.getByText('MLD_LX3')).toBeInTheDocument();
    const pending = screen.getAllByTestId('mapping-pending');
    expect(pending.length).toBeGreaterThanOrEqual(2);
    pending.forEach((node) => {
      expect(node).toHaveTextContent('待手动填写');
    });
    expect(screen.getByText('未分配（LEGACY）')).toBeInTheDocument();
    expect(screen.getByText('系统回填标签（非正式编组）')).toBeInTheDocument();
    expect(screen.getAllByText('非正式回填').length).toBeGreaterThanOrEqual(1);
  });

  it('filters fleet table to unassigned models only', async () => {
    const user = userEvent.setup();
    mocks.inventoryModels.mockResolvedValue([
      makeInventoryRow(),
      makeInventoryRow({
        model: 'MYSTERY_X',
        device_count: 1,
        backfill_project_keys: ['LEGACY'],
        legacy_device_count: 1,
      }),
    ]);

    renderPage();
    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();

    await user.click(screen.getByTestId('inventory-unassigned-only'));

    expect(screen.queryByText('MLD_LX2')).not.toBeInTheDocument();
    expect(screen.getByText('MYSTERY_X')).toBeInTheDocument();
  });
});
