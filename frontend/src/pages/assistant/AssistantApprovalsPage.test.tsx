import { describe, expect, it, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AssistantApprovalsPage from './AssistantApprovalsPage';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  confirm: vi.fn(),
  aiAssistant: {
    listPendingActions: vi.fn(),
    approveAction: vi.fn(),
    rejectAction: vi.fn(),
  },
}));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return { ...actual, api: { ...actual.api, aiAssistant: mocks.aiAssistant } };
});
vi.mock('@/hooks/useToast', () => ({ useToast: () => mocks.toast }));
vi.mock('@/hooks/useConfirm', () => ({ useConfirm: () => mocks.confirm }));

function renderPage() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter>
        <AssistantApprovalsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

const PENDING = {
  id: 42,
  tool_name: 'test_notification_channel',
  preview_text: '向渠道「运维群」发送测试消息',
  requested_by: 'alice',
  created_at: '2026-09-10T00:00:00Z',
};

describe('AssistantApprovalsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.confirm.mockResolvedValue(true);
    mocks.aiAssistant.approveAction.mockResolvedValue({});
    mocks.aiAssistant.rejectAction.mockResolvedValue({});
  });

  it('lists pending actions for admin', async () => {
    mocks.aiAssistant.listPendingActions.mockResolvedValue([PENDING]);
    renderPage();
    expect(await screen.findByText('通知通道测试发送')).toBeInTheDocument();
    expect(screen.getByText(/alice/)).toBeInTheDocument();
  });

  it('approves a pending action after confirmation', async () => {
    mocks.aiAssistant.listPendingActions.mockResolvedValue([PENDING]);
    renderPage();
    const btn = await screen.findByRole('button', { name: /批准执行/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(mocks.aiAssistant.approveAction).toHaveBeenCalledWith(42),
    );
  });

  it('rejects a pending action after confirmation', async () => {
    mocks.aiAssistant.listPendingActions.mockResolvedValue([PENDING]);
    renderPage();
    const btn = await screen.findByRole('button', { name: /^拒绝$/ });
    fireEvent.click(btn);
    await waitFor(() =>
      expect(mocks.aiAssistant.rejectAction).toHaveBeenCalledWith(42),
    );
  });

  it('shows empty state when no pending actions', async () => {
    mocks.aiAssistant.listPendingActions.mockResolvedValue([]);
    renderPage();
    expect(await screen.findByText('暂无待审批操作')).toBeInTheDocument();
  });
});
