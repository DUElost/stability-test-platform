import type { ReactNode } from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { describe, expect, it, vi } from 'vitest';
import type { FileServerOverview } from '@/utils/api/types';
import FileServerPage from './FileServerPage';

const mocks = vi.hoisted(() => ({ fileServer: vi.fn() }));

vi.mock('@/utils/api', () => ({
  api: { stats: { fileServer: mocks.fileServer } },
}));

vi.mock('recharts', () => ({
  ResponsiveContainer: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  LineChart: ({ children }: { children: ReactNode }) => <div>{children}</div>,
  Line: () => null,
  Tooltip: () => null,
  XAxis: () => null,
  YAxis: () => null,
}));

const overview: FileServerOverview = {
  generated_at: '2026-07-27T13:30:00Z',
  status: 'healthy',
  control_plane: {
    node: {
      hostname: 'debian13',
      address: '192.0.2.202',
      cpu_count: 20,
      uptime_seconds: 172800,
    },
    system: {
      cpu_usage_pct: 12.3,
      memory_usage_pct: 42.1,
      memory_total_bytes: 24875495424,
      load1: 1.2,
      disk_read_bytes_per_second: 1024,
      disk_write_bytes_per_second: 2048,
      network_receive_bytes_per_second: 4096,
      network_transmit_bytes_per_second: 8192,
    },
    client_mount: {
      path: '/mnt/stp-aee',
      source: '/dev/sda1',
      filesystem: 'ext4',
      mounted: true,
      backend_write_access: true,
    },
    monitoring: { prometheus_available: true, error: null },
  },
  storage_server: {
    node: {
      hostname: 'debian13',
      address: '192.0.2.202',
      cpu_count: 20,
      uptime_seconds: 172800,
    },
    same_source: true,
    system: {
      cpu_usage_pct: 12.3,
      memory_usage_pct: 42.1,
      memory_total_bytes: 24875495424,
      load1: 1.2,
      disk_read_bytes_per_second: 1024,
      disk_write_bytes_per_second: 2048,
      network_receive_bytes_per_second: 4096,
      network_transmit_bytes_per_second: 8192,
    },
    disk: {
      path: '/mnt/stp-aee',
      source: '/dev/sda1',
      filesystem: 'ext4',
      mounted: true,
      backend_write_access: true,
      total_bytes: 983349346304,
      used_bytes: 2142208,
      available_bytes: 983230406656,
      used_pct: 0.1,
      inode_total: 61054976,
      inode_used: 16,
      inode_available: 61054960,
      inode_used_pct: 0.1,
    },
    nfs: {
      service_ready: true,
      exported: true,
      export_targets: ['192.0.2.0/24', '198.51.100.0/24'],
      server_threads: 16,
      requests_per_second: 1.5,
      rpc_errors_per_second: 0,
      stale_file_handles_total: 0,
      connections_total: 45,
    },
    monitoring: { prometheus_available: true, error: null },
  },
  agents: {
    total: 1,
    mounted: 1,
    failed: 0,
    unreported: 0,
    items: [{
      host_id: '198-51-100-124',
      ip: '198.51.100.124',
      status: 'ONLINE',
      mounted: true,
      last_heartbeat: '2026-07-27T13:29:58Z',
    }],
  },
  device_log_disks: {
    total: 1,
    reported: 1,
    warning: 0,
    critical: 0,
    items: [{
      host_id: '198-51-100-124',
      ip: '198.51.100.124',
      path: '/mnt/hdd/aee_events',
      total_bytes: 966367641600,
      used_bytes: 10737418240,
      available_bytes: 955630223360,
      usage_percent: 1.1,
      last_heartbeat: '2026-07-27T13:29:58Z',
    }],
  },
  history: {
    hours: 6,
    capacity_usage_pct: [{ timestamp: 1785158998, value: 0.1 }],
    cpu_usage_pct: [{ timestamp: 1785158998, value: 12.3 }],
    memory_usage_pct: [{ timestamp: 1785158998, value: 42.1 }],
    nfs_requests_per_second: [{ timestamp: 1785158998, value: 1.5 }],
  },
  alerts: [],
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <FileServerPage />
    </QueryClientProvider>,
  );
}

describe('FileServerPage', () => {
  it('renders capacity, NFS health, and Agent mount compliance', async () => {
    mocks.fileServer.mockResolvedValueOnce(overview);
    renderPage();

    expect(await screen.findByText('916 GiB')).toBeInTheDocument();
    expect(screen.getByText(/中心存储挂载 1\/1/)).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();
    expect(screen.getByText('192.0.2.0/24, 198.51.100.0/24')).toBeInTheDocument();
    expect(screen.getAllByText('已挂载').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('与控制面同机')).toBeInTheDocument();
    expect(screen.getAllByText('debian13 · 192.0.2.202').length).toBeGreaterThanOrEqual(2);
    expect(screen.getByText('/mnt/hdd/aee_events')).toBeInTheDocument();
    expect(screen.getByText('1.1%')).toBeInTheDocument();
  });

  it('switches history range and refetches with the selected hours', async () => {
    mocks.fileServer.mockResolvedValue(overview);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText('916 GiB')).toBeInTheDocument();
    expect(mocks.fileServer).toHaveBeenCalledWith(6);

    await user.click(screen.getByRole('button', { name: '24H' }));
    await waitFor(() => expect(mocks.fileServer).toHaveBeenCalledWith(24));

    await user.click(screen.getByRole('button', { name: '7D' }));
    await waitFor(() => expect(mocks.fileServer).toHaveBeenCalledWith(168));
  });

  it('switches between control-plane and storage-server panels', async () => {
    mocks.fileServer.mockResolvedValueOnce(overview);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText('916 GiB');
    const controlPanel = document.getElementById('panel-control-plane');
    const storagePanel = document.getElementById('panel-storage-server');
    expect(controlPanel).not.toBeNull();
    expect(storagePanel).not.toBeNull();
    expect(storagePanel).toHaveAttribute('hidden');

    await user.click(screen.getByRole('tab', { name: '中心存储机' }));
    expect(storagePanel).not.toHaveAttribute('hidden');
    expect(controlPanel).toHaveAttribute('hidden');
  });
});
