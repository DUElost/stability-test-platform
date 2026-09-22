import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExpandableDeviceTable } from './ExpandableDeviceTable';
import { BULK_BAR_SPACER_CLASS } from '@/components/ui/bulk-action-bar';

const devices = [
  {
    id: 1,
    serial: 'SERIAL-1',
    model: 'Model A',
    status: 'idle' as const,
    build_display_id: 'build-a',
    host_name: '203.0.113.36',
    last_seen: '2026-05-09T18:00:00+08:00',
    battery_level: 75,
    temperature: 42.5,
    network_latency: 88,
    tags: ['shanghai', 'android15', 'regression'],
  },
];

describe('ExpandableDeviceTable', () => {
  it('renders a named search textbox', () => {
    render(<ExpandableDeviceTable devices={devices} />);
    expect(screen.getByRole('textbox', { name: '搜索设备' })).toHaveAttribute('name', 'device-search');
  });

  it('does not emit React key warnings for paginated rows', () => {
    const errorSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(<ExpandableDeviceTable devices={devices} />);
    const joined = errorSpy.mock.calls.map(call => call.join(' ')).join('\n');
    expect(joined).not.toContain('unique "key" prop');
    errorSpy.mockRestore();
  });

  it('supports current-page selection and selected-row feedback', () => {
    const onSelectionChange = vi.fn();
    const twoDevices = [
      ...devices,
      { ...devices[0], id: 2, serial: 'SERIAL-2', model: 'Model B' },
    ];
    render(
      <ExpandableDeviceTable
        devices={twoDevices}
        selectedIds={new Set([1])}
        onSelectionChange={onSelectionChange}
      />,
    );

    // #2600：可访问名必须写明作用域与页大小——「全选」在不同页面语义不同
    const selectPage = screen.getByRole('checkbox', {
      name: /选择当前页设备（本页 \d+ 台）/,
    }) as HTMLInputElement;
    expect(selectPage.indeterminate).toBe(true);
    expect(screen.getByRole('checkbox', { name: '选择设备 SERIAL-1' }).closest('tr')).toHaveAttribute(
      'data-state',
      'selected',
    );

    fireEvent.click(screen.getByRole('checkbox', { name: '选择设备 SERIAL-2' }));
    expect(onSelectionChange).toHaveBeenCalledWith(new Set([1, 2]));
  });

  it('reports filtered devices and uses responsive columns', async () => {
    const onFilteredDevicesChange = vi.fn();
    render(
      <ExpandableDeviceTable
        devices={devices}
        onFilteredDevicesChange={onFilteredDevicesChange}
      />,
    );

    await waitFor(() => expect(onFilteredDevicesChange).toHaveBeenCalledWith(devices));
    expect(screen.getByRole('columnheader', { name: '设备' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '版本' })).not.toHaveClass('hidden');
    expect(screen.getByRole('columnheader', { name: '最后活跃' })).not.toHaveClass('hidden');
    expect(screen.getByRole('columnheader', { name: '电量' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '温度' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '网络' })).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '标签' })).toBeInTheDocument();
    expect(screen.getByText('75%')).toBeInTheDocument();
    expect(screen.getByText('42.5°C')).toBeInTheDocument();
    expect(screen.getByText('88ms')).toBeInTheDocument();
    expect(screen.getByText('shanghai')).toBeInTheDocument();
    expect(screen.getByText('+1')).toBeInTheDocument();
    expect(screen.getByRole('table')).toHaveClass('min-w-[1420px]');
  });

  it('clamps to the last valid page when the filtered list shrinks', () => {
    const manyDevices = Array.from({ length: 51 }, (_, index) => ({
      ...devices[0],
      id: index + 1,
      serial: `SERIAL-${index + 1}`,
    }));
    const { rerender } = render(<ExpandableDeviceTable devices={manyDevices} />);

    fireEvent.click(screen.getByRole('button', { name: '下一页' }));
    expect(screen.getByText('SERIAL-51')).toBeInTheDocument();

    rerender(<ExpandableDeviceTable devices={[manyDevices[0]]} />);

    expect(screen.getByText('SERIAL-1')).toBeInTheDocument();
    expect(screen.queryByText('SERIAL-51')).not.toBeInTheDocument();
    expect(screen.queryByText('51 / 2')).not.toBeInTheDocument();
  });

  it('filters by model, version, and host dropdowns', async () => {
    const onFilteredDevicesChange = vi.fn();
    const multiDevices = [
      ...devices,
      {
        ...devices[0],
        id: 2,
        serial: 'SERIAL-2',
        model: 'Model B',
        build_display_id: 'build-b',
        host_id: 10,
        host_name: '198.51.100.116',
      },
      {
        ...devices[0],
        id: 3,
        serial: 'SERIAL-3',
        model: 'Model A',
        build_display_id: 'build-a',
        host_id: 10,
        host_name: '198.51.100.116',
      },
    ];
    render(
      <ExpandableDeviceTable
        devices={multiDevices}
        onFilteredDevicesChange={onFilteredDevicesChange}
      />,
    );

    fireEvent.change(screen.getByRole('combobox', { name: '按所属主机筛选' }), {
      target: { value: '10' },
    });
    await waitFor(() => {
      const latest = onFilteredDevicesChange.mock.calls[
        onFilteredDevicesChange.mock.calls.length - 1
      ]?.[0] as typeof multiDevices;
      expect(latest.map((d) => d.id)).toEqual([2, 3]);
    });

    fireEvent.change(screen.getByRole('combobox', { name: '按设备筛选' }), {
      target: { value: 'Model A' },
    });
    await waitFor(() => {
      const latest = onFilteredDevicesChange.mock.calls[
        onFilteredDevicesChange.mock.calls.length - 1
      ]?.[0] as typeof multiDevices;
      expect(latest.map((d) => d.id)).toEqual([3]);
    });

    fireEvent.change(screen.getByRole('combobox', { name: '按版本筛选' }), {
      target: { value: 'build-b' },
    });
    await waitFor(() => {
      const latest = onFilteredDevicesChange.mock.calls[
        onFilteredDevicesChange.mock.calls.length - 1
      ]?.[0] as typeof multiDevices;
      expect(latest).toEqual([]);
    });
  });

  // #2614：/devices 全选后底部悬浮批量条压住分页行——坐标级点击被吞或误触「取消选择」。
  // jsdom 没有布局引擎，测不了命中测试；这里钉的是**几何补偿的存在与位置**：
  // 占位必须渲染在分页行**之后**、且在带边框的表格卡片**之外**，才会把最后一行顶出覆盖带。
  it('reserves bottom clearance below the pagination row once devices are selected', () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      ...devices[0],
      id: i + 1,
      serial: `SERIAL-${i + 1}`,
    }));
    const { rerender } = render(
      <ExpandableDeviceTable
        devices={many}
        selectedIds={new Set<number>()}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('device-table-selection-spacer')).not.toBeInTheDocument();

    rerender(
      <ExpandableDeviceTable
        devices={many}
        selectedIds={new Set([1])}
        onSelectionChange={vi.fn()}
      />,
    );
    const spacer = screen.getByTestId('device-table-selection-spacer');
    expect(spacer).toHaveClass(...BULK_BAR_SPACER_CLASS.split(' '));
    expect(spacer).toHaveAttribute('aria-hidden');

    const nextPage = screen.getByRole('button', { name: '下一页' });
    // 占位在分页行之后——否则滚到底时被打扰的仍是分页按钮
    expect(nextPage.compareDocumentPosition(spacer) & Node.DOCUMENT_POSITION_FOLLOWING)
      .toBeTruthy();
    // 占位在卡片之外——放在卡片内会在边框里留出一块空白
    const card = nextPage.closest('.rounded-xl');
    expect(card).not.toBeNull();
    expect(card?.contains(spacer)).toBe(false);

    rerender(
      <ExpandableDeviceTable
        devices={many}
        selectedIds={new Set<number>()}
        onSelectionChange={vi.fn()}
      />,
    );
    expect(screen.queryByTestId('device-table-selection-spacer')).not.toBeInTheDocument();
  });

  // 设备存储空间指标（#2757 心跳 df 上报的字节口径 → /devices 渲染）
  it('renders the storage column with free space when devices report disk', () => {
    const withDisk = devices.map((d) => ({
      ...d,
      disk_total: 128 * 1024 ** 3,
      disk_used: 100 * 1024 ** 3,
    }));
    render(<ExpandableDeviceTable devices={withDisk} />);

    expect(screen.getByRole('columnheader', { name: '存储' })).toBeInTheDocument();
    expect(screen.getByText('剩 28.0 GiB')).toBeInTheDocument();
    expect(screen.getByTitle(/已用 100 GiB \/ 共 128 GiB/)).toBeInTheDocument();
  });

  it('hides the storage column when no device reports disk telemetry', () => {
    render(<ExpandableDeviceTable devices={devices} />);
    expect(screen.queryByRole('columnheader', { name: '存储' })).not.toBeInTheDocument();
  });

  it('shows storage breakdown in the expanded detail card', () => {
    const withDisk = devices.map((d) => ({
      ...d,
      disk_total: 128 * 1024 ** 3,
      disk_used: 100 * 1024 ** 3,
    }));
    render(<ExpandableDeviceTable devices={withDisk} />);
    fireEvent.click(screen.getByText('SERIAL-1'));

    expect(screen.getByText('存储空间')).toBeInTheDocument();
    expect(screen.getByText('总容量 (/data)')).toBeInTheDocument();
    expect(screen.getByText('128 GiB')).toBeInTheDocument();
    expect(screen.getByText('78%')).toBeInTheDocument();
    // 未上报的行在详情里显式说明，而不是渲染 0
    expect(screen.queryByText('未上报（等待 df /data 采样）')).not.toBeInTheDocument();
  });

  it('does not reserve clearance when the table is not selectable', () => {
    const many = Array.from({ length: 51 }, (_, i) => ({
      ...devices[0],
      id: i + 1,
      serial: `SERIAL-${i + 1}`,
    }));
    render(<ExpandableDeviceTable devices={many} selectedIds={new Set([1, 2])} />);

    // 没有 onSelectionChange => 页面不会渲染批量条 => 不该有无故留白
    expect(screen.queryByTestId('device-table-selection-spacer')).not.toBeInTheDocument();
  });

  // #3131：limit 是单次响应护栏，设备数越过它时已加载条数 < 服务端 total。
  // 拿 devices.length 当总数会静默少报，必须用 total 并显式提示列表不完整。
  it('shows the server total on the all-devices card, not the loaded count', () => {
    render(<ExpandableDeviceTable devices={devices} totalCount={9} />);

    // 「全部设备」文案在下拉的 option 里也有一份，按角色取统计卡按钮
    const allCard = screen.getByRole('button', { name: /全部设备/ });
    expect(within(allCard).getByText('9')).toBeInTheDocument();
  });

  it('warns when fewer devices than the server total are loaded', () => {
    render(<ExpandableDeviceTable devices={devices} totalCount={9} />);

    const banner = screen.getByTestId('device-table-truncated');
    expect(banner).toHaveTextContent('设备总数 9 台，本次仅加载 1 台');
    // 搜索/筛选只作用于已加载部分——必须说出来，否则「搜不到」会被读成「不存在」
    expect(banner).toHaveTextContent('未加载的设备不会出现在结果里');
  });

  it('stays quiet when everything is loaded', () => {
    render(<ExpandableDeviceTable devices={devices} totalCount={1} />);

    expect(screen.queryByTestId('device-table-truncated')).not.toBeInTheDocument();
  });

});
