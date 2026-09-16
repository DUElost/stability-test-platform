import { render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  listLoads: vi.fn(),
}));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      resourcePools: { ...actual.api.resourcePools, listLoads: mocks.listLoads },
    },
  };
});

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn(), dialog: null }),
}));

import WifiPage from './WifiPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <WifiPage />
    </QueryClientProvider>,
  );
}

/**
 * #2359：/wifi 的加载失败必须按 **HTTP 语义**给文案——403 是权限，不是连接。
 *
 * 反例（修复前）：一律显示「请检查后端服务连接」，把排查方向误导到后端/网络
 * （同类先例 #955：WiFi 选择器 403 静默变空）。
 */
describe('WifiPage 错误文案（#2359）', () => {
  it('403 → 无权限提示（不出现「检查后端服务连接」）', async () => {
    mocks.listLoads.mockRejectedValue(
      new ApiError('HTTP_403', 'Forbidden', { status: 403 }),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/无权限访问 WiFi 资源池/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/检查后端服务连接/)).not.toBeInTheDocument();
  });

  it('网络层失败 → 连接提示（保留原文案）', async () => {
    mocks.listLoads.mockRejectedValue(new ApiError('NETWORK_ERROR', '网络请求失败'));

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/检查后端服务连接/)).toBeInTheDocument(),
    );
  });
});
