import { describe, expect, it } from 'vitest';

import { readNamedValues } from './forms';

/**
 * #2453 / #2456：提交取值必须**以 DOM 实际值为准**。
 *
 * 反例：受控组件只认 React state，而密码管理器直写 `.value`（常伴非冒泡 input 事件）
 * → state 为空 → 校验把已填好的表单判成空、提交根本不发出。
 */
describe('readNamedValues', () => {
  function mountForm(innerHtml: string): HTMLFormElement {
    document.body.innerHTML = '<form>' + innerHtml + '</form>';
    return document.querySelector('form')!;
  }

  it('以 DOM 值为准，而不是调用方传入的 state 回落值', () => {
    const root = mountForm('<input name="username" value="typed-later" />');
    const values = readNamedValues(root, { username: '', password: '' });
    expect(values.username).toBe('typed-later'); // DOM 有值 → 覆盖空的 state
    expect(values.password).toBe(''); // DOM 没有该字段 → 保持回落
  });

  it('非冒泡 input 事件（密码管理器常见形态）不影响取值', () => {
    const root = mountForm('<input name="password" />');
    const el = root.querySelector<HTMLInputElement>('input')!;
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value',
    )!.set!;
    setter.call(el, 'tPe-KLu-3Uw-3Fb');
    el.dispatchEvent(new Event('input', { bubbles: false }));
    expect(readNamedValues(root, { password: '' }).password).toBe('tPe-KLu-3Uw-3Fb');
  });

  it('root 为空 → 原样回落（不抛）', () => {
    expect(readNamedValues(null, { a: 'x' })).toEqual({ a: 'x' });
  });

  it('select 也能读（role 这类枚举字段）', () => {
    const root = mountForm(
      '<select name="role"><option value="user">u</option>' +
        '<option value="admin">a</option></select>',
    );
    root.querySelector('select')!.value = 'admin';
    expect(readNamedValues(root, { role: 'user' }).role).toBe('admin');
  });
});
