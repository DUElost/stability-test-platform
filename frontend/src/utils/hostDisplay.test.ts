import { describe, expect, it } from 'vitest';
import type { Host } from './api/types';
import { UNASSIGNED_HOST_LABEL, hostLabel } from './hostDisplay';

function host(partial: Partial<Host>): Host {
  return { id: 'h1', name: null, ip: null, status: 'ONLINE', ssh_user: null, extra: {}, mount_status: {}, ...partial } as Host;
}

describe('hostLabel（#2601 唯一入口）', () => {
  it('优先 name（与主机页/设备页/报告页一致）', () => {
    expect(hostLabel(host({ name: '测试机1', ip: '192.0.2.11' }), '192-0-2-11')).toBe('测试机1');
  });

  it('无 name 时用 ip——**不是**内部 slug', () => {
    // #2601 现场形态：同一台 host 在一处显示 slug、一处显示 IP，被读成两台机器
    expect(hostLabel(host({ ip: '192.0.2.11' }), '192-0-2-11')).toBe('192.0.2.11');
  });

  it('name/ip 都没有时退回 hostId（不得显示 undefined）', () => {
    expect(hostLabel(host({}), '192-0-2-11')).toBe('192-0-2-11');
  });

  it('host 为 null（缓存过旧/主机已删）时仍给得出 hostId', () => {
    expect(hostLabel(null, '192-0-2-11')).toBe('192-0-2-11');
    expect(hostLabel(undefined, 'h-9')).toBe('h-9');
  });

  it('unassigned 是桶不是主机：统一给「未分配节点」', () => {
    expect(hostLabel(null, 'unassigned')).toBe(UNASSIGNED_HOST_LABEL);
    expect(hostLabel(host({}), 'unassigned')).toBe(UNASSIGNED_HOST_LABEL);
  });

  it('全空时用调用方给的 fallback（表格单元格传 —）', () => {
    expect(hostLabel(null, null)).toBe('未知主机');
    expect(hostLabel(null, '   ')).toBe('未知主机');
    expect(hostLabel(null, '', '—')).toBe('—');
  });

  it('空串 name/ip 不遮蔽 hostId（后端可能给空串而不是 null）', () => {
    expect(hostLabel(host({ name: '', ip: '' }), 'h-2')).toBe('h-2');
  });
});
