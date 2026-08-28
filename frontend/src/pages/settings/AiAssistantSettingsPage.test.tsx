import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import AiAssistantSettingsPage from './AiAssistantSettingsPage';
import type { AiAssistantConfig } from '@/utils/api/types';

const mocks = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
  aiAssistant: {
    getConfig: vi.fn(),
    updateConfig: vi.fn(),
    testConnection: vi.fn(),
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

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return { ...actual, api: { ...actual.api, aiAssistant: mocks.aiAssistant } };
});
vi.mock('@/hooks/useToast', () => ({ useToast: () => mocks.toast }));

const CONFIG: AiAssistantConfig = {
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
};

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <MemoryRouter initialEntries={['/settings/ai-assistant']}>
        <AiAssistantSettingsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe('AiAssistantSettingsPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.aiAssistant.getConfig.mockResolvedValue(CONFIG);
    mocks.aiAssistant.updateConfig.mockResolvedValue(CONFIG);
  });

  it('载入配置回填表单，Key 只见掩码且输入框为空', async () => {
    renderPage();
    const urlInput = await screen.findByLabelText('API URL');
    expect(urlInput).toHaveValue('https://api.example.com/v1');
    expect(screen.getByLabelText('模型')).toHaveValue('test-model');
    const keyInput = screen.getByLabelText('API Key');
    expect(keyInput).toHaveValue('');
    expect(keyInput.getAttribute('placeholder')).toContain('sk-***abcd');
  });

  it('保存时 api_key 留空则不上送该字段（不变更语义）', async () => {
    renderPage();
    fireEvent.click(await screen.findByText('保存配置'));
    await waitFor(() => {
      expect(mocks.aiAssistant.updateConfig).toHaveBeenCalledTimes(1);
    });
    const payload = mocks.aiAssistant.updateConfig.mock.calls[0][0];
    expect(payload.base_url).toBe('https://api.example.com/v1');
    expect('api_key' in payload).toBe(false);
  });

  it('输入新 Key 后保存随 payload 上送', async () => {
    renderPage();
    fireEvent.change(await screen.findByLabelText('API Key'), {
      target: { value: 'sk-new-key' },
    });
    fireEvent.click(screen.getByText('保存配置'));
    await waitFor(() => {
      expect(mocks.aiAssistant.updateConfig).toHaveBeenCalledWith(
        expect.objectContaining({ api_key: 'sk-new-key' }),
      );
    });
  });

  it('测试连接成功展示延迟结果', async () => {
    mocks.aiAssistant.testConnection.mockResolvedValue({
      ok: true,
      latency_ms: 321,
      model: 'test-model',
      error: null,
    });
    renderPage();
    fireEvent.click(await screen.findByText('测试连接'));
    await waitFor(() => {
      expect(screen.getByText(/连接成功（321 ms/)).toBeInTheDocument();
    });
  });

  it('T1 收回开关与免确认白名单可切换并随保存上送', async () => {
    renderPage();
    fireEvent.click(await screen.findByLabelText(/收回 T1 自动执行/));
    fireEvent.click(screen.getByLabelText(/通知通道测试发送/));
    fireEvent.click(screen.getByText('保存配置'));
    await waitFor(() => {
      expect(mocks.aiAssistant.updateConfig).toHaveBeenCalledWith(
        expect.objectContaining({
          t1_require_confirm: true,
          auto_approve_tools: ['test_notification_channel'],
        }),
      );
    });
  });
});
