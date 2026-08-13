import type { ReactNode } from 'react';
import { render, screen } from '@testing-library/react';
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
  server: {
    hostname: 'debian13',
    address: '192.0.2.202',
    cpu_count: 20,
    uptime_seconds: 172800,
  },
  storage: {
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
  nfs: {
    service_ready: true,
    exported: true,
    export_targets: ['192.0.2.0/23'],
    server_threads: 16,
    requests_per_second: 1.5,
    rpc_errors_per_second: 0,
    stale_file_handles_total: 0,
    connections_total: 45,
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
  history: {
    hours: 6,
    capacity_usage_pct: [{ timestamp: 1785158998, value: 0.1 }],
    cpu_usage_pct: [{ timestamp: 1785158998, value: 12.3 }],
    memory_usage_pct: [{ timestamp: 1785158998, value: 42.1 }],
    nfs_requests_per_second: [{ timestamp: 1785158998, value: 1.5 }],
  },
  monitoring: { prometheus_available: true, error: null },
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
    expect(screen.getByText('1/1')).toBeInTheDocument();
    expect(screen.getByText('运行中')).toBeInTheDocument();
    expect(screen.getByText('192.0.2.0/23')).toBeInTheDocument();
    expect(screen.getByText('已挂载')).toBeInTheDocument();
  });
});
