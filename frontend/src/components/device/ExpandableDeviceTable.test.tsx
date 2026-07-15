import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ExpandableDeviceTable } from './ExpandableDeviceTable';

const devices = [
  {
    id: 1,
    serial: 'SERIAL-1',
    model: 'Model A',
    status: 'idle' as const,
    build_display_id: 'build-a',
    host_name: '172.21.10.36',
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
});
