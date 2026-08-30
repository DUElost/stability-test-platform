import { describe, expect, it, vi, beforeAll, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AssistantPage from './AssistantPage';
import type { AiChatMessage, AiChatSession } from '@/utils/api/types';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  confirm: vi.fn(),
  session: { data: { role: 'admin', username: 'tester' }, isLoading: false, isSuccess: true },
  aiAssistant: {
    getConfig: vi.fn(),
    listSessions: vi.fn(),
    createSession: vi.fn(),
    deleteSession: vi.fn(),
    listMessages: vi.fn(),
    sendMessage: vi.fn(),
    getAction: vi.fn(),
    approveAction: vi.fn(),
    rejectAction: vi.fn(),
    getActionLog: vi.fn(),
    cancelAction: vi.fn(),
  },
}));

// toApiError 保留真实实现：未配置（ai_not_configured）→ 横幅的分支要过真实归一化。
vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return { ...actual, api: { ...actual.api, aiAssistant: mocks.aiAssistant } };
});
vi.mock('@/hooks/useToast', () => ({ useToast: () => mocks.toast }));
vi.mock('@/hooks/useConfirm', () => ({ useConfirm: () => mocks.confirm }));
vi.mock('@/hooks/useAuthSession', () => ({ useAuthSession: () => mocks.session }));

const SESSION: AiChatSession = {
  id: 1,
  title: '平台巡检',
  created_at: '2026-08-28T00:00:00Z',
  updated_at: '2026-08-28T01:00:00Z',
};

function makeMessage(overrides: Partial<AiChatMessage> & { id: number }): AiChatMessage {
  return {
    session_id: 1,
    role: 'assistant',
    content: '',
    tool_calls: [],
    tool_call_id: null,
    status: 'completed',
    meta: {},
    created_at: '2026-08-28T01:00:00Z',
    ...overrides,
  };
}

const USER_MSG = makeMessage({ id: 10, role: 'user', content: '平台现在健康吗？' });
const ASSISTANT_MSG = makeMessage({ id: 11, content: '一切正常，无告警。' });

function setData(opts?: { messages?: AiChatMessage[] }) {
  mocks.aiAssistant.getConfig.mockResolvedValue({
    base_url: 'https://api.example.com/v1',
    model: 'test-model',
    api_key_masked: 'sk-***abcd',
    enabled: true,
    temperature: 0.2,
    max_turns: 8,
    request_timeout_seconds: 120,
    t1_require_confirm: false,
    auto_approve_tools: [],
    updated_at: '2026-08-28T00:00:00Z',
  });
  mocks.aiAssistant.listSessions.mockResolvedValue([SESSION]);
  mocks.aiAssistant.listMessages.mockResolvedValue(opts?.messages ?? [USER_MSG, ASSISTANT_MSG]);
  mocks.aiAssistant.getAction.mockResolvedValue({
    id: 99,
    session_id: 1,
    tool_name: 'test_notification_channel',
    params: {},
    status: 'succeeded',
    console_run_id: null,
    result_summary: null,
    requested_by: 'tester',
    decided_by: null,
    created_at: '2026-08-28T01:00:00Z',
    decided_at: null,
  });
}

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/assistant']}>
        <AssistantPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AssistantPage', () => {
  beforeAll(() => {
    // jsdom 未实现 Element.scrollTo；消息区自动滚动仅在真实浏览器有意义
    Element.prototype.scrollTo = vi.fn();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    setData();
  });

  it('载入会话列表并自动选中首个会话拉取消息', async () => {
    renderPage();
    // 会话标题同时出现在桌面侧栏与移动端选择条（CSS 断点切换，jsdom 下并存）
    expect((await screen.findAllByText('平台巡检')).length).toBeGreaterThan(0);
    await waitFor(() => {
      expect(mocks.aiAssistant.listMessages).toHaveBeenCalledWith(1);
    });
    expect(await screen.findByText('一切正常，无告警。')).toBeInTheDocument();
  });

  it('输入消息回车发送并展示用户气泡', async () => {
    renderPage();
    const input = await screen.findByLabelText('消息输入框');
    // 排队下一次 refetch 的返回（发送成功后 invalidate 触发），避免竞态
    mocks.aiAssistant.listMessages.mockResolvedValueOnce([
      USER_MSG,
      makeMessage({ id: 12, role: 'user', content: '帮我跑一遍 check:quick 门禁' }),
      ASSISTANT_MSG,
    ]);
    fireEvent.change(input, { target: { value: '帮我跑一遍 check:quick 门禁' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => {
      expect(mocks.aiAssistant.sendMessage).toHaveBeenCalledWith(1, '帮我跑一遍 check:quick 门禁');
    });
    expect(await screen.findByText('帮我跑一遍 check:quick 门禁')).toBeInTheDocument();
  });

  it('后端返回未配置错误时切换引导横幅（admin 见「前往设置」）', async () => {
    mocks.aiAssistant.sendMessage.mockRejectedValue(
      Object.assign(
        new Error('ai_not_configured'),
        { response: { status: 409, data: { error: { code: 'ai_not_configured', message: 'ai_not_configured' } } } },
      ),
    );
    renderPage();
    const input = await screen.findByLabelText('消息输入框');
    fireEvent.change(input, { target: { value: '你好' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(await screen.findByText(/尚未启用/)).toBeInTheDocument();
    expect(screen.getByText('前往设置')).toBeInTheDocument();
  });
});
