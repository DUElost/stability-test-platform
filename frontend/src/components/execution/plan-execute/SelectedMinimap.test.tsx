import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SelectedMinimap, MINIMAP_VIRTUAL_THRESHOLD } from './SelectedMinimap';
import type { ReadinessDevice } from '@/utils/planExecuteReadiness';

vi.mock('@tanstack/react-virtual', () => ({
  useVirtualizer: () => ({
    getTotalSize: () => 40,
    getVirtualItems: () => [],
  }),
}));

function device(id: number): ReadinessDevice {
  return {
    id,
    serial: `serial-${id}`,
    model: 'M1',
    build_display_id: 'V1',
    host_id: 'h1',
    status: 'ONLINE',
    tags: [],
  } as ReadinessDevice;
}

describe('SelectedMinimap layout', () => {
  it('uses fixed-width auto-fill columns for static grid', () => {
    const devices = Array.from({ length: 5 }, (_, i) => device(i + 1));
    render(
      <SelectedMinimap
        embedded
        devices={devices}
        readinessByDeviceId={new Map()}
        hostMap={new Map([['h1', { ip: '10.0.0.1' }]])}
        onLocate={() => {}}
        onRemove={() => {}}
        onCopySerials={() => {}}
        onDownloadCsv={() => {}}
      />,
    );

    const grid = screen.getByTestId('selected-minimap-grid');
    expect(grid.className).toContain('w-full');
    expect(grid).toHaveStyle({ gridTemplateColumns: 'repeat(auto-fill, 22px)' });
    expect(devices.length).toBeLessThan(MINIMAP_VIRTUAL_THRESHOLD);
    expect(grid).not.toHaveAttribute('data-virtual', 'true');
  });
});
