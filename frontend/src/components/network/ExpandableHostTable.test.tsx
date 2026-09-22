import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { ExpandableHostTable, type HostTableData } from './ExpandableHostTable';
import { BULK_BAR_SPACER_CLASS } from '@/components/ui/bulk-action-bar';

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
    expect(screen.getByTestId('host-table-selection-spacer'))
      .toHaveClass(...BULK_BAR_SPACER_CLASS.split(' '));

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

  describe('USB 设备数（lsusb 对照值）', () => {
    it('USB 数与在线数一致时中性展示', () => {
      render(<ExpandableHostTable hosts={[{ ...host, usb_device_count: 6 }]} />);

      const usb = screen.getByText('USB 6');
      expect(usb).toBeInTheDocument();
      expect(usb).not.toHaveClass('text-warning');
      expect(usb.getAttribute('title')).toContain('lsusb');
    });

    it('USB 枚举数大于 ADB 在线数时告警并解释差值', () => {
      render(<ExpandableHostTable hosts={[{ ...host, device_count: 6, usb_device_count: 8 }]} />);

      const usb = screen.getByText('USB 8');
      expect(usb).toHaveClass('text-warning');
      expect(usb.getAttribute('title')).toContain('USB 枚举 8 台 > ADB 在线 6 台');
      // 「在线」仍保持 adb 口径，不被 USB 值污染
      expect(screen.getByText('在线 6')).toBeInTheDocument();
    });

    it('未采集到 lsusb 数据时显示 — 而非 0', () => {
      render(<ExpandableHostTable hosts={[{ ...host, usb_device_count: null }]} />);

      const usb = screen.getByText('USB —');
      expect(usb).toBeInTheDocument();
      expect(screen.queryByText('USB 0')).not.toBeInTheDocument();
      expect(usb.getAttribute('title')).toContain('未采集到');
    });

    it('字段缺失（旧心跳数据）同样显示 —', () => {
      render(<ExpandableHostTable hosts={[host]} />);

      expect(screen.getByText('USB —')).toBeInTheDocument();
    });

    it('USB 为 0 是有效值，与「未知」区分', () => {
      render(<ExpandableHostTable hosts={[{ ...host, usb_device_count: 0 }]} />);

      expect(screen.getByText('USB 0')).toBeInTheDocument();
      expect(screen.queryByText('USB —')).not.toBeInTheDocument();
    });
  });

});

describe('ADR-0038 退役显示与入口（#1807）', () => {
  const retired: HostTableData = {
    ...host,
    status: 'OFFLINE',
    retired_at: '2026-09-13T00:00:00Z',
    retired_by: 'admin',
    retire_reason: '样机报废',
  };

  it('离线退役主机显示「已退役」徽标', () => {
    render(<ExpandableHostTable hosts={[retired]} />);

    const badge = screen.getByTestId(`host-retired-badge-${retired.id}`);
    expect(badge).toHaveTextContent('已退役');
    expect(badge).toHaveAttribute('title', expect.stringContaining('样机报废'));
  });

  it('退役但仍在心跳（ONLINE）显示异常徽标', () => {
    render(<ExpandableHostTable hosts={[{ ...retired, status: 'ONLINE' }]} />);

    expect(screen.getByTestId(`host-retired-badge-${retired.id}`)).toHaveTextContent(
      '已退役但仍在心跳',
    );
  });

  it('在用主机的 admin 下拉提供「退役」入口', async () => {
    const onRetire = vi.fn();
    const user = userEvent.setup();
    render(<ExpandableHostTable hosts={[host]} isAdmin onRetire={onRetire} />);

    await user.click(screen.getByRole('button', { name: `${host.name} 更多操作` }));
    await user.click(screen.getByText('退役'));

    expect(onRetire).toHaveBeenCalledWith(expect.objectContaining({ id: host.id }));
  });

  it('退役主机不提供热更新，下拉给出「解除退役」', async () => {
    const onUnretire = vi.fn();
    const onHotUpdate = vi.fn();
    const user = userEvent.setup();
    render(
      <ExpandableHostTable
        hosts={[{ ...retired, status: 'ONLINE' }]}
        isAdmin
        onHotUpdate={onHotUpdate}
        onUnretire={onUnretire}
      />,
    );

    expect(screen.queryByText('热更新')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: `${retired.name} 更多操作` }));
    await user.click(screen.getByText('解除退役'));

    expect(onUnretire).toHaveBeenCalledWith(expect.objectContaining({ id: retired.id }));
  });

  it('digest 判据：revision 不等不得渲染成 drift（ADR-0040 v1.1 判据唯一性）', () => {
    // 后端判据唯一 = code artifact digest：digest 相等即 `matched`，revision
    // （`VERSION` = 仓库 HEAD）只作溯源文本。若前端改成按 revision 判等，
    // 任何不动 backend/agent/** 的提交都会让全 fleet 出现**假 drift**
    // ——这正是 #2057 的根因，#2155 把口径钉在前端。
    render(
      <ExpandableHostTable
        hosts={[
          {
            ...host,
            agent_code_revision: 'abc1234',      // 主机上报的（旧）
            expected_code_revision: 'def5678',   // 期望（新 HEAD）
            agent_code_sync_status: 'matched',   // digest 已对齐
          },
        ]}
      />,
    );

    expect(screen.getByText('已对齐')).toBeInTheDocument();
    expect(screen.queryByText('内容漂移')).not.toBeInTheDocument();
  });

  it('#2366: 汇总单独报出「待热更新」台数（全队落后一版不得只读成 0/N）', () => {
    // 现场实测：观测时点的「Agent 已对齐 0/48」是**真实状态**（那批热更新之前），
    // 不是判据 bug——只给「已对齐 N/M」时这类读数极易被读成口径坏了，故把落后台数
    // 一并渲染。unknown（未上报）不计入「待热更新」：它的动作是「等一次心跳」。
    render(
      <ExpandableHostTable
        hosts={[
          { ...host, id: 1, agent_code_sync_status: 'matched' },
          { ...host, id: 2, agent_code_sync_status: 'drift' },
          { ...host, id: 3, agent_code_sync_status: 'unknown' },
        ]}
      />,
    );

    expect(screen.getByText(/Agent 已对齐 1\/3/)).toBeInTheDocument();
    expect(screen.getByText(/1 台待热更新/)).toBeInTheDocument();
  });

  it('digest 缺失（未上报）渲染为 unknown，而不是 drift', () => {
    // ADR-0040 v1.1：未上报 digest（#1907 前部署 / 新装未心跳）→ `unknown`，
    // 运维动作是「等一次心跳 / 首次 --force 迁移」，**不得**渲染成需更新。
    render(
      <ExpandableHostTable
        hosts={[{ ...host, agent_code_sync_status: 'unknown' }]}
      />,
    );

    expect(screen.getByText('未知')).toBeInTheDocument();
    expect(screen.queryByText('内容漂移')).not.toBeInTheDocument();
  });
});

describe('脚本在位（#2958 第五道闸）', () => {
  const summary = {
    counts: { present: 40, missing: 1, mismatch: 2, unknown: 3, n_a: 4, maintenance: 1 },
    hosts_total: 48,
    hosts_with_gap: 2,
    full_versions: 51,
    // #3111：账本不核验的 active 版本（无 Plan 引用）——与 full_versions 刻意不同值，
    // 免得断言两边写同一个数时把「字段接错」放过去。
    uncovered_active_versions: 46,
    checked_at_min: '2026-09-21T00:00:00Z',
    checked_at_max: '2026-09-21T06:00:00Z',
    stale: false,
  };

  const hostPresence = {
    host_id: String(host.id),
    checked_at: '2026-09-21T06:00:00Z',
    sweep_id: 'sweep-1',
    counts: { present: 1, missing: 1, mismatch: 0, unknown: 1, n_a: 3, maintenance: 0 },
    items: [
      { name: 'flash_preflight', version: '1.0.2', state: 'present' as const, detail: '' },
      { name: 'ensure_root', version: '0.3.0', state: 'unknown' as const, detail: 'agent 不可达' },
      {
        name: 'powercycle_setup',
        version: '0.2.0',
        state: 'missing' as const,
        detail: '文件缺失 /opt/stp/scripts/powercycle_setup/v0.2.0/run.sh',
      },
    ],
  };

  it('fleet 汇总行报缺口台数与未知/维护台数，明细面板给六态分解', () => {
    render(<ExpandableHostTable hosts={[host]} scriptPresenceSummary={summary} />);

    const bar = screen.getByTestId('script-presence-summary');
    expect(within(bar).getByText(/缺口 2 台/)).toBeInTheDocument();
    expect(within(bar).getByText(/^未知 3 台$/)).toBeInTheDocument();
    expect(within(bar).getByText(/^维护窗 1 台$/)).toBeInTheDocument();
    // 非陈旧时不得出现陈旧提示，缺口按红读
    expect(screen.queryByTestId('script-presence-stale')).not.toBeInTheDocument();
    expect(within(bar).getByText(/缺口 2 台/).className).toContain('text-destructive');

    fireEvent.click(within(bar).getByRole('button', { name: '展开明细' }));
    expect(within(bar).getByText('内容不符 2')).toBeInTheDocument();
    expect(within(bar).getByText('缺失 1')).toBeInTheDocument();
    expect(within(bar).getByText('不适用 4')).toBeInTheDocument();
    expect(within(bar).getByText('目标版本 51')).toBeInTheDocument();
    // #3111：未覆盖的 active 版本数必须在明细里，且与目标版本分开读
    expect(within(bar).getByText(/^账本未覆盖 46$/)).toBeInTheDocument();
    // 汇总没有逐台名单，文案要指路到展开行
    expect(within(bar).getByText(/不含逐台名单/)).toBeInTheDocument();
  });

  it('stale=true 时汇总读作陈旧（不当绿读）', () => {
    render(
      <ExpandableHostTable hosts={[host]} scriptPresenceSummary={{ ...summary, stale: true }} />,
    );

    expect(screen.getByTestId('script-presence-stale')).toHaveTextContent('账本陈旧');
    const gap = screen.getByText(/缺口 2 台/);
    expect(gap.className).not.toContain('text-success');
    expect(gap.className).not.toContain('text-destructive');
    expect(gap.className).toContain('text-muted-foreground');
  });

  it('展开主机行才拉单机矩阵，缺口在前、未知不读成绿', async () => {
    const onLoad = vi.fn().mockResolvedValue(hostPresence);
    render(<ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />);

    expect(onLoad).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(host.name));

    await screen.findByText('powercycle_setup@0.2.0');
    expect(onLoad).toHaveBeenCalledWith(host.id);

    const block = screen.getByTestId(`host-script-presence-${host.id}`);
    const rows = within(block).getAllByRole('listitem').map((li) => li.textContent ?? '');
    expect(rows).toHaveLength(3);
    expect(rows[0]).toContain('powercycle_setup@0.2.0');
    expect(rows[0]).toContain('缺失');
    expect(rows[1]).toContain('ensure_root@0.3.0');
    expect(rows[2]).toContain('flash_preflight@1.0.2');
    expect(rows[0]).toContain('文件缺失 /opt/stp/scripts/powercycle_setup/v0.2.0/run.sh');
    expect(within(block).getByText('缺失').className).toContain('text-destructive');
    expect(within(block).getByText('未知').className).toContain('text-muted-foreground');
    expect(within(block).getByText('在位').className).toContain('text-success');
  });

  it('「重新核验」调 refresh 并重拉该机矩阵', async () => {
    const onLoad = vi.fn().mockResolvedValue(hostPresence);
    const onRefresh = vi.fn().mockResolvedValue({});
    render(
      <ExpandableHostTable
        hosts={[host]}
        isAdmin
        onLoadHostScriptPresence={onLoad}
        onRefreshHostScriptPresence={onRefresh}
      />,
    );

    fireEvent.click(screen.getByText(host.name));
    await screen.findByText('powercycle_setup@0.2.0');

    fireEvent.click(screen.getByRole('button', { name: `${host.name} 重新核验脚本在位` }));
    await waitFor(() => expect(onRefresh).toHaveBeenCalledWith(host.id));
    await waitFor(() => expect(onLoad).toHaveBeenCalledTimes(2));
  });

  it('非管理员不渲染「重新核验」（#3091：该动作触发 require_admin 写端点）', async () => {
    const onLoad = vi.fn().mockResolvedValue(hostPresence);
    const onRefresh = vi.fn().mockResolvedValue({});
    render(
      <ExpandableHostTable
        hosts={[host]}
        onLoadHostScriptPresence={onLoad}
        onRefreshHostScriptPresence={onRefresh}
      />,
    );

    fireEvent.click(screen.getByText(host.name));
    await screen.findByText('powercycle_setup@0.2.0');   // 区块本身仍可读

    expect(
      screen.queryByRole('button', { name: `${host.name} 重新核验脚本在位` }),
    ).toBeNull();
  });

  it('stale=true 时逐台区块顶部给陈旧提示', async () => {
    const onLoad = vi.fn().mockResolvedValue(hostPresence);
    render(
      <ExpandableHostTable
        hosts={[host]}
        scriptPresenceSummary={{ ...summary, stale: true }}
        onLoadHostScriptPresence={onLoad}
      />,
    );

    fireEvent.click(screen.getByText(host.name));

    const banner = await screen.findByTestId(`host-script-presence-stale-${host.id}`);
    expect(banner).toHaveTextContent('账本陈旧');
    expect(banner).toHaveTextContent('当前状态可能已过期');
  });

  it('加载失败显示错误与重试入口（不渲染成空矩阵）', async () => {
    const onLoad = vi.fn().mockRejectedValue(new Error('网络不可达'));
    render(<ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />);

    fireEvent.click(screen.getByText(host.name));

    expect(await screen.findByText(/核验数据加载失败：网络不可达/)).toBeInTheDocument();
    expect(screen.queryByText(/暂无条目/)).not.toBeInTheDocument();
  });

  it('未提供逐台 fetcher 时不出现「脚本在位」区块（既有调用方零回归）', () => {
    render(<ExpandableHostTable hosts={[host]} />);

    fireEvent.click(screen.getByText(host.name));

    expect(screen.getByText('Agent 版本')).toBeInTheDocument();
    expect(screen.queryByText('脚本在位')).not.toBeInTheDocument();
  });
});
