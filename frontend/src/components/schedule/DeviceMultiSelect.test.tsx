import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { Device } from '@/utils/api/types';

const mockFetchAllDevices = vi.fn();

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    fetchAllDevices: (...args: unknown[]) => mockFetchAllDevices(...args),
    // 数据源契约守卫：`api.devices.list` 故意置为炸 mock——组件若从 `fetchAllDevices`
    // （翻页）退回单次 `list(0, N)`（#3131 修的静默丢设备点），这里立刻红。
    api: {
      ...actual.api,
      devices: {
        ...actual.api.devices,
        list: vi.fn(() => {
          throw new Error('DeviceMultiSelect 必须走 fetchAllDevices（按 total 翻页），不得退回单次 list');
        }),
      },
    },
  };
});

import { DeviceMultiSelect } from './DeviceMultiSelect';

const device = (id: number, serial: string, model: string | null): Device => ({
  id,
  serial,
  model,
  host_id: 'h1',
  status: 'ONLINE',
  last_seen: null,
  tags: [],
});

const DEVICES = [device(1, 'SN-ALPHA', 'Model A'), device(2, 'SN-BETA', 'Model B')];

function renderComponent(props: Partial<Parameters<typeof DeviceMultiSelect>[0]> = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const onChange = vi.fn();
  const result = render(
    <QueryClientProvider client={queryClient}>
      <DeviceMultiSelect selectedIds={[]} onChange={onChange} {...props} />
    </QueryClientProvider>,
  );
  return { ...result, onChange };
}

/** 触发器是全组件唯一带 `aria-expanded` 的按钮——用它定位，不依赖「点击选择设备/已选 N 台」文案分叉。 */
async function openDropdown() {
  fireEvent.click(await screen.findByRole('button', { expanded: false }));
}

describe('DeviceMultiSelect', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockFetchAllDevices.mockResolvedValue(DEVICES);
  });

  it('设备数据源是 fetchAllDevices（翻页契约，#3131）', async () => {
    renderComponent();
    await openDropdown();

    expect(mockFetchAllDevices).toHaveBeenCalledTimes(1);
    // 渲染来自翻页助手的行 ⇒ 数据确实经 fetchAllDevices 流入
    expect(await screen.findByText('SN-ALPHA')).toBeInTheDocument();
    expect(screen.getByText('SN-BETA')).toBeInTheDocument();
  });

  it('加载中显示占位，不闪「暂无设备」', async () => {
    mockFetchAllDevices.mockReturnValue(new Promise(() => {}));
    renderComponent();
    await openDropdown();

    expect(screen.getByText('设备列表加载中…')).toBeInTheDocument();
    expect(screen.queryByText('暂无设备')).not.toBeInTheDocument();
  });

  it('勾选回调新增 id（受控：由父层回写 selectedIds）', async () => {
    const { onChange } = renderComponent();
    await openDropdown();
    await screen.findByText('SN-ALPHA');

    const alphaCheckbox = screen.getAllByRole('checkbox').find((box) =>
      box.closest('label')?.textContent?.includes('SN-ALPHA'),
    );
    expect(alphaCheckbox).toBeTruthy();
    fireEvent.click(alphaCheckbox as HTMLElement);
    expect(onChange).toHaveBeenLastCalledWith([1]);
  });

  it('已勾选项取消后 onChange 移除该 id', async () => {
    const { onChange } = renderComponent({ selectedIds: [1, 2] });
    await openDropdown();
    // 数据加载完成的同步点：chip 的移除按钮（serial 文本此时在 chip 与下拉行各出现一次，
    // 不能用 getByText 单点定位）
    await screen.findByLabelText('移除设备 SN-ALPHA');

    const betaCheckbox = screen.getAllByRole('checkbox').find((box) =>
      box.closest('label')?.textContent?.includes('SN-BETA'),
    );
    expect(betaCheckbox).toBeTruthy();
    fireEvent.click(betaCheckbox as HTMLElement);
    expect(onChange).toHaveBeenLastCalledWith([1]);
  });

  it('chip 显示 serial，未知 id 回落 #id，移除按钮回调去掉对应 id', async () => {
    const { onChange } = renderComponent({ selectedIds: [1, 99] });

    expect(await screen.findByText('SN-ALPHA')).toBeInTheDocument();
    // 99 不在设备集（刚登记未进缓存/已退役），chip 仍要可移除且回落显示 #99
    expect(screen.getByText('#99')).toBeInTheDocument();

    fireEvent.click(screen.getByLabelText('移除设备 SN-ALPHA'));
    expect(onChange).toHaveBeenLastCalledWith([99]);
  });

  it('搜索按 serial/型号过滤，无匹配显示「无匹配设备」', async () => {
    renderComponent();
    await openDropdown();
    await screen.findByText('SN-ALPHA');

    fireEvent.change(screen.getByLabelText('搜索设备'), { target: { value: 'model a' } });
    expect(screen.getByText('SN-ALPHA')).toBeInTheDocument();
    expect(screen.queryByText('SN-BETA')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('搜索设备'), { target: { value: 'zzz' } });
    await waitFor(() => {
      expect(screen.getByText('无匹配设备')).toBeInTheDocument();
    });
    // 「无匹配」≠「暂无设备」——空集归因不能指错（#2362 同族口径）
    expect(screen.queryByText('暂无设备')).not.toBeInTheDocument();
  });

  it('空 fleet 时下拉显示「暂无设备」', async () => {
    mockFetchAllDevices.mockResolvedValue([]);
    renderComponent();
    await openDropdown();

    expect(await screen.findByText('暂无设备')).toBeInTheDocument();
  });

  it('有已选时触发器文案报台数', async () => {
    mockFetchAllDevices.mockResolvedValue(DEVICES);
    renderComponent({ selectedIds: [1, 2] });

    expect(await screen.findByRole('button', { name: /已选 2 台设备/ })).toBeInTheDocument();
  });
});
