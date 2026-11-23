import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { DeviceDetailDrawer } from './DeviceDetailDrawer';
import type { DeviceTableData } from './ExpandableDeviceTable';

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return {
    ...actual,
    api: {
      ...actual.api,
      devices: {
        ...actual.api.devices,
        returnDebts: vi.fn().mockResolvedValue([]),
      },
    },
  };
});

const SAMPLE: DeviceTableData = {
  id: 42,
  serial: 'DEV-DRAWER-42',
  model: 'Pixel 9',
  product: 'komodo',
  status: 'offline',
  build_display_id: 'AP2A.240805',
  host_id: 'host-7',
  host_name: 'lab-host-07',
  project_key: 'proj-alpha',
  battery_level: 64,
  temperature: 43.2,
  network_latency: 145,
  disk_total: 128 * 1024 ** 3,
  disk_used: 96 * 1024 ** 3,
  tags: ['smoke', 'shanghai'],
  last_seen: '2026-11-23T08:00:00Z',
};

function renderDrawer(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter>
      <QueryClientProvider client={qc}>{ui}</QueryClientProvider>
    </MemoryRouter>,
  );
}

describe('DeviceDetailDrawer（#4117 U2-1b / D2）', () => {
  it('展示设备核心元信息、遥测指标、项目/AI 助手深链与上下台导航', () => {
    const onPrev = vi.fn();
    const onNext = vi.fn();
    const onClose = vi.fn();

    renderDrawer(
      <DeviceDetailDrawer
        device={SAMPLE}
        onClose={onClose}
        isAdmin
        currentIndex={1}
        totalCount={5}
        onPrev={onPrev}
        onNext={onNext}
      />,
    );

    const drawer = screen.getByTestId('fleet-device-drawer');
    expect(drawer).toHaveAttribute('role', 'dialog');
    expect(drawer).toHaveAttribute('aria-modal', 'true');
    expect(drawer).toHaveTextContent('DEV-DRAWER-42');
    expect(drawer).toHaveTextContent('Pixel 9');
    expect(drawer).toHaveTextContent('64%');
    expect(drawer).toHaveTextContent('43.2');
    expect(drawer).toHaveTextContent('128 GiB');

    const aiLink = screen.getByRole('link', { name: /向 AI 助手提问/ });
    expect(aiLink.getAttribute('href')).toContain('/assistant?prompt=');
    expect(aiLink.getAttribute('href')).toContain('DEV-DRAWER-42');

    fireEvent.click(screen.getByTestId('fleet-device-drawer-prev'));
    fireEvent.click(screen.getByTestId('fleet-device-drawer-next'));
    expect(onPrev).toHaveBeenCalledOnce();
    expect(onNext).toHaveBeenCalledOnce();

    fireEvent.keyDown(drawer, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledOnce();
  });
});
