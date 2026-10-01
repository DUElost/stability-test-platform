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

// ── #3237 / 批次 B3 G1（S3 / S5）：Plan Execute 的 CSV 与 clipboard ──

describe('planExecuteExport — 公式前缀中和（S3 / #3237）', () => {
  const hostMap = new Map([
    ['h1', { ip: '10.0.0.1', name: 'node-a' }],
    ['h2', { ip: '10.0.0.2', name: '=HACK()' }],
  ]);

  it('serial / model / build / host label 的危险首字符在 raw CSV 里带可见 apostrophe', () => {
    const csv = buildDeviceSelectionCsv(
      [
        { serial: '=1+1', host_id: 'h1', model: 'ABC', build_display_id: 'V1' },
        { serial: 'S2', host_id: 'h2', model: '-M', build_display_id: '@V2' },
        { serial: 'S3', host_id: 'h1', model: 'M3', build_display_id: '\tV3' },
      ],
      hostMap,
    );
    expect(csv).toContain("'=1+1"); // serial
    expect(csv).toContain("'@V2"); // build
    expect(csv).toContain("'\tV3"); // build + TAB 首字符
    expect(csv).toContain("'=HACK()"); // host label 来自管理员 host.name
    expect(csv).toContain("'-M"); // model
  });

  it('普通值不误伤：中间位置的 - / + / @ 保持原样', () => {
    const csv = buildDeviceSelectionCsv(
      [{ serial: 'ABC-123', host_id: 'h1', model: 'A+B', build_display_id: 'V1' }],
      hostMap,
    );
    expect(csv).toContain('ABC-123');
    expect(csv).toContain('A+B');
    expect(csv).not.toContain("'ABC-123");
    expect(csv).not.toContain("'A+B");
  });

  it('既有 framing 回归：逗号 / 引号 escaping 与 hostLabel 顺序不变（#2601）', () => {
    const csv = buildDeviceSelectionCsv(
      [
        { serial: 'S1', host_id: 'h1', model: 'ELA, Pro', build_display_id: 'V104' },
        { serial: 'a"b', host_id: 'h2', model: null, build_display_id: null },
      ],
      hostMap,
    );
    expect(csv).toBe(
      [
        'serial,host,model,version',
        'S1,node-a,"ELA, Pro",V104',
        '"a""b",\'=HACK(),,',
        '',
      ].join('\n'),
    );
  });

  it('全角触发前缀也中和', () => {
    const csv = buildDeviceSelectionCsv(
      [{ serial: '＝1+1', host_id: null, model: '＋x', build_display_id: '－y' }],
      hostMap,
    );
    expect(csv).toContain("'＝1+1");
    expect(csv).toContain("'＋x");
    expect(csv).toContain("'－y");
  });
});

describe('planExecuteExport — clipboard 结构与公式中和（S5 / #3237）', () => {
  it('公式前缀被中和', () => {
    expect(formatSerialsClipboard([{ serial: '=1+1' }, { serial: '@SUM(1,1)' }]))
      .toBe("'=1+1\n'@SUM(1,1)");
  });

  it('行首双引号被中和，不能重新暴露 = 或吞掉下一条记录', () => {
    // 两条记录必须仍是两行：行首 " 不允许把 'B' 并进同一 cell
    expect(formatSerialsClipboard([{ serial: '"=1+1' }, { serial: 'B' }]))
      .toBe('\'"=1+1\nB');
  });

  it('内嵌 TAB / CR / LF / NUL 不制造额外 cell/row', () => {
    const text = formatSerialsClipboard([{ serial: 'a\tb' }, { serial: 'c\nd' }]);
    expect(text).toBe('a\\tb\nc\\nd');
    // 唯一的换行是记录分隔的那一个
    expect(text.split('\n')).toHaveLength(2);
  });

  it('普通 serial 原样保留，记录之间的换行不变', () => {
    expect(formatSerialsClipboard([{ serial: 'A' }, { serial: 'B' }])).toBe('A\nB');
    expect(formatSerialsClipboard([{ serial: 'ABC-123' }, { serial: 'foo@bar' }]))
      .toBe('ABC-123\nfoo@bar');
  });
});
