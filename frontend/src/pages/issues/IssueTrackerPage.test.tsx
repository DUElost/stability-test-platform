import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const listRecentJiraDrafts = vi.fn();
const planRunsList = vi.fn();
const planRunsListJobs = vi.fn();
const getCachedJiraDraft = vi.fn();
const navigate = vi.fn();

vi.mock('@/utils/api', () => ({
  api: {
    // 旧路径的 mock 保留：用于断言页面不再走「逐 PlanRun × 逐 Job」自算扇出。
    planRuns: {
      list: (...a: unknown[]) => planRunsList(...a),
      listJobs: (...a: unknown[]) => planRunsListJobs(...a),
    },
    runs: {
      listRecentJiraDrafts: (...a: unknown[]) => listRecentJiraDrafts(...a),
      getCachedJiraDraft: (...a: unknown[]) => getCachedJiraDraft(...a),
    },
  },
}));

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual<typeof import('react-router-dom')>('react-router-dom');
  return { ...actual, useNavigate: () => navigate };
});

vi.mock('@/components/issues/JiraSubmitPanel', () => ({
  default: () => <div data-testid="jira-submit-panel-stub" />,
}));

vi.mock('@/components/issues/JiraRunHistory', () => ({
  default: () => <div data-testid="jira-run-history-stub" />,
}));

import IssueTrackerPage from './IssueTrackerPage';

function makeDraftItem(overrides: Record<string, unknown> = {}) {
  return {
    job_id: 900,
    plan_run_id: 42,
    ended_at: '2026-06-01T00:00:00Z',
    post_processed_at: '2026-06-01T00:00:00Z',
    draft: {
      summary: 'Crash on boot',
      priority: 'Minor',
      project_key: 'ABC',
      issue_type: 'Bug',
      component: 'system',
      description: 'Device crashes repeatedly during boot sequence testing.',
      labels: ['crash', 'boot'],
      environment: {},
      custom_fields: {},
      extra: {},
    },
    ...overrides,
  };
}

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <IssueTrackerPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('IssueTrackerPage', () => {
  beforeEach(() => {
    listRecentJiraDrafts.mockReset();
    planRunsList.mockReset();
    planRunsListJobs.mockReset();
    getCachedJiraDraft.mockReset();
    navigate.mockReset();
    listRecentJiraDrafts.mockResolvedValue([]);
  });

  it('defaults to the "form" tab showing JiraSubmitPanel', () => {
    renderPage();
    expect(screen.getByTestId('jira-submit-panel-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-run-history-stub')).not.toBeInTheDocument();
  });

  it('#1532: 草稿查询按页签懒加载，挂载时不打请求', () => {
    renderPage();
    expect(listRecentJiraDrafts).not.toHaveBeenCalled();
  });

  it('switches to the "history" tab and renders JiraRunHistory', () => {
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-history'));

    expect(screen.getByTestId('jira-run-history-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-submit-panel-stub')).not.toBeInTheDocument();
    expect(listRecentJiraDrafts).not.toHaveBeenCalled();
  });

  it('switches to the "drafts" tab and shows empty state when no drafts exist', async () => {
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText(/暂无 JIRA 草稿/)).toBeInTheDocument();
    expect(listRecentJiraDrafts).toHaveBeenCalledWith(50);
  });

  it('#1532: 草稿列表单请求取数，不再逐 PlanRun × 逐 Job 扇出', async () => {
    listRecentJiraDrafts.mockResolvedValue([makeDraftItem()]);
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));
    await screen.findByText('Crash on boot');

    expect(planRunsList).not.toHaveBeenCalled();
    expect(planRunsListJobs).not.toHaveBeenCalled();
    expect(getCachedJiraDraft).not.toHaveBeenCalled();
  });

  it('#1532: 草稿行用 plan_run_id 展示与跳转，job_id 不得当 PlanRun id 用', async () => {
    listRecentJiraDrafts.mockResolvedValue([makeDraftItem()]);
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText('Crash on boot')).toBeInTheDocument();
    expect(screen.getByText('ABC')).toBeInTheDocument();
    // 列显示 PlanRun id（42），不是 JobInstance id（900）
    expect(screen.getByText('#42')).toBeInTheDocument();
    expect(screen.queryByText('#900')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Crash on boot').closest('tr') as HTMLElement);

    expect(navigate).toHaveBeenCalledWith('/execution/plan-runs/42');
  });

  it('#1532: 无 PlanRun 归属的草稿行不可跳转且展示占位', async () => {
    listRecentJiraDrafts.mockResolvedValue([makeDraftItem({ plan_run_id: null })]);
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText('Crash on boot')).toBeInTheDocument();
    expect(screen.queryByText('#900')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('Crash on boot').closest('tr') as HTMLElement);

    expect(navigate).not.toHaveBeenCalled();
  });

  it('shows an inline error when the drafts query fails outright', async () => {
    listRecentJiraDrafts.mockRejectedValue(new Error('network down'));
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText(/JIRA 草稿列表加载失败/)).toBeInTheDocument();
  });
});
