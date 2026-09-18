import { describe, expect, it } from 'vitest';
import { buildDeviceSelectionCsv, formatSerialsClipboard } from './planExecuteExport';

describe('planExecuteExport', () => {
  it('formats serials as newline list', () => {
    expect(formatSerialsClipboard([{ serial: 'A' }, { serial: 'B' }])).toBe('A\nB');
  });

  it('builds CSV with host labels and escapes commas', () => {
    // #2601：主机显示名统一走 hostLabel()，顺序 name > ip > hostId（与主机页/设备页/
    // 报告页一致）。此前本家族是 ip-first——同一台 host 在不同导出面读数不同。
    const hostMap = new Map([
      ['h1', { ip: '10.0.0.1', name: 'node-a' }],
      ['h2', { ip: '10.0.0.2', name: null }],
      ['h3', { ip: null, name: null }],
    ]);
    const csv = buildDeviceSelectionCsv(
      [
        { serial: 'S1', host_id: 'h1', model: 'ELA, Pro', build_display_id: 'V104' },
        { serial: 'S2', host_id: null, model: null, build_display_id: null },
        { serial: 'S3', host_id: 'h2', model: null, build_display_id: null },
        { serial: 'S4', host_id: 'h3', model: null, build_display_id: null },
      ],
      hostMap,
    );
    expect(csv).toBe(
      [
        'serial,host,model,version',
        'S1,node-a,"ELA, Pro",V104',
        'S2,未分配节点,,',
        'S3,10.0.0.2,,',
        'S4,h3,,',
        '',
      ].join('\n'),
    );
  });
});
