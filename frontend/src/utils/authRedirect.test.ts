import { describe, expect, it } from 'vitest';

import { resolvePostLoginTarget } from './authRedirect';

/**
 * #2081：回跳守卫必须按 **URL 归一**判定，而不是字面前缀。
 *
 * 反例（修复前）：`/\evil.com` 通过 `startsWith('//')` 检查，但 WHATWG 对特殊协议
 * 把 `\` 归一为 `/` → 解析成 `https://evil.com/`（跨源）；`navigate` 抛
 * SecurityError 被 LoginPage 当成登录失败，会话已建立却停在登录页。
 */
describe('resolvePostLoginTarget', () => {
  it('接受站内路径（含 query/hash）', () => {
    expect(resolvePostLoginTarget(null, '/devices')).toBe('/devices');
    expect(resolvePostLoginTarget(null, '/plan-runs/12?tab=x#f')).toBe('/plan-runs/12?tab=x#f');
    expect(resolvePostLoginTarget(null, '/devices?q=a%20b')).toBe('/devices?q=a%20b');
  });

  it('state.from 优先于 ?next=', () => {
    expect(resolvePostLoginTarget({ from: '/hosts' }, '/devices')).toBe('/hosts');
  });

  it.each([
    '//evil.com',
    '/\\evil.com',
    '/\\\\evil.com',
    '/\\/evil.com',
    '/\t/evil.com',
    'https://evil.com',
    'evil.com',
    '',
  ])('拒绝非站内同源候选：%j', (candidate) => {
    expect(resolvePostLoginTarget(null, candidate)).toBe('/');
  });

  it('null / 非字符串 state 回落到 ?next= 或 /', () => {
    expect(resolvePostLoginTarget(undefined, null)).toBe('/');
    expect(resolvePostLoginTarget({ from: 123 }, null)).toBe('/');
    expect(resolvePostLoginTarget({ from: 123 }, '/devices')).toBe('/devices');
  });

  it('归一后返回的路径可安全交给 navigate（与校验对象一致）', () => {
    // 路径里的编码不被改写；反斜杠形态一律拒绝而不是归一后放行
    expect(resolvePostLoginTarget(null, '/%5Cevil.com')).toBe('/%5Cevil.com');
    expect(resolvePostLoginTarget(null, '/\\evil.com')).toBe('/');
  });
});
