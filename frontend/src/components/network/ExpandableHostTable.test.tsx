import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExpandableHostTable, type HostTableData } from './ExpandableHostTable';

const host: HostTableData = {
  id: 1,
  name: '上海执行机-01',
  ip: '192.0.2.31',
  status: 'ONLINE',
  watcher_admin_active: true,
  agent_installed: true,
  agent_protocol_version: '1.4',
  agent_code_revision: 'abc1234',
  expected_code_revision: 'abc1234',
  agent_code_sync_status: 'matched',
  last_heartbeat: new Date().toISOString(),
  device_count: 6,
  active_tasks: 2,
  resources: {
    cpu_load: 23,
    ram_usage: 61,
    disk_usage: 48,
    temperature: 42,
    uptime_seconds: 7200,
  },
};

describe('ExpandableHostTable', () => {
  it('uses compact overview columns and combines related values', () => {
    render(<ExpandableHostTable hosts={[host]} />);

    const headers = screen
      .getAllByRole('columnheader')
      .map((header) => header.textContent?.trim());

    expect(headers).toEqual(expect.arrayContaining([
      '主机',
      '状态',
      '设备 / 任务',
      '资源',
      'Agent',
      '心跳',
      '操作',
    ]));
    expect(headers).not.toEqual(expect.arrayContaining(['IP地址', '设备数', '任务数', 'Watch状态']));

    expect(screen.getByText(host.name).closest('td')).toBe(screen.getByText(host.ip).closest('td'));
    expect(screen.getByText('在线 6')).toBeInTheDocument();
    expect(screen.getByText('任务 2')).toBeInTheDocument();
    expect(screen.getByText('1.4 @abc1234')).toBeInTheDocument();
    expect(screen.getByText('已对齐')).toBeInTheDocument();

    expect(screen.getByRole('columnheader', { name: '资源' })).toHaveClass('2xl:hidden');
    expect(screen.getByRole('columnheader', { name: 'CPU' })).toHaveClass('hidden', '2xl:table-cell');
    expect(screen.getByRole('columnheader', { name: '内存' })).toHaveClass('hidden', '2xl:table-cell');
    expect(screen.getByRole('columnheader', { name: '磁盘' })).toHaveClass('hidden', '2xl:table-cell');
  });

  it('renders the ip subtitle only when it differs from the host name', () => {
    const ipNamedHost: HostTableData = { ...host, name: host.ip };
    render(<ExpandableHostTable hosts={[ipNamedHost]} />);

    expect(screen.getAllByText(host.ip)).toHaveLength(1);
  });

  it('shows 未知 when disk usage is unavailable', () => {
    const unknownDisk: HostTableData = {
      ...host,
      resources: { ...host.resources!, disk_usage: null },
    };
    render(<ExpandableHostTable hosts={[unknownDisk]} />);
    const diskCells = screen.getAllByTestId('host-disk-usage');
    expect(diskCells.length).toBeGreaterThan(0);
    expect(diskCells.every((el) => el.textContent === '未知')).toBe(true);
  });

  it('shows deployment time with full date in expanded Agent section', () => {
    const deployedHost: HostTableData = {
      ...host,
      agent_code_deployed: 'def5678',
      agent_code_deployed_at: '2026-08-06T03:36:31.714551+00:00',
    };
    render(<ExpandableHostTable hosts={[deployedHost]} />);

    fireEvent.click(screen.getByText(deployedHost.name));

    expect(screen.getByText('部署时间')).toBeInTheDocument();
    const deployedAt = screen.getByText((content) => content.includes('2026') && content.includes('08') && content.includes('36'));
    expect(deployedAt.textContent).toMatch(/2026/);
    expect(deployedAt.textContent).not.toMatch(/^\d{2}:\d{2}:\d{2}$/);
  });

  it('keeps Watcher management and full Agent details in the expanded row', () => {
    const onWatcherAdminStateChange = vi.fn();
    render(
      <ExpandableHostTable
        hosts={[host]}
        onWatcherAdminStateChange={onWatcherAdminStateChange}
        canManageWatcherAdminState
      />,
    );

    fireEvent.click(screen.getByText(host.name));

    expect(screen.getByText('Agent 版本')).toBeInTheDocument();
    expect(screen.getAllByText('@abc1234')).toHaveLength(2);
    const watcherSwitch = screen.getByRole('switch', { name: `${host.name} Watcher 管理开关` });
    fireEvent.click(watcherSwitch);
    expect(onWatcherAdminStateChange).toHaveBeenCalledWith(host.id, false);
  });

  it('does not expand the row when the primary operation is clicked', () => {
    const onHotUpdate = vi.fn();
    render(<ExpandableHostTable hosts={[host]} onHotUpdate={onHotUpdate} />);

    fireEvent.click(screen.getByRole('button', { name: `${host.name} 热更新 Agent` }));

    expect(onHotUpdate).toHaveBeenCalledWith(host.id);
    expect(screen.queryByText('Agent 版本')).not.toBeInTheDocument();
  });

  it('adds bottom spacer when hosts are selected so the bulk bar does not cover the last row', () => {
    const { rerender } = render(
      <ExpandableHostTable
        hosts={[host]}
        selectedIds={new Set()}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('host-table-selection-spacer')).not.toBeInTheDocument();

    rerender(
      <ExpandableHostTable
        hosts={[host]}
        selectedIds={new Set([host.id])}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.getByTestId('host-table-selection-spacer')).toHaveClass('h-40');

    rerender(
      <ExpandableHostTable
        hosts={[host]}
        selectedIds={new Set()}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('host-table-selection-spacer')).not.toBeInTheDocument();
  });

  it('shows partial selection and highlights selected rows', () => {
    const secondHost: HostTableData = {
      ...host,
      id: 2,
      name: '上海执行机-02',
      ip: '192.0.2.32',
    };
    render(
      <ExpandableHostTable
        hosts={[host, secondHost]}
        selectedIds={new Set([host.id])}
        onSelectionChange={vi.fn()}
      />,
    );

    const selectAll = screen.getByRole('checkbox', { name: '选择全部主机' }) as HTMLInputElement;
    expect(selectAll.indeterminate).toBe(true);
    expect(screen.getByRole('checkbox', { name: `选择主机 ${host.name}` }).closest('tr')).toHaveAttribute(
      'data-state',
      'selected',
    );
  });

  it('filters hosts when summary status cards are clicked', () => {
    const offlineHost: HostTableData = {
      ...host,
      id: 2,
      name: '上海执行机-02',
      ip: '192.0.2.32',
      status: 'OFFLINE',
      agent_installed: false,
      resources: undefined,
    };
    const degradedHost: HostTableData = {
      ...host,
      id: 3,
      name: '上海执行机-03',
      ip: '192.0.2.33',
      status: 'DEGRADED',
    };
    render(<ExpandableHostTable hosts={[host, offlineHost, degradedHost]} />);

    expect(screen.getByText('上海执行机-01')).toBeInTheDocument();
    expect(screen.getByText('上海执行机-02')).toBeInTheDocument();
    expect(screen.getByText('上海执行机-03')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '筛选在线主机' }));
    expect(screen.getByText('上海执行机-01')).toBeInTheDocument();
    expect(screen.queryByText('上海执行机-02')).not.toBeInTheDocument();
    expect(screen.queryByText('上海执行机-03')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '筛选在线主机' })).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(screen.getByRole('button', { name: '筛选告警主机' }));
    expect(screen.queryByText('上海执行机-01')).not.toBeInTheDocument();
    expect(screen.getByText('上海执行机-03')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '筛选离线主机' }));
    expect(screen.getByText('上海执行机-02')).toBeInTheDocument();
    expect(screen.queryByText('上海执行机-03')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '筛选全部主机' }));
    expect(screen.getByText('上海执行机-01')).toBeInTheDocument();
    expect(screen.getByText('上海执行机-02')).toBeInTheDocument();
    expect(screen.getByText('上海执行机-03')).toBeInTheDocument();
  });

});
