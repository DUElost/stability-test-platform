import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  listLoads: vi.fn(),
  create: vi.fn(),
}));

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      resourcePools: {
        ...actual.api.resourcePools,
        listLoads: mocks.listLoads,
        create: mocks.create,
      },
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

/**
 * #2456：WiFi 池表单同样要「提交以 DOM 值为准」——密码管理器直写 .value 时
 * React 的 onChange 收不到，只认 state 会把已填好的表单当空提交。
 */
describe('WifiPage 自动填充（#2456）', () => {
  function managerFill(el: HTMLElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: false }));
  }

  it('管理器填值后提交 → 请求带的是 DOM 里的值', async () => {
    mocks.listLoads.mockResolvedValue([]);
    mocks.create.mockResolvedValue({ id: 1 });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /新增 WiFi 池/ }));

    managerFill(screen.getByLabelText('名称'), 'Lab A 2.4G');
    managerFill(screen.getByLabelText('SSID'), 'lab-a-2g');
    managerFill(screen.getByLabelText('密码'), 'tPe-KLu-3Uw-3Fb');

    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(mocks.create).toHaveBeenCalled());
    expect(mocks.create.mock.calls[0][0]).toMatchObject({
      name: 'Lab A 2.4G',
      config: { ssid: 'lab-a-2g', password: 'tPe-KLu-3Uw-3Fb' },
    });
  });
});
