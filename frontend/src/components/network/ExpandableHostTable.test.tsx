import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
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

  it('#3550 explains the digest-based alignment when expected revision diverges from reported', () => {
    const diverged: HostTableData = { ...host, expected_code_revision: 'def5678' };
    render(<ExpandableHostTable hosts={[diverged]} />);

    fireEvent.click(screen.getByText(diverged.name));

    // 常显判据行：把「对齐以摘要为准、修订仅溯源」说在明处
    expect(screen.getByText(/对齐以「部署摘要」判定/)).toBeInTheDocument();
    // 条件化归因只挂在展开面板的徽章上（收起行徽章已有自己的 digest 提示）
    const card = screen.getByText('Agent 版本').closest('.bg-card') as HTMLElement;
    const badge = within(card).getByText('已对齐');
    expect(badge.getAttribute('title')).toContain('载荷摘要未变');
    expect(badge.getAttribute('title')).toContain('期望修订随每次合入前进');
  });

  it('#3550 omits the divergence hint when revisions agree', () => {
    render(<ExpandableHostTable hosts={[host]} />);

    fireEvent.click(screen.getByText(host.name));

    const card = screen.getByText('Agent 版本').closest('.bg-card') as HTMLElement;
    const badge = within(card).getByText('已对齐');
    expect(badge.getAttribute('title')).toBeNull();
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

describe('ADR-0038 v0.3 D9 设备面意图徽标（#3159）', () => {
  const emptied: HostTableData = {
    ...host,
    status: 'OFFLINE',
    emptied_at: '2026-09-26T00:00:00Z',
    emptied_by: 'admin',
    emptied_reason: '机柜撤线，设备已移机',
  };

  it('置位主机显示「设备面已处置」徽标，悬浮带 who/reason 与豁免说明', () => {
    render(<ExpandableHostTable hosts={[emptied]} />);

    const badge = screen.getByTestId(`host-emptied-badge-${emptied.id}`);
    expect(badge).toHaveTextContent('设备面已处置');
    expect(badge).toHaveAttribute('title', expect.stringContaining('（admin）'));
    expect(badge).toHaveAttribute('title', expect.stringContaining('设备已移机'));
    expect(badge).toHaveAttribute('title', expect.stringContaining('ADR-0038 D9'));
  });

  it('未置位主机不显示徽标', () => {
    render(<ExpandableHostTable hosts={[host]} />);

    expect(screen.queryByTestId(`host-emptied-badge-${host.id}`)).not.toBeInTheDocument();
  });
});

describe('脚本在位（#2958 第五道闸 · 抽屉按需加载）', () => {
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

  it('打开详情抽屉才拉单机矩阵，缺口在前、未知不读成绿', async () => {
    const onLoad = vi.fn().mockResolvedValue(hostPresence);
    render(
      <MemoryRouter>
        <ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />
      </MemoryRouter>,
    );

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
    // 链往脚本库
    expect(within(block).getByRole('link', { name: /脚本库/ })).toHaveAttribute('href', '/script-management');
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
    await screen.findByText('powercycle_setup@0.2.0');

    expect(
      screen.queryByRole('button', { name: `${host.name} 重新核验脚本在位` }),
    ).toBeNull();
  });

  it('加载失败显示错误与重试入口（不渲染成空矩阵）', async () => {
    const onLoad = vi.fn().mockRejectedValue(new Error('网络不可达'));
    render(<ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />);

    fireEvent.click(screen.getByText(host.name));

    expect(await screen.findByText(/核验数据加载失败：网络不可达/)).toBeInTheDocument();
    expect(screen.queryByText(/暂无条目/)).not.toBeInTheDocument();
  });

  it('逐台空态：全部 n_a 显示 n_a 项数，不误报「尚未跑过 sweep」（#3497 B2-G1）', async () => {
    const onLoad = vi.fn().mockResolvedValue({
      ...hostPresence,
      counts: { present: 0, missing: 0, mismatch: 0, unknown: 0, n_a: 5, maintenance: 0 },
      items: [],
    });
    render(<ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />);

    fireEvent.click(screen.getByText(host.name));

    const block = await screen.findByTestId(`host-script-presence-${host.id}`);
    expect(within(block).getByText(/全部 5 项均「不适用」/)).toBeInTheDocument();
    expect(within(block).queryByText(/尚未跑过 sweep/)).not.toBeInTheDocument();
    expect(within(block).queryByText(/可点「重新核验」/)).not.toBeInTheDocument();
  });

  it('逐台空态：本轮无行（未 sweep）保持「尚未跑过 sweep」原文案', async () => {
    const onLoad = vi.fn().mockResolvedValue({
      ...hostPresence,
      counts: { present: 0, missing: 0, mismatch: 0, unknown: 0, n_a: 0, maintenance: 0 },
      items: [],
    });
    render(<ExpandableHostTable hosts={[host]} onLoadHostScriptPresence={onLoad} />);

    fireEvent.click(screen.getByText(host.name));

    const block = await screen.findByTestId(`host-script-presence-${host.id}`);
    expect(within(block).getByText(/尚未跑过 sweep，可点「重新核验」/)).toBeInTheDocument();
  });

  it('未提供逐台 fetcher 时不出现「脚本在位」区块（既有调用方零回归）', () => {
    render(<ExpandableHostTable hosts={[host]} />);

    fireEvent.click(screen.getByText(host.name));

    expect(screen.getByText('Agent 版本')).toBeInTheDocument();
    expect(screen.queryByText('脚本在位')).not.toBeInTheDocument();
  });
});

describe('U3-1 标杆治理：健康异常快捷视图、抽屉挂载呈现与键盘无障碍', () => {
  it('「健康异常」快捷视图同时命中 DEGRADED 与 UNSCHEDULABLE（不漏禁调主机）', () => {
    const healthy: HostTableData = { ...host, id: 10, name: 'Host-OK', health_status: 'HEALTHY' };
    const degraded: HostTableData = { ...host, id: 11, name: 'Host-Deg', health_status: 'DEGRADED' };
    const unschedulable: HostTableData = {
      ...host,
      id: 12,
      name: 'Host-Block',
      health_status: 'UNSCHEDULABLE',
    };
    render(<ExpandableHostTable hosts={[healthy, degraded, unschedulable]} />);

    const quickBtn = screen.getByRole('button', { name: /健康异常/ });
    expect(quickBtn).toHaveTextContent('2');
    fireEvent.click(quickBtn);

    expect(screen.queryByText('Host-OK')).not.toBeInTheDocument();
    expect(screen.getByText('Host-Deg')).toBeInTheDocument();
    expect(screen.getByText('Host-Block')).toBeInTheDocument();
  });

  it('详情抽屉呈现 mount_status 并链往 /storage，支持键盘 Enter 打开与 Esc 关闭归位', async () => {
    const user = userEvent.setup();
    const mountedHost: HostTableData = {
      ...host,
      id: 20,
      name: 'Host-Mount-Test',
      mount_status: [
        { path: '/mnt/aee/central/long-storage-share', mounted: false },
        { path: '/var/log/stp', mounted: true, available_gb: 120, total_gb: 500 },
      ],
    };
    render(
      <MemoryRouter>
        <ExpandableHostTable hosts={[mountedHost]} />
      </MemoryRouter>,
    );

    const row = screen.getByRole('button', { name: /查看主机 Host-Mount-Test 详情/ });
    row.focus();
    await user.keyboard('{Enter}');

    const drawer = await screen.findByTestId('fleet-host-drawer');
    const mountSection = within(drawer).getByTestId('host-mount-status-20');
    expect(within(mountSection).getByText('/mnt/aee/central/long-storage-share')).toBeInTheDocument();
    expect(within(mountSection).getByText('未挂载')).toBeInTheDocument();
    expect(within(mountSection).getByRole('link', { name: /前往文件服务器/ })).toHaveAttribute('href', '/storage');

    await user.keyboard('{Escape}');
    await waitFor(() => {
      expect(screen.queryByTestId('fleet-host-drawer')).not.toBeInTheDocument();
    });
    await waitFor(() => {
      expect(document.activeElement).toBe(row);
    });
  });
});

describe('S2：USB / Hub 故障工作台（快捷视图、主行外显、异常置顶与展开诊断）', () => {
  const healthyHost: HostTableData = {
    ...host,
    id: 'host-ok',
    name: 'Host-Healthy',
    ip: '10.0.0.10',
    health_status: 'HEALTHY',
    health_reasons: [],
  };

  const degradedHubHost: HostTableData = {
    ...host,
    id: 'host-hub-bad',
    name: 'Host-Hub-Bad',
    ip: '10.0.0.12',
    health_status: 'DEGRADED',
    health_reasons: ['usb_link_degraded'],
    usb_device_count: 16,
    adb_interface_count: 16,
    usb_kernel_log: 'ok',
    usb_hub_topology: { cascade_hubs: 5, unbound_hubs: 1, unbound_idle_bus_hubs: 1 },
    usb_kernel_errors_1h: {
      link_errors: 73,
      cable_suspect: 0,
      disconnects: 2,
      resets: 0,
      enum_errors: 0,
      hub_port_errors: 68,
    },
    health_probe: {
      verdict: 'agent_mute',
      topology: 'hub_present_phones_zero',
      strike_open: true,
    },
  };

  const emptiedEmptyTreeHost: HostTableData = {
    ...host,
    id: 'host-emptied',
    name: 'Host-Emptied',
    ip: '10.0.0.91',
    health_status: 'DEGRADED',
    health_reasons: ['usb_tree_empty'],
    emptied_at: '2026-11-23T00:00:00Z',
    emptied_by: 'admin',
  };

  it('USB / Hub 异常快捷视图支持互斥过滤与往返切回全部主机，并排除已处置空树主机', () => {
    render(
      <ExpandableHostTable
        hosts={[healthyHost, degradedHubHost, emptiedEmptyTreeHost]}
      />,
    );

    // 异常主机应自动置顶到健康主机之前
    const rows = screen.getAllByRole('button', { name: /^查看主机 / });
    expect(rows[0]).toHaveTextContent('Host-Hub-Bad');

    // 主行直接外显故障芯片，无需 hover tooltip
    const inlineFaults = screen.getByTestId('host-inline-faults-host-hub-bad');
    expect(inlineFaults).toHaveTextContent('USB 链路劣化');
    expect(inlineFaults).toHaveTextContent('SS Hub 半枚举(1)');

    // 切换到「USB / Hub 异常」：仅保留 degradedHubHost（emptied 空树不算异常）
    fireEvent.click(screen.getByText(/USB \/ Hub 异常/));
    expect(screen.getByText('Host-Hub-Bad')).toBeInTheDocument();
    expect(screen.queryByText('Host-Healthy')).not.toBeInTheDocument();
    expect(screen.queryByText('Host-Emptied')).not.toBeInTheDocument();

    // 往返切回「全部主机」
    fireEvent.click(screen.getByText(/全部主机/));
    expect(screen.getByText('Host-Healthy')).toBeInTheDocument();
    expect(screen.getByText('Host-Emptied')).toBeInTheDocument();
  });

  it('展开行展示 USB / Hub 与对账诊断卡片，并支持管理员触发设备面已处置操作', () => {
    const onSetDeviceIntent = vi.fn();
    render(
      <ExpandableHostTable
        hosts={[degradedHubHost]}
        isAdmin
        onSetDeviceIntent={onSetDeviceIntent}
      />,
    );

    fireEvent.click(screen.getByText('Host-Hub-Bad'));

    const card = screen.getByTestId('host-usb-diagnostics-host-hub-bad');
    expect(within(card).getByText('USB / Hub 与对账诊断')).toBeInTheDocument();
    expect(within(card).getByText('5 / 1')).toBeInTheDocument();
    expect(within(card).getByText(/-71\/-110 传输错: 73/)).toBeInTheDocument();
    expect(within(card).getByText(/Hub 端口错: 68/)).toBeInTheDocument();
    expect(within(card).getByText(/级联 Hub 在树但无终端/)).toBeInTheDocument();
    expect(within(card).getByText('已触发（需核查）')).toBeInTheDocument();

    fireEvent.click(screen.getByTestId('host-set-emptied-host-hub-bad'));
    expect(onSetDeviceIntent).toHaveBeenCalledWith(degradedHubHost);
  });

  it('自愈熔断主机自动置顶、主行外显熔断芯片并在诊断卡展示熔断态与计数', () => {
    const fuseHost: HostTableData = {
      ...host,
      id: 'host-fuse-88',
      name: 'Host-Fuse-88',
      ip: '10.0.0.88',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      self_heal: {
        state: 'hardware_suspect',
        fuse_open: true,
        fuse_reason: 'fuse_probe_failed',
        xhci: {
          state: 'hardware_suspect',
          fuse_open: true,
          fuse_reason: 'fuse_probe_failed',
          boot_attempts: 1,
          window_attempts: 1,
          episode_failures: 0,
          last_marker: 'STP_XHCI_REBIND_PROBE_FAILED',
        },
      },
    };
    render(<ExpandableHostTable hosts={[healthyHost, fuseHost]} />);

    const rows = screen.getAllByRole('button', { name: /^查看主机 / });
    expect(rows[0]).toHaveTextContent('Host-Fuse-88');
    expect(screen.getByTestId('host-inline-faults-host-fuse-88')).toHaveTextContent('自愈熔断(疑似硬件)');

    fireEvent.click(screen.getByText('Host-Fuse-88'));
    const card = screen.getByTestId('host-usb-diagnostics-host-fuse-88');
    expect(within(card).getByText(/疑似硬件 \(fuse_probe_failed\)/)).toBeInTheDocument();
    expect(within(card).getByText(/1 \/ 1 · STP_XHCI_REBIND_PROBE_FAILED/)).toBeInTheDocument();
  });

  it('状态与故障标签（3 个及以上，如在线/降级/USB 空树）保持同一不换行行内排列，避免撑高行高', () => {
    const multiBadgeHost: HostTableData = {
      id: 'host-empty-tree',
      name: 'Host-Empty-Tree',
      ip: '10.0.15.99',
      status: 'ONLINE',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      watcher_admin_active: true,
    };
    render(<ExpandableHostTable hosts={[multiBadgeHost]} />);

    const statusLine = screen.getByTestId('host-status-line-host-empty-tree');
    expect(statusLine).toHaveClass('flex-nowrap', 'whitespace-nowrap');
    expect(statusLine).not.toHaveClass('flex-wrap');
    expect(within(statusLine).getByText('在线')).toBeInTheDocument();
    expect(within(statusLine).getByText('降级')).toBeInTheDocument();
    expect(within(statusLine).getByText('USB 空树')).toBeInTheDocument();
  });

  it('零台账纯空柜不计入 USB / Hub 异常并显示「未接设备」，而硬件端口禁用与卡死端口会置顶外显', () => {
    const pureEmptyCabinet: HostTableData = {
      ...host,
      id: 'host-empty-zero',
      name: 'Host-Zero-Ledger',
      ip: '10.0.15.1',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      ledger_device_count: 0,
      usb_controller_health: {
        unbound_xhci_controllers: 0,
        disabled_root_ports: 0,
        disabled_hub_ports: 0,
        over_current_ports: 0,
        stuck_default_ports: 0,
      },
    };
    const rootDisabledHost: HostTableData = {
      ...host,
      id: 'host-root-disabled',
      name: 'Host-Root-Disabled-73',
      ip: '10.0.15.73',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      ledger_device_count: 0,
      usb_controller_health: {
        unbound_xhci_controllers: 0,
        disabled_root_ports: 16,
        disabled_hub_ports: 0,
        over_current_ports: 0,
        stuck_default_ports: 0,
      },
    };
    const stuckPortHost: HostTableData = {
      ...host,
      id: 'host-stuck-57',
      name: 'Host-Stuck-57',
      ip: '10.0.15.57',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      ledger_device_count: 0,
      usb_controller_health: {
        unbound_xhci_controllers: 0,
        disabled_root_ports: 0,
        disabled_hub_ports: 0,
        over_current_ports: 0,
        stuck_default_ports: 1,
      },
    };

    render(
      <ExpandableHostTable
        hosts={[pureEmptyCabinet, stuckPortHost, rootDisabledHost]}
      />,
    );

    const rows = screen.getAllByRole('button', { name: /^查看主机 / });
    expect(rows[0]).toHaveTextContent('Host-Root-Disabled-73');
    expect(rows[1]).toHaveTextContent('Host-Stuck-57');
    expect(rows[2]).toHaveTextContent('Host-Zero-Ledger');

    expect(screen.getByTestId('host-inline-faults-host-empty-zero')).toHaveTextContent('未接设备');
    expect(screen.getByTestId('host-inline-faults-host-root-disabled')).toHaveTextContent(
      '主控端口禁用(16)',
    );
    expect(screen.getByTestId('host-inline-faults-host-stuck-57')).toHaveTextContent('端口卡死(1)');

    // 切到「USB / Hub 异常」：排除纯空柜 Host-Zero-Ledger，保留真硬件异常主机
    fireEvent.click(screen.getByText(/USB \/ Hub 异常/));
    expect(screen.getByText('Host-Root-Disabled-73')).toBeInTheDocument();
    expect(screen.getByText('Host-Stuck-57')).toBeInTheDocument();
    expect(screen.queryByText('Host-Zero-Ledger')).not.toBeInTheDocument();

    // 打开详情抽屉展示「台账在册设备」与「主控与端口体检（sysfs）」
    fireEvent.click(screen.getByText('Host-Root-Disabled-73'));
    const card = screen.getByTestId('host-usb-diagnostics-host-root-disabled');
    expect(within(card).getByText('台账在册设备')).toBeInTheDocument();
    expect(within(card).getByText('主控与端口体检（sysfs）')).toBeInTheDocument();
    expect(within(card).getByText('根口禁用: 16')).toBeInTheDocument();
  });

  it('按 72 小时活跃窗口消噪长期空置主机，并在主行与抽屉独立展示 uhubctl VBUS 断电端口', () => {
    const staleSeen = new Date(Date.now() - 5 * 24 * 60 * 60 * 1000).toISOString();
    const recentSeen = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();

    const staleEmptyHost: HostTableData = {
      ...host,
      id: 'host-stale-empty',
      name: 'Host-Stale-Empty',
      ip: '10.0.15.20',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      ledger_device_count: 4,
      ledger_latest_seen_at: staleSeen,
      usb_controller_health: {
        unbound_xhci_controllers: 0,
        disabled_root_ports: 0,
        disabled_hub_ports: 0,
        over_current_ports: 0,
        stuck_default_ports: 0,
      },
    };
    const recentEmptyHost: HostTableData = {
      ...host,
      id: 'host-recent-empty',
      name: 'Host-Recent-Empty',
      ip: '10.0.15.43',
      health_status: 'DEGRADED',
      health_reasons: ['usb_tree_empty'],
      device_count: 0,
      usb_device_count: 0,
      ledger_device_count: 6,
      ledger_latest_seen_at: recentSeen,
    };
    const vbusOffHost: HostTableData = {
      ...host,
      id: 'host-vbus-off',
      name: 'Host-Vbus-Off',
      ip: '10.0.15.66',
      health_status: 'HEALTHY',
      health_reasons: [],
      device_count: 2,
      usb_device_count: 2,
      usb_power_slot_serials: {
        '1-5.1:2': 'SER-VBUS-02',
      },
      health_probe: {
        verdict: 'aligned',
        topology: 'ok',
        strike_open: false,
        uhubctl_status: 'ok',
        powered_off_ports: ['1-5.1:2', '1-5.1:4'],
      },
    };

    render(
      <ExpandableHostTable
        hosts={[staleEmptyHost, vbusOffHost, recentEmptyHost]}
      />,
    );

    const rows = screen.getAllByRole('button', { name: /^查看主机 / });
    expect(rows[0]).toHaveTextContent('Host-Recent-Empty');
    expect(rows[1]).toHaveTextContent('Host-Vbus-Off');
    expect(rows[2]).toHaveTextContent('Host-Stale-Empty');

    expect(screen.getByTestId('host-inline-faults-host-stale-empty')).toHaveTextContent('长期空置');
    expect(screen.getByTestId('host-inline-faults-host-recent-empty')).toHaveTextContent('USB 空树');
    expect(screen.getByTestId('host-inline-faults-host-vbus-off')).toHaveTextContent('VBUS 断电(2)');

    // 切到「USB / Hub 异常」：排除 >72h 长期空置主机，保留近期掉光(P2)与 VBUS 断电(P1)
    fireEvent.click(screen.getByText(/USB \/ Hub 异常/));
    expect(screen.getByText('Host-Recent-Empty')).toBeInTheDocument();
    expect(screen.getByText('Host-Vbus-Off')).toBeInTheDocument();
    expect(screen.queryByText('Host-Stale-Empty')).not.toBeInTheDocument();

    // 打开 VBUS 断电主机抽屉，确认列出具体 hub:port 与映射串号
    fireEvent.click(screen.getByText('Host-Vbus-Off'));
    const card = screen.getByTestId('host-usb-diagnostics-host-vbus-off');
    expect(within(card).getByText('VBUS 供电（uhubctl）')).toBeInTheDocument();
    expect(within(card).getByText('2 口断电（0000 off）')).toBeInTheDocument();
    expect(within(card).getByText('1-5.1:2 (SER-VBUS-02)')).toBeInTheDocument();
    expect(within(card).getByText('1-5.1:4')).toBeInTheDocument();
  });
});

describe('B3-2 服务端分页模式（serverMode + 10 项 Facets + upgrade_pending）', () => {
  it('消费独立 10 项 serverFacets、渲染分页条、委托排序且「待热更新」触发 upgrade_pending', () => {
    const onSyncFilterChange = vi.fn();
    const onPageChange = vi.fn();
    const onPageSizeChange = vi.fn();
    const onSortChange = vi.fn();
    render(
      <ExpandableHostTable
        hosts={[host]}
        serverMode
        serverFacets={{
          all: 150,
          online: 120,
          degraded: 10,
          offline: 20,
          usb_anomaly: 4,
          upgrade_pending: 9,
          health_anomaly: 7,
          mount_anomaly: 2,
          aligned_agent_count: 111,
          trackable_agent_count: 120,
        }}
        serverFilteredTotal={150}
        page={1}
        pageSize={50}
        onSyncFilterChange={onSyncFilterChange}
        onPageChange={onPageChange}
        onPageSizeChange={onPageSizeChange}
        onSortChange={onSortChange}
      />,
    );

    expect(screen.getByTestId('host-agent-alignment-summary')).toHaveTextContent(
      'Agent 已对齐 111/120 · 9 台待热更新',
    );
    expect(screen.getByTestId('hosts-pagination-bar')).toHaveTextContent('第 1–1 / 共 150 台');
    expect(screen.getByTestId('hosts-page-indicator')).toHaveTextContent('1 / 3');

    fireEvent.click(screen.getByRole('button', { name: /待热更新/ }));
    expect(onSyncFilterChange).toHaveBeenCalledWith('upgrade_pending');

    fireEvent.click(screen.getByTestId('hosts-next-page'));
    expect(onPageChange).toHaveBeenCalledWith(2);

    fireEvent.click(screen.getByTestId('hosts-page-size-100'));
    expect(onPageSizeChange).toHaveBeenCalledWith(100);

    fireEvent.click(screen.getByRole('button', { name: '主机' }));
    expect(onSortChange).toHaveBeenCalledWith('name', 'asc');
  });

  it('通过 drawerHostOverride 独立打开不在当前页的主机详情抽屉', () => {
    const offPageHost: HostTableData = {
      ...host,
      id: 'host-999',
      name: '跨页深链主机-999',
      ip: '10.9.9.9',
    };
    render(
      <MemoryRouter>
        <ExpandableHostTable
          hosts={[host]}
          serverMode
          activeDrawerHostId="host-999"
          drawerHostOverride={offPageHost}
        />
      </MemoryRouter>,
    );

    const drawer = screen.getByTestId('fleet-host-drawer');
    expect(within(drawer).getByText('跨页深链主机-999')).toBeInTheDocument();
  });

  it('表格滚动区采用弹性锁高且分页底栏固定在卡片底部（无 calc(100vh-...)）', () => {
    const { container } = render(
      <ExpandableHostTable
        hosts={[host]}
        serverMode
        serverFilteredTotal={78}
        page={1}
        pageSize={50}
        onPageChange={vi.fn()}
        onPageSizeChange={vi.fn()}
      />,
    );

    const scrollBox = container.querySelector('[data-slot="table-scroll-container"]');
    expect(scrollBox).not.toBeNull();
    expect(scrollBox?.className).toContain('flex-1');
    expect(scrollBox?.className).toContain('min-h-0');
    expect(scrollBox?.className).not.toContain('calc(100vh');

    const paginationBar = screen.getByTestId('hosts-pagination-bar');
    expect(paginationBar.className).toContain('shrink-0');
    expect(paginationBar.parentElement).toBe(scrollBox?.parentElement);
  });
});
