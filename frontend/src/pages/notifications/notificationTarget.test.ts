import { describe, expect, it } from 'vitest';
import { notificationTarget } from './notificationTarget';
import type { NotificationLog } from '@/utils/api/types';

const log = (over: Partial<NotificationLog>): NotificationLog =>
  ({ id: 1, event_type: 'UNKNOWN', context: {}, read: false, ...over } as NotificationLog);

describe('notificationTarget #625 context.link', () => {
  it('站内路径 link 优先盲拼', () => {
    expect(notificationTarget(log({ context: { link: '/hosts' } }))).toEqual({
      to: '/hosts',
      label: '查看详情',
    });
  });

  it('协议相对 URL 一并拒绝（#2054：//host 与 /\\host 也以 / 开头）', () => {
    expect(notificationTarget(log({ context: { link: '//evil.example/x' } }))).toBeNull();
    expect(notificationTarget(log({ context: { link: '/\\evil.example/x' } }))).toBeNull();
  });

  // #2288：WHATWG URL 解析会**移除**输入里的 TAB/LF/CR，故 `/<TAB>/evil.com` 与
  // `//evil.com` 等价（协议相对 → 跨源），而它第二个字符是 TAB，能通过只挡
  // `/` 与 `\` 的 #2054 判据。
  it('拒绝前导斜杠后紧跟 TAB/LF/CR 的形态（移除后即协议相对 URL）', () => {
    expect(notificationTarget(log({ context: { link: '/\t/evil.example/x' } }))).toBeNull();
    expect(notificationTarget(log({ context: { link: '/\n/evil.example/x' } }))).toBeNull();
    expect(notificationTarget(log({ context: { link: '/\r/evil.example/x' } }))).toBeNull();
    // 正常站内路径不受影响（收紧不得把合法目标一起挡掉）
    expect(notificationTarget(log({ context: { link: '/hosts' } }))).toEqual({
      to: '/hosts',
      label: '查看详情',
    });
  });

  it('非 / 开头的 link 忽略（防外链）', () => {
    expect(notificationTarget(log({ context: { link: 'https://evil.example/x' } }))).toBeNull();
  });

  it('link 优先于 event_type 映射', () => {
    const t = notificationTarget(
      log({ event_type: 'RUN_FAILED', context: { run_id: 7, link: '/hosts' } }),
    );
    expect(t?.to).toBe('/hosts');
  });

  it('无 link 时回退既有映射', () => {
    expect(
      notificationTarget(log({ event_type: 'RUN_FAILED', context: { run_id: 7 } }))?.to,
    ).toBe('/execution/plan-runs/7');
  });

  it('未知类型且无 link → null', () => {
    expect(notificationTarget(log({}))).toBeNull();
  });
});
