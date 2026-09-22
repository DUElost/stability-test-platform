import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import type { ComponentProps } from 'react';
import DeviceOverview from './DeviceOverview';
import type { PlanRunDevicesPayload } from '@/utils/api/types';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// #2601：组件新增了 host 查询（hostKeys.retiredList），渲染必须带 QueryClientProvider。
const HOSTS = [
  { id: 'host-101', name: 'node-a', ip: '10.0.0.1' },
  { id: 'host-202', name: null, ip: '10.0.0.2' },
];

vi.mock('@/utils/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/utils/api')>();
  return { ...actual, fetchAllHosts: vi.fn(async () => HOSTS) };
});

function renderWithClient(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const wrap = (node: React.ReactElement) => (
    <QueryClientProvider client={queryClient}>{node}</QueryClientProvider>
  );
  const result = render(wrap(ui));
  // 受控用例里的 rerender 必须带同一 provider（RTL 的 rerender 会整棵树重渲）
  return { ...result, rerender: (node: React.ReactElement) => result.rerender(wrap(node)) };
}

const fixture: PlanRunDevicesPayload = {
  plan_run_id: 12,
  total: 4,
  by_status: { all: 4, running: 2, backoff: 1, failed: 1 },
  by_host: { 'host-101': 2, 'host-202': 2 },
  devices: [
    {
      device_id: 1,
      device_serial: 'DEV-AAAA',
      device_model: 'Pixel 8',
      host_id: 'host-101',
      job_id: 3001,
      job_status: 'RUNNING',
      ui_status: 'running',
      current_stage: 'patrol',
      current_step: 'monkey_check',
      patrol_cycle_count: 12,
      patrol_success_cycle_count: 12,
      patrol_failed_cycle_count: 0,
      current_failure_streak: 0,
      next_retry_at: null,
      manual_action: null,
      log_signal_count: 0,
      last_heartbeat_at: '2026-05-08T12:30:00Z',
      started_at: '2026-05-08T12:00:00Z',
      ended_at: null,
    },
    {
      device_id: 2,
      device_serial: 'DEV-BBBB',
      device_model: 'Pixel 8',
      host_id: 'host-101',
      job_id: 3002,
      job_status: 'RUNNING',
      ui_status: 'backoff',
      current_stage: 'patrol',
      current_step: 'monkey_check',
      patrol_cycle_count: 12,
      patrol_success_cycle_count: 9,
      patrol_failed_cycle_count: 3,
      current_failure_streak: 4,
      next_retry_at: new Date(Date.now() + 60_000).toISOString(),
      manual_action: null,
      log_signal_count: 2,
      last_heartbeat_at: '2026-05-08T12:30:00Z',
      started_at: '2026-05-08T12:00:00Z',
      ended_at: null,
    },
    {
      device_id: 3,
      device_serial: 'DEV-CCCC',
      device_model: 'Pixel 8',
      host_id: 'host-202',
      job_id: 3003,
      job_status: 'FAILED',
      ui_status: 'failed',
      current_stage: 'failed',
      current_step: null,
      patrol_cycle_count: 5,
      patrol_success_cycle_count: 2,
      patrol_failed_cycle_count: 3,
      current_failure_streak: 0,
      next_retry_at: null,
      manual_action: null,
      log_signal_count: 5,
      last_heartbeat_at: null,
      started_at: '2026-05-08T11:00:00Z',
      ended_at: '2026-05-08T11:30:00Z',
      status_reason: 'patrol_step_failed: monkey_launch',
    },
    {
      device_id: 4,
      device_serial: 'DEV-DDDD',
      device_model: 'Pixel 8',
      host_id: 'host-202',
      job_id: 3004,
      job_status: 'RUNNING',
      ui_status: 'running',
      current_stage: 'patrol',
      current_step: 'monkey_check',
      patrol_cycle_count: 12,
      patrol_success_cycle_count: 12,
      patrol_failed_cycle_count: 0,
      current_failure_streak: 0,
      next_retry_at: null,
      manual_action: 'EXIT_REQUESTED',
      log_signal_count: 0,
      last_heartbeat_at: '2026-05-08T12:30:00Z',
      started_at: '2026-05-08T12:00:00Z',
      ended_at: null,
    },
  ],
};

// DeviceOverview defaults to the grid (minimap) view; the table that
// DeviceMatrixCard used to own now lives behind the table-view toggle.
function renderInTableView(
  props: Partial<ComponentProps<typeof DeviceOverview>> = {},
) {
  const result = renderWithClient(<DeviceOverview data={fixture} {...props} />);
  fireEvent.click(screen.getByTestId('device-overview-table-btn'));
  return result;
}

describe('DeviceOverview', () => {
  it('HOST 列与 HOST 筛选显示主机显示名而不是内部 host_id（#2601）', async () => {
    // 现场形态：`by_host` 的键是内部 slug（host-101），此前被直接当展示值——同一条
    // host 事实在报告页显示 IP、在这里显示 slug，被读成两台不同主机。
    renderInTableView();
    const withName = screen.getByTestId('device-row-3001');
    await waitFor(() => expect(withName).toHaveTextContent('node-a'));
    expect(withName).not.toHaveTextContent('host-101');

    // 没有 name 的主机退回 ip（不是 slug）
    const withoutName = screen.getByTestId('device-row-3003');
    await waitFor(() => expect(withoutName).toHaveTextContent('10.0.0.2'));

    const filter = screen.getByTestId('device-host-filter');
    await waitFor(() =>
      expect(within(filter).getByRole('option', { name: /node-a/ })).toBeInTheDocument(),
    );
    // 筛选的 value 仍是 host_id（回传父组件的语义不变）
    expect(within(filter).getByRole('option', { name: /node-a/ })).toHaveValue('host-101');
  });

  it('defaults to grid (minimap) view and switches to table on toggle', () => {
    renderWithClient(<DeviceOverview data={fixture} />);
    expect(screen.getByTestId('minimap-cell-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    expect(screen.getByTestId('device-row-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('minimap-cell-3001')).not.toBeInTheDocument();
  });

  it('renders all devices in table view with status pills + facet counts', () => {
    renderInTableView();
    expect(screen.getByTestId('device-row-3001')).toHaveTextContent('DEV-AAAA');
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('退避');
    // failure streak 4 → highlight red and shown as `× 4`
    expect(screen.getByTestId('device-row-3002')).toHaveTextContent('× 4');
    expect(screen.getByTestId('device-row-3003')).toHaveTextContent('失败');
    // exit_requested job shows "退出待执行"
    expect(screen.getByTestId('device-row-3004')).toHaveTextContent('退出待执行');
    // facet counts on filter buttons (filter bar is shared across both views)
    expect(screen.getByTestId('device-status-filter-running')).toHaveTextContent('2');
    expect(screen.getByTestId('device-status-filter-backoff')).toHaveTextContent('1');
    expect(screen.queryByTestId('device-status-filter-risk')).not.toBeInTheDocument();
  });

  it('forwards filter changes to parent', () => {
    const onStatus = vi.fn();
    const onHost = vi.fn();
    renderWithClient(
      <DeviceOverview
        data={fixture}
        onStatusFilterChange={onStatus}
        onHostFilterChange={onHost}
      />,
    );
    fireEvent.click(screen.getByTestId('device-status-filter-backoff'));
    expect(onStatus).toHaveBeenCalledWith('backoff');
    fireEvent.change(screen.getByTestId('device-host-filter'), {
      target: { value: 'host-202' },
    });
    expect(onHost).toHaveBeenCalledWith('host-202');
  });

  it('triggers onSelectDevice when a table row is clicked', () => {
    const onSelect = vi.fn();
    renderInTableView({ onSelectDevice: onSelect });
    fireEvent.click(screen.getByTestId('device-row-3002'));
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 3002, ui_status: 'backoff' }),
    );
  });

  it('shows empty state when no devices', () => {
    renderWithClient(
      <DeviceOverview
        data={{
          plan_run_id: 12,
          total: 0,
          by_status: { all: 0 },
          by_host: {},
          devices: [],
        }}
      />,
    );
    expect(screen.getByText('该过滤条件下暂无设备')).toBeInTheDocument();
  });

  it('exposes status_reason as title tooltip on the status pill for failed devices', () => {
    renderInTableView();
    const failedRow = screen.getByTestId('device-row-3003');
    // pill is the first <span> with a title; assert it carries the full reason
    const pill = failedRow.querySelector('span[title]') as HTMLElement | null;
    expect(pill).not.toBeNull();
    expect(pill!.getAttribute('title')).toBe('patrol_step_failed: monkey_launch');
    // Inline truncated text should NOT be rendered (reason moved to tooltip + drawer)
    expect(failedRow).not.toHaveTextContent('patrol_step_failed: monkey_launch');
  });

  it('renders unknown status pill distinct from failed', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, unknown: 1 },
      devices: [
        {
          device_id: 9,
          device_serial: 'DEV-UNKN',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3009,
          job_status: 'UNKNOWN',
          ui_status: 'unknown',
          current_stage: 'unknown',
          current_step: null,
          patrol_cycle_count: 3,
          patrol_success_cycle_count: 2,
          patrol_failed_cycle_count: 1,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: '2026-05-08T11:00:00Z',
          ended_at: null,
          status_reason: 'lease_expired',
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-row-3009')).toHaveTextContent('已断开');
    const pill = screen.getByTestId('device-row-3009').querySelector('span[title]');
    expect(pill?.getAttribute('title')).toMatch(/lease_expired/);
    expect(pill?.getAttribute('title')).toMatch(/grace/);
  });

  it('keeps a running device as 运行中 even when anomaly count is non-zero', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, running: 1 },
      devices: [
        {
          ...fixture.devices[0],
          device_id: 11,
          device_serial: 'DEV-RUN-LOG',
          job_id: 3011,
          ui_status: 'running',
          log_signal_count: 7,
        },
      ],
    };
    renderInTableView({ data });
    const row = screen.getByTestId('device-row-3011');
    expect(row).toHaveTextContent('运行中');
    expect(row).toHaveTextContent('7');
    expect(row).not.toHaveTextContent('风险');
  });

  it('shows grace countdown in wait column for unknown devices', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, unknown: 1 },
      devices: [
        {
          device_id: 9,
          device_serial: 'DEV-UNKN',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3009,
          job_status: 'UNKNOWN',
          ui_status: 'unknown',
          current_stage: 'unknown',
          current_step: null,
          patrol_cycle_count: 3,
          patrol_success_cycle_count: 2,
          patrol_failed_cycle_count: 1,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: '2026-05-08T11:00:00Z',
          ended_at: '2026-05-08T11:30:00Z',
          status_reason: 'lease_expired',
          grace_remaining_seconds: 180,
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-wait-3009')).toHaveTextContent('grace 180s');
  });

  it('shows pending claim SLA in wait column', () => {
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, pending: 1 },
      devices: [
        {
          device_id: 10,
          device_serial: 'DEV-PEND',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3010,
          job_status: 'PENDING',
          ui_status: 'pending',
          current_stage: 'pending',
          current_step: null,
          patrol_cycle_count: 0,
          patrol_success_cycle_count: 0,
          patrol_failed_cycle_count: 0,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: null,
          created_at: new Date(Date.now() - 30_000).toISOString(),
          ended_at: null,
          pending_claim_remaining_seconds: 90,
        },
      ],
    };
    renderInTableView({ data });
    expect(screen.getByTestId('device-wait-3010')).toHaveTextContent('认领 90s');
  });

  it('shows pending claim SLA countdown in status tooltip', () => {
    const createdAt = new Date(Date.now() - 30_000).toISOString();
    const data: PlanRunDevicesPayload = {
      ...fixture,
      by_status: { all: 1, pending: 1 },
      devices: [
        {
          device_id: 10,
          device_serial: 'DEV-PEND',
          device_model: 'Pixel 8',
          host_id: 'host-101',
          job_id: 3010,
          job_status: 'PENDING',
          ui_status: 'pending',
          current_stage: 'pending',
          current_step: null,
          patrol_cycle_count: 0,
          patrol_success_cycle_count: 0,
          patrol_failed_cycle_count: 0,
          current_failure_streak: 0,
          next_retry_at: null,
          manual_action: null,
          log_signal_count: 0,
          last_heartbeat_at: null,
          started_at: null,
          created_at: createdAt,
          ended_at: null,
        },
      ],
    };
    renderInTableView({ data });
    const pill = screen.getByTestId('device-row-3010').querySelector('span[title]');
    expect(pill?.getAttribute('title')).toMatch(/90s 内未认领/);
    expect(pill?.getAttribute('title')).toMatch(/120s SLA/);
  });

  it('selects device on Enter key (keyboard access)', () => {
    const onSelect = vi.fn();
    renderInTableView({ onSelectDevice: onSelect });
    fireEvent.keyDown(screen.getByTestId('device-row-3002'), { key: 'Enter' });
    expect(onSelect).toHaveBeenCalledWith(
      expect.objectContaining({ job_id: 3002 }),
    );
  });

  it('respects controlled viewMode and emits onViewModeChange', () => {
    const onViewModeChange = vi.fn();
    const { rerender } = renderWithClient(
      <DeviceOverview
        data={fixture}
        viewMode="grid"
        onViewModeChange={onViewModeChange}
      />,
    );
    // controlled grid → minimap cells, no table rows
    expect(screen.getByTestId('minimap-cell-3001')).toBeInTheDocument();
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();
    // clicking table-btn emits change but does NOT self-switch (controlled)
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    expect(onViewModeChange).toHaveBeenCalledWith('table');
    expect(screen.queryByTestId('device-row-3001')).not.toBeInTheDocument();
    // parent flips the prop → table renders
    rerender(
      <DeviceOverview
        data={fixture}
        viewMode="table"
        onViewModeChange={onViewModeChange}
      />,
    );
    expect(screen.getByTestId('device-row-3001')).toBeInTheDocument();
  });
});

// ── 连接 / 执行 双维度 ────────────────────────────────────────────────────

/** 断连设备:link=offline、job 仍 RUNNING、UNKNOWN 且处于 grace 窗口内。 */
const disconnectedFixture: PlanRunDevicesPayload = {
  plan_run_id: 12,
  total: 1,
  by_status: { all: 1, unknown: 1 },
  by_link_status: { all: 1, offline: 1 },
  by_host: { 'host-101': 1 },
  devices: [
    {
      ...fixture.devices[0],
      job_id: 4001,
      device_serial: 'DEV-OFF',
      job_status: 'UNKNOWN',
      ui_status: 'unknown',
      job_exec_status: 'unknown',
      device_link_status: 'offline',
      grace_remaining_seconds: 180,
      status_reason: 'lease_expired',
    },
  ],
};

describe('DeviceOverview — 连接/执行 双维度', () => {
  it('tooltip 同时保留连接提示与 grace 倒计时，而非只显示连接提示', () => {
    const { container } = renderWithClient(
      <DeviceOverview data={disconnectedFixture} viewMode="table" />,
    );
    const tooltips = Array.from(container.querySelectorAll('[title]'))
      .map((el) => el.getAttribute('title') ?? '');
    // 早退版本会把 grace 倒计时整个吞掉 —— 断连设备恰恰最需要它
    const combined = tooltips.find((t) => t.includes('grace 剩余 180s'));
    expect(combined).toBeDefined();
    expect(combined).toContain('请检查 USB');
  });

  it('minimap 方块按执行维度着色，连接状态拼进 label', () => {
    renderWithClient(<DeviceOverview data={disconnectedFixture} />);
    const label = screen.getByTestId('minimap-cell-4001').getAttribute('aria-label') ?? '';
    expect(label).toContain('DEV-OFF');
    expect(label).toContain('已断开');   // 执行维度 unknown
    expect(label).toContain('离线');     // 连接维度 offline
  });

  it('渲染连接维度 chip 组并把选择回传父组件', () => {
    const onLink = vi.fn();
    renderWithClient(
      <DeviceOverview data={disconnectedFixture} onLinkFilterChange={onLink} />,
    );
    expect(screen.getByTestId('device-link-filter-offline')).toHaveTextContent('1');
    fireEvent.click(screen.getByTestId('device-link-filter-offline'));
    expect(onLink).toHaveBeenCalledWith('offline');
  });

  it('后端未返回 by_link_status 时不渲染连接 chip 组', () => {
    renderWithClient(<DeviceOverview data={fixture} onLinkFilterChange={vi.fn()} />);
    expect(screen.queryByTestId('device-link-filter-offline')).not.toBeInTheDocument();
    expect(screen.getByTestId('device-status-filter-running')).toBeInTheDocument();
  });
});

// ── #83：表格视图行虚拟化（minimap 保留全量）────────────────────────────────
//
// jsdom 没有布局引擎（`docs/development/testing.md` §4），所以这里**不声称**证明了
// 「滚动窗口在真机上落在哪」。被测的是**结构与语义**：过阈值才进虚拟层、进层后
// 表格体只渲染窗口内的行、facets 与 minimap 的全集语义不受影响。几何常量锁在
// `deviceTableVirtual.ts`，由 `tests/test_frontend_device_table_virtual_guard_83.py` 静态守卫。
vi.mock('@tanstack/react-virtual', () => {
  const ROW = 43;
  const VIEWPORT_ROWS = 12;
  return {
    useVirtualizer: ({ count, enabled }: { count: number; enabled?: boolean }) => {
      const n = enabled === false ? count : Math.min(count, VIEWPORT_ROWS);
      const items = Array.from({ length: n }, (_, i) => ({
        key: i,
        index: i,
        start: i * ROW,
        end: (i + 1) * ROW,
        size: ROW,
        lane: 0,
      }));
      return {
        getVirtualItems: () => items,
        getTotalSize: () => count * ROW,
        scrollToIndex: vi.fn(),
      };
    },
  };
});

function manyDevices(total: number): PlanRunDevicesPayload {
  const base = fixture.devices[0];
  return {
    plan_run_id: 12,
    total,
    by_status: { all: total, running: total },
    by_host: { 'host-101': total },
    devices: Array.from({ length: total }, (_, i) => ({
      ...base,
      device_id: i + 1,
      device_serial: `DEV-${String(i + 1).padStart(4, '0')}`,
      job_id: 9000 + i,
    })),
  };
}

describe('DeviceOverview 表格虚拟化（#83）', () => {
  function renderTableWithMany(total: number) {
    const big = manyDevices(total);
    const result = renderWithClient(<DeviceOverview data={big} />);
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    return result;
  }

  function renderedRowCount(container: HTMLElement): number {
    return container.querySelectorAll('[data-testid^="device-row-"]').length;
  }

  it('超过阈值才进虚拟层：4 行仍走静态表格、不挂滚动容器', () => {
    const { container } = renderWithClient(<DeviceOverview data={fixture} />);
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    expect(screen.queryByTestId('device-table-scroll')).not.toBeInTheDocument();
    expect(renderedRowCount(container)).toBe(4); // 静态路径逐行渲染，行为与改造前一致
  });

  it('500 行走虚拟层：只渲染窗口内的行，未渲染部分用两段空白垫片补回总高', () => {
    const { container } = renderTableWithMany(500);
    const scroller = screen.getByTestId('device-table-scroll');
    expect(scroller).toBeInTheDocument();
    expect(scroller.getAttribute('data-virtual')).toBe('true');
    expect(scroller.getAttribute('data-row-total')).toBe('500');
    expect(scroller.className).toMatch(/overflow-y-auto/);

    const rows = renderedRowCount(container);
    expect(rows).toBeGreaterThan(0);
    expect(rows).toBeLessThan(60); // 关键判据：DOM 行数与总数解耦（改造前 = 500）

    const spacers = Array.from(
      container.querySelectorAll<HTMLTableRowElement>('tbody > tr[aria-hidden="true"]'),
    );
    expect(spacers.length).toBeGreaterThan(0);
    // 底部垫片必须覆盖「未渲染的绝大部分行」——否则滚动条长度失真、滚到一半就到底
    const bottomPad = Math.max(
      ...spacers.map((el) => parseInt(el.style.height || '0', 10) || 0),
    );
    expect(bottomPad).toBeGreaterThan(400 * 43);
    expect(bottomPad).toBeLessThanOrEqual(500 * 43);
  });

  it('虚拟层不改 facets 口径：chip 仍显示全集总数（后端全集语义，前端不得二次统计）', () => {
    renderTableWithMany(500);
    expect(screen.getByTestId('device-status-filter-all')).toHaveTextContent('500');
    expect(screen.getByTestId('device-status-filter-running')).toHaveTextContent('500');
  });

  it('minimap 保留全量：方块阵每设备 1 节点，不跟着虚拟化（#83 的约束）', () => {
    const { container } = renderWithClient(<DeviceOverview data={manyDevices(500)} />);
    fireEvent.click(screen.getByTestId('device-overview-grid-btn'));
    expect(container.querySelectorAll('[data-testid^="minimap-cell-"]').length).toBe(500);
  });

  it('虚拟层里点行仍把设备回传给父组件（窗口内的行可点击）', () => {
    const onSelect = vi.fn();
    const big = manyDevices(500);
    renderWithClient(<DeviceOverview data={big} onSelectDevice={onSelect} />);
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    fireEvent.click(screen.getByTestId('device-row-9000'));
    expect(onSelect).toHaveBeenCalledWith(expect.objectContaining({ job_id: 9000 }));
  });

  // #83 sticky 修复：虚拟层必须只有一个 scrollport。Table 原语自带的 overflow-auto
  // 包裹层若仍是滚动容器，sticky 的最近滚动祖先落在它身上（高度=内容高，永不滚动），
  // 表头钉不住——见真浏览器几何验证（tests 守卫锁的是这里的 class 语义）。
  it('虚拟层把 Table 内层包裹降为 overflow-visible（sticky 绑到外层滚动视口）', () => {
    const { container } = renderTableWithMany(500);
    const wrapper = container.querySelector<HTMLElement>(
      '[data-slot="table-scroll-container"]',
    );
    expect(wrapper).not.toBeNull();
    expect(wrapper!.className).toMatch(/overflow-visible/);
    expect(wrapper!.className).not.toMatch(/overflow-auto/);
  });

  it('静态路径保持内层 overflow-auto（原生滚动行为不变）', () => {
    const { container } = renderWithClient(<DeviceOverview data={fixture} />);
    fireEvent.click(screen.getByTestId('device-overview-table-btn'));
    const wrapper = container.querySelector<HTMLElement>(
      '[data-slot="table-scroll-container"]',
    );
    expect(wrapper).not.toBeNull();
    expect(wrapper!.className).toMatch(/overflow-auto/);
    expect(wrapper!.className).not.toMatch(/overflow-visible/);
  });
});
