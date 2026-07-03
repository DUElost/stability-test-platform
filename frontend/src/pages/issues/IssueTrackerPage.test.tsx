import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';

const planRunsList = vi.fn();
const getCachedJiraDraft = vi.fn();

vi.mock('@/utils/api', () => ({
  api: {
    planRuns: { list: (...a: unknown[]) => planRunsList(...a) },
    runs: { getCachedJiraDraft: (...a: unknown[]) => getCachedJiraDraft(...a) },
  },
}));

vi.mock('@/components/issues/JiraSubmitPanel', () => ({
  default: () => <div data-testid="jira-submit-panel-stub" />,
}));

vi.mock('@/components/issues/JiraRunHistory', () => ({
  default: () => <div data-testid="jira-run-history-stub" />,
}));

import IssueTrackerPage from './IssueTrackerPage';

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
    planRunsList.mockReset();
    getCachedJiraDraft.mockReset();
  });

  it('defaults to the "form" tab showing JiraSubmitPanel', () => {
    planRunsList.mockResolvedValue([]);
    renderPage();
    expect(screen.getByTestId('jira-submit-panel-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-run-history-stub')).not.toBeInTheDocument();
  });

  it('switches to the "history" tab and renders JiraRunHistory', () => {
    planRunsList.mockResolvedValue([]);
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-history'));

    expect(screen.getByTestId('jira-run-history-stub')).toBeInTheDocument();
    expect(screen.queryByTestId('jira-submit-panel-stub')).not.toBeInTheDocument();
  });

  it('switches to the "drafts" tab and shows empty state when no drafts exist', async () => {
    planRunsList.mockResolvedValue([
      { id: 1, plan_id: 10, status: 'SUCCESS', ended_at: '2026-06-01T00:00:00Z' },
    ]);
    getCachedJiraDraft.mockRejectedValue(new Error('no draft'));
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText('暂无 JIRA 草稿')).toBeInTheDocument();
  });

  it('shows a draft row when a run has a cached JIRA draft', async () => {
    planRunsList.mockResolvedValue([
      { id: 42, plan_id: 10, status: 'SUCCESS', ended_at: '2026-06-01T00:00:00Z' },
    ]);
    getCachedJiraDraft.mockResolvedValue({
      summary: 'Crash on boot',
      priority: 'High',
      project_key: 'ABC',
      issue_type: 'Bug',
      description: 'Device crashes repeatedly during boot sequence testing.',
      labels: ['crash', 'boot'],
      component: 'system',
    });
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText('Crash on boot')).toBeInTheDocument();
    expect(screen.getByText('ABC-Bug | Plan #10 | Job #42')).toBeInTheDocument();
  });

  it('shows an inline error when the drafts query fails outright', async () => {
    planRunsList.mockRejectedValue(new Error('network down'));
    renderPage();

    fireEvent.click(screen.getByTestId('issue-tracker-tab-drafts'));

    expect(await screen.findByText(/JIRA 草稿列表加载失败/)).toBeInTheDocument();
  });
});
