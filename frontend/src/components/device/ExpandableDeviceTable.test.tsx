import { render, screen } from '@testing-library/react';
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
});
