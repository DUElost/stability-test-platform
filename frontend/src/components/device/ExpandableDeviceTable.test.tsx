import { fireEvent, render, screen, waitFor } from '@testing-library/react';
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

    const selectPage = screen.getByRole('checkbox', { name: '选择当前页设备' }) as HTMLInputElement;
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

});
