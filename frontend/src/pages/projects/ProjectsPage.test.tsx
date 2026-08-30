import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import ProjectsPage from './ProjectsPage';

const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  listProjects: vi.fn(),
  getProject: vi.fn(),
  inventoryModels: vi.fn(),
  inventorySummary: vi.fn(),
  createProject: vi.fn(),
  mapPreview: vi.fn(),
  mapApply: vi.fn(),
  listSeed: vi.fn(),
  promoteSeed: vi.fn(),
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
    projects: {
      list: mocks.listProjects,
      get: mocks.getProject,
      inventoryModels: mocks.inventoryModels,
      inventorySummary: mocks.inventorySummary,
      create: mocks.createProject,
      mapPreview: mocks.mapPreview,
      mapApply: mocks.mapApply,
      listSeed: mocks.listSeed,
      promoteSeed: mocks.promoteSeed,
    },
  },
  toApiError: (error: unknown) => ({
    message: error instanceof Error ? error.message : '请求失败',
  }),
}));

function makeProject(overrides: Record<string, unknown> = {}) {
  return {
    project_key: 'HONOR-CAMERA',
    display_name: '荣耀相机',
    jira_project_key: null,
    product_line: 'Sonic',
    customer: 'CustA',
    platforms: ['MTK'],
    form_factor: 'PHONE',
    status: 'ACTIVE',
    source: 'USER',
    match_models: ['MLD_LX2'],
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
    mapped_project_keys: [],
    unassigned_device_count: 2,
    ...overrides,
  };
}

const emptySummary = {
  total_devices: 0,
  user_mapped_devices: 0,
  distinct_models: 0,
  unmapped_models: [],
  unassigned_devices: 0,
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
    mocks.authRole = 'admin';
    mocks.listProjects.mockResolvedValue([
      makeProject(),
      makeProject({
        project_key: 'ODM-TABLET',
        display_name: 'ODM 平板',
        customer: 'CustB',
        platforms: ['QCOM'],
        match_models: [],
        device_count: 0,
        running_run_count: 0,
      }),
    ]);
    mocks.inventoryModels.mockResolvedValue([]);
    mocks.inventorySummary.mockResolvedValue(emptySummary);
    mocks.listSeed.mockResolvedValue([]);
  });

  it('renders project cards with facet badges and counts', async () => {
    renderPage();

    expect(await screen.findByText('荣耀相机')).toBeInTheDocument();
    expect(screen.getByText('HONOR-CAMERA')).toBeInTheDocument();
    expect(screen.getByText('客户: CustA')).toBeInTheDocument();
    expect(screen.getByText('平台: MTK')).toBeInTheDocument();
    const cardA = screen.getByText('荣耀相机').closest('[data-testid="project-card"]');
    expect(within(cardA as HTMLElement).getByText('3')).toBeInTheDocument();
    expect(within(cardA as HTMLElement).getByText('台设备')).toBeInTheDocument();
    expect(screen.getByText('ODM 平板')).toBeInTheDocument();
    expect(screen.queryByText('HONOR-MLD')).not.toBeInTheDocument();
    expect(screen.queryByText('Legacy')).not.toBeInTheDocument();
    expect(await screen.findByText('2')).toBeInTheDocument();
    expect(screen.getByTestId('kpi-strip')).toBeInTheDocument();
    expect(screen.getByTestId('project-avatar-HONOR-CAMERA')).toHaveTextContent('荣');
  });

  it('filters cards by facet selection', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('荣耀相机');
    await user.click(screen.getByTestId('facet-customer-CustA'));

    await waitFor(() => {
      expect(screen.getByText('荣耀相机')).toBeInTheDocument();
      expect(screen.queryByText('ODM 平板')).not.toBeInTheDocument();
    });
  });

  it('navigates to detail page on card click', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('荣耀相机');
    await user.click(screen.getByText('荣耀相机').closest('[data-testid="project-card"]')!);

    // P2-11：抽屉删除——卡片直接跳详情页
    expect(mocks.navigate).toHaveBeenCalledWith('/projects/HONOR-CAMERA');
    expect(screen.queryByTestId('project-detail-sheet')).not.toBeInTheDocument();
  });

  it('shows empty state when no projects exist', async () => {
    mocks.listProjects.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText('暂无项目')).toBeInTheDocument();
    expect(screen.getByTestId('inventory-models')).toBeInTheDocument();
    expect(screen.getByTestId('create-project-open')).toBeInTheDocument();
  });

  it('shows no-match state when facet filters out everything', async () => {
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('荣耀相机');
    await user.click(screen.getByTestId('facet-customer-CustA'));
    await user.click(screen.getByTestId('facet-platforms-QCOM'));

    expect(await screen.findByText('没有匹配的项目')).toBeInTheDocument();
  });

  it('shows fleet mapping as unmapped until a user project is attached', async () => {
    mocks.inventoryModels.mockResolvedValue([
      makeInventoryRow({ model: 'MLD_LX2', device_count: 260 }),
      makeInventoryRow({
        model: 'MYSTERY_X',
        device_count: 1,
        mapped_project_keys: ['HONOR-CAMERA'],
        unassigned_device_count: 0,
      }),
    ]);

    renderPage();

    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();
    expect(screen.getByTestId('mapping-pending')).toHaveTextContent('未映射');
    expect(screen.getAllByText('HONOR-CAMERA').length).toBeGreaterThanOrEqual(2);
    expect(screen.queryByText('待手动填写')).not.toBeInTheDocument();
    expect(screen.queryByText('系统回填标签（非正式编组）')).not.toBeInTheDocument();
  });

  it('filters fleet table to unmapped models only', async () => {
    const user = userEvent.setup();
    mocks.inventoryModels.mockResolvedValue([
      makeInventoryRow(),
      makeInventoryRow({
        model: 'MYSTERY_X',
        device_count: 1,
        mapped_project_keys: ['HONOR-CAMERA'],
        unassigned_device_count: 0,
      }),
    ]);

    renderPage();
    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();

    await user.click(screen.getByTestId('inventory-unmapped-only'));

    expect(screen.getByText('MLD_LX2')).toBeInTheDocument();
    expect(screen.queryByText('MYSTERY_X')).not.toBeInTheDocument();
  });

  it('creates a project from the admin dialog', async () => {
    const user = userEvent.setup();
    mocks.createProject.mockResolvedValue(makeProject());
    renderPage();

    await user.click(await screen.findByTestId('create-project-open'));
    await user.type(screen.getByTestId('create-project-key'), 'HONOR-CAMERA');
    await user.type(screen.getByTestId('create-project-name'), '荣耀相机');
    await user.click(screen.getByTestId('create-project-confirm'));

    await waitFor(() => {
      expect(mocks.createProject).toHaveBeenCalledWith({
        project_key: 'HONOR-CAMERA',
        display_name: '荣耀相机',
        customer: null,
        form_factor: null,
        product_line: null,
        jira_project_key: null,
      });
    });
  });

  it('previews and applies model mapping', async () => {
    const user = userEvent.setup();
    mocks.inventoryModels.mockResolvedValue([makeInventoryRow()]);
    mocks.mapPreview.mockResolvedValue({
      target_project_key: 'HONOR-CAMERA',
      models: ['MLD_LX2'],
      will_assign: 2,
      already_in_target: 0,
      conflicts: [],
      unknown_models: [],
    });
    mocks.mapApply.mockResolvedValue({
      target_project_key: 'HONOR-CAMERA',
      models: ['MLD_LX2'],
      will_assign: 2,
      already_in_target: 0,
      conflicts: [],
      unknown_models: [],
    });

    renderPage();
    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();
    await user.click(screen.getByTestId('inventory-model-check'));
    await user.click(screen.getByTestId('map-models-open'));
    await user.click(screen.getByTestId('map-preview-btn'));
    expect(await screen.findByTestId('map-preview')).toHaveTextContent('将归入 2 台');
    // #376：SEED/LEGACY 直迁不进冲突列表的醒目提示（will_assign>0 即显示）
    expect(screen.getByTestId('map-preview')).toHaveTextContent('不视为冲突');
    await user.click(screen.getByTestId('map-apply-btn'));
    await waitFor(() => {
      expect(mocks.mapApply).toHaveBeenCalledWith('HONOR-CAMERA', ['MLD_LX2'], false);
    });
  });

  it('warns when preview reports models unseen in fleet (#376)', async () => {
    const user = userEvent.setup();
    mocks.inventoryModels.mockResolvedValue([makeInventoryRow()]);
    mocks.mapPreview.mockResolvedValue({
      target_project_key: 'HONOR-CAMERA',
      models: ['MLD_LX2', 'GHOST_MODEL'],
      will_assign: 0,
      already_in_target: 0,
      conflicts: [],
      unknown_models: ['GHOST_MODEL'],
    });

    renderPage();
    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();
    await user.click(screen.getByTestId('inventory-model-check'));
    await user.click(screen.getByTestId('map-models-open'));
    await user.click(screen.getByTestId('map-preview-btn'));
    expect(await screen.findByTestId('map-unknown-models')).toHaveTextContent('GHOST_MODEL');
    expect(screen.getByTestId('map-unknown-models')).toHaveTextContent('match_models');
  });

  it('keeps apply disabled when preview reports conflicts', async () => {
    const user = userEvent.setup();
    mocks.inventoryModels.mockResolvedValue([makeInventoryRow()]);
    mocks.mapPreview.mockResolvedValue({
      target_project_key: 'HONOR-CAMERA',
      models: ['MLD_LX2'],
      will_assign: 1,
      already_in_target: 0,
      conflicts: [{
        device_id: 9,
        serial: 's-conflict',
        model: 'MLD_LX2',
        from_project_key: 'ODM-TABLET',
      }],
      unknown_models: [],
    });

    renderPage();
    expect(await screen.findByText('MLD_LX2')).toBeInTheDocument();
    await user.click(screen.getByTestId('inventory-model-check'));
    await user.click(screen.getByTestId('map-models-open'));
    await user.click(screen.getByTestId('map-preview-btn'));
    expect(await screen.findByTestId('map-preview')).toHaveTextContent('冲突 1 台');
    expect(screen.getByTestId('map-apply-btn')).toBeDisabled();
    expect(mocks.mapApply).not.toHaveBeenCalled();
  });

  it('hides admin actions for non-admin users', async () => {
    mocks.authRole = 'user';
    renderPage();
    expect(await screen.findByText('荣耀相机')).toBeInTheDocument();
    expect(screen.queryByTestId('create-project-open')).not.toBeInTheDocument();
    expect(screen.queryByTestId('map-models-open')).not.toBeInTheDocument();
  });

  it('renders seed queue and promotes on confirm', async () => {
    const user = userEvent.setup();
    mocks.listSeed.mockResolvedValue([
      {
        project_key: 'HONOR-ELA',
        display_name: '荣耀 ELA',
        status: 'ACTIVE',
        source: 'SEED',
        match_models: [],
        device_count: 20,
        running_run_count: 0,
      },
      {
        project_key: 'LEGACY',
        display_name: 'Legacy',
        status: 'ACTIVE',
        source: 'SEED',
        match_models: [],
        device_count: 6,
        running_run_count: 0,
      },
    ]);
    mocks.promoteSeed.mockResolvedValue({
      project_key: 'HONOR-ELA',
      display_name: '荣耀 ELA',
      source: 'USER',
      match_models: ['MLD_LX2'],
      device_count: 20,
      running_run_count: 0,
    });
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    renderPage();
    expect(await screen.findByTestId('seed-queue')).toBeInTheDocument();
    expect(screen.getByText('荣耀 ELA')).toBeInTheDocument();
    expect(screen.getByText('20 台设备')).toBeInTheDocument();
    // LEGACY 是兜底标签，不提供转正按钮
    expect(screen.queryByTestId('seed-promote-LEGACY')).not.toBeInTheDocument();

    await user.click(screen.getByTestId('seed-promote-HONOR-ELA'));
    expect(confirmSpy).toHaveBeenCalled();
    expect(mocks.promoteSeed).toHaveBeenCalledWith('HONOR-ELA');
    await waitFor(() => expect(mocks.toast.success).toHaveBeenCalled());
    confirmSpy.mockRestore();
  });

  it('skips promote when confirm is dismissed', async () => {
    const user = userEvent.setup();
    mocks.listSeed.mockResolvedValue([
      {
        project_key: 'HONOR-ELA',
        display_name: '荣耀 ELA',
        status: 'ACTIVE',
        source: 'SEED',
        match_models: [],
        device_count: 20,
        running_run_count: 0,
      },
    ]);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(false);

    renderPage();
    await screen.findByTestId('seed-queue');
    await user.click(screen.getByTestId('seed-promote-HONOR-ELA'));
    expect(mocks.promoteSeed).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });
});
