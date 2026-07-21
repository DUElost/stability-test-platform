import { describe, expect, it } from 'vitest';
import { buildDeviceSelectionCsv, formatSerialsClipboard } from './planExecuteExport';

describe('planExecuteExport', () => {
  it('formats serials as newline list', () => {
    expect(formatSerialsClipboard([{ serial: 'A' }, { serial: 'B' }])).toBe('A\nB');
  });

  it('builds CSV with host labels and escapes commas', () => {
    const hostMap = new Map([['h1', { ip: '10.0.0.1', name: 'node-a' }]]);
    const csv = buildDeviceSelectionCsv(
      [
        { serial: 'S1', host_id: 'h1', model: 'ELA, Pro', build_display_id: 'V104' },
        { serial: 'S2', host_id: null, model: null, build_display_id: null },
      ],
      hostMap,
    );
    expect(csv).toBe(
      [
        'serial,host,model,version',
        'S1,10.0.0.1,"ELA, Pro",V104',
        'S2,未分配节点,,',
        '',
      ].join('\n'),
    );
  });
});
