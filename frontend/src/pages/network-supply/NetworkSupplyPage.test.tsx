import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi, beforeEach } from 'vitest';

import { ApiError } from '@/utils/api';

const mocks = vi.hoisted(() => ({
  listLoads: vi.fn(),
  createPool: vi.fn(),
  listSessions: vi.fn(),
  enableManual: vi.fn(),
  renewManual: vi.fn(),
  releaseSession: vi.fn(),
  auditOverride: vi.fn(),
  devicesList: vi.fn(),
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
        create: mocks.createPool,
      },
      networkSupply: {
        listSessions: mocks.listSessions,
        enableManual: mocks.enableManual,
        renewManual: mocks.renewManual,
        releaseSession: mocks.releaseSession,
        auditOverride: mocks.auditOverride,
      },
      devices: {
        ...actual.api.devices,
        list: mocks.devicesList,
      },
    },
  };
});

vi.mock('@/hooks/useToast', () => ({
  useToast: () => ({ success: vi.fn(), error: vi.fn() }),
}));

vi.mock('@/hooks/useConfirm', () => ({
  useConfirm: () => ({ confirm: vi.fn().mockResolvedValue(true), dialog: null }),
}));

import NetworkSupplyPage from './NetworkSupplyPage';

function renderPage() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>
        <NetworkSupplyPage />
      </QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('NetworkSupplyPage（ADR-0066 D7）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listSessions.mockResolvedValue([]);
    mocks.devicesList.mockResolvedValue({ items: [], total: 0 });
  });

  it('403 → 无权限提示（保留 #2359 语义，不出现「检查后端服务连接」）', async () => {
    mocks.listLoads.mockRejectedValue(
      new ApiError('HTTP_403', 'Forbidden', { status: 403 }),
    );

    renderPage();

    await waitFor(() =>
      expect(screen.getByText(/无权限访问网络供给资源池/)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/检查后端服务连接/)).not.toBeInTheDocument();
  });

  it('管理器填值后提交 WiFi 池 → 请求带 DOM 值（#2456）', async () => {
    mocks.listLoads.mockResolvedValue([]);
    mocks.createPool.mockResolvedValue({ id: 1 });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /新增供给池/ }));

    const fill = (el: HTMLElement, value: string) => {
      const setter = Object.getOwnPropertyDescriptor(
        window.HTMLInputElement.prototype,
        'value',
      )!.set!;
      setter.call(el, value);
      el.dispatchEvent(new Event('input', { bubbles: false }));
    };

    fill(screen.getByLabelText('名称'), 'Lab A 2.4G');
    fill(screen.getByLabelText('SSID'), 'lab-a-2g');
    fill(screen.getByLabelText('密码'), 'tPe-KLu-3Uw-3Fb');

    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(mocks.createPool).toHaveBeenCalled());
    expect(mocks.createPool.mock.calls[0][0]).toMatchObject({
      name: 'Lab A 2.4G',
      resource_type: 'wifi',
      config: { egress: 'direct', ssid: 'lab-a-2g', password: 'tPe-KLu-3Uw-3Fb' },
    });
  });

  it('支持创建 usb_net 池并在 Phase 1 禁用 usb_net + vpn', async () => {
    mocks.listLoads.mockResolvedValue([]);
    mocks.createPool.mockResolvedValue({ id: 2 });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /新增供给池/ }));
    fireEvent.click(screen.getByLabelText('USB 反向供网'));

    const vpnRadio = screen.getByLabelText(/VPN 出口/) as HTMLInputElement;
    expect(vpnRadio).toBeDisabled();

    fireEvent.change(screen.getByLabelText('名称'), { target: { value: 'USB Direct Pool' } });
    fireEvent.click(screen.getByRole('button', { name: '创建' }));

    await waitFor(() => expect(mocks.createPool).toHaveBeenCalled());
    expect(mocks.createPool.mock.calls[0][0]).toMatchObject({
      name: 'USB Direct Pool',
      resource_type: 'usb_net',
      config: {
        egress: 'direct',
        bridge_name: 'br-stpusb-dir',
        subnet_cidr: '10.200.0.0/22',
        max_devices_per_host: 4,
      },
    });
  });

  it('受管会话视图明示排除口径，展示 legacy_path/RELEASE_FAILED 与专用审计放行', async () => {
    mocks.listLoads.mockResolvedValue([
      {
        id: 9,
        name: 'USB-Pool',
        resource_type: 'usb_net',
        max_concurrent_devices: 4,
        current_devices: 1,
        host_group: null,
        is_active: true,
        config: { egress: 'direct', bridge_name: 'br-stpusb-dir', subnet_cidr: '10.200.0.0/22', max_devices_per_host: 4 },
      },
    ]);
    mocks.listSessions.mockResolvedValue([
      {
        id: 101,
        device_id: 7,
        device_serial: 'SER-NET-101',
        host_id: 'h-lab-1',
        resource_pool_id: 9,
        pool_name: 'USB-Pool',
        resource_type: 'usb_net',
        egress: 'direct',
        source: 'manual',
        plan_run_id: null,
        job_instance_id: null,
        lease_id: 88,
        generation: 5,
        legacy_path: false,
        status: 'RELEASE_FAILED',
        release_reason: null,
        assigned_ip: '10.200.0.12',
        usb_devpath: '1-2.3',
        created_by: 'admin',
        expires_at: new Date(Date.now() + 3600_000).toISOString(),
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        released_at: null,
      },
      {
        id: 102,
        device_id: 8,
        device_serial: 'SER-WIFI-102',
        host_id: 'h-lab-1',
        resource_pool_id: 1,
        pool_name: 'LabWiFi',
        resource_type: 'wifi',
        egress: 'direct',
        source: 'job',
        plan_run_id: 55,
        job_instance_id: 501,
        lease_id: 89,
        generation: 2,
        legacy_path: true,
        status: 'ACTIVE',
        release_reason: null,
        assigned_ip: null,
        usb_devpath: null,
        created_by: 'admin',
        expires_at: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        released_at: null,
      },
    ]);
    mocks.auditOverride.mockResolvedValue({
      session: { id: 101, status: 'RELEASED', release_reason: 'manual_override' },
      closed_debt_count: 1,
      lease_released: true,
      lease_converted_to_debt: false,
      open_debt_count: 0,
      usbnet_revoke_directive: { session_id: 101, generation: 5 },
    });

    renderPage();

    fireEvent.click(await screen.findByRole('tab', { name: /已启用供网设备/ }));

    expect(await screen.findByTestId('network-sessions-scope-banner')).toHaveTextContent(
      '不纳入两类非受管路径',
    );
    expect(screen.getByText('SER-NET-101')).toBeInTheDocument();
    expect(screen.getByText('RELEASE_FAILED（回收失败）')).toBeInTheDocument();
    expect(screen.getByText('存量 WiFi 路径')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '审计放行' }));
    fireEvent.change(screen.getByLabelText(/凭据 \/ 工单/), {
      target: { value: 'OPS-4212-test' },
    });
    fireEvent.click(screen.getByRole('button', { name: '确认审计放行' }));

    await waitFor(() => expect(mocks.auditOverride).toHaveBeenCalledWith(101, {
      reason_category: 'device_unreachable',
      evidence_ref: 'OPS-4212-test',
    }));
  });
});
