import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PASSWORD_MAX_BYTES, passwordByteLength, passwordRuleError } from './passwordRules';
import type { User } from '@/utils/api';
import { UserModal } from './UserModal';
import { usernameRuleError } from './usernameRules';

/**
 * #2406：密码校验的**前后端一致性**——bcrypt 的 72 是**字节**不是字符。
 *
 * 反例（修复前）：前端只数 `length`（字符），后端 `PasswordStr` 按 UTF-8 字节拒绝
 * → 中文约 24 字即超（3 字节/字）：表单填完、提交后才失败，且只回一句英文技术串
 * （`password must not exceed 72 bytes ...`）——现场「新建账号建不出来」即此形态。
 */
describe('passwordRuleError（与后端 PasswordStr 同判据）', () => {
  it('少于 8 个字符 → 拒绝', () => {
    expect(passwordRuleError('1234567')).toBe('密码至少 8 个字符');
  });

  it('24 个汉字 = 恰好 72 字节 → 放行（bcrypt 上限之内）', () => {
    const pw = '密'.repeat(24);
    expect(passwordByteLength(pw)).toBe(PASSWORD_MAX_BYTES);
    expect(passwordRuleError(pw)).toBeUndefined();
  });

  it('25 个汉字 = 75 字节 → 内联拒绝（修复前这里会放行到提交才失败）', () => {
    const pw = '密'.repeat(25);
    expect(passwordByteLength(pw)).toBe(75);
    expect(passwordRuleError(pw)).toBe(
      '密码不能超过 72 字节（bcrypt 限制；中文约 24 字）',
    );
  });

  it('ASCII：72 字节 = 72 字符放行，73 字符超字节上限', () => {
    expect(passwordRuleError('a'.repeat(72))).toBeUndefined();
    expect(passwordRuleError('a'.repeat(73))).toBe(
      '密码不能超过 72 字节（bcrypt 限制；中文约 24 字）',
    );
  });

  it('超过 128 个字符 → 拒绝（字符维度上限仍在）', () => {
    expect(passwordRuleError('a'.repeat(129))).toBe('密码不能超过 128 个字符');
  });
});

describe('UserModal 创建态（#2406 验收）', () => {
  it('25 个汉字密码 → 内联中文提示，且不触发提交', () => {
    const onSubmit = vi.fn();
    render(
      <UserModal isOpen onClose={() => {}} onSubmit={onSubmit} isSubmitting={false} />,
    );

    fireEvent.change(screen.getByLabelText(/用户名/), { target: { value: 'normal_user' } });
    const pw = '密'.repeat(25);
    fireEvent.change(screen.getByLabelText(/^密码/), { target: { value: pw } });
    fireEvent.change(screen.getByLabelText(/^确认密码/), { target: { value: pw } });
    fireEvent.click(screen.getByRole('button', { name: /添加用户/ }));

    expect(screen.getByText(/72 字节/)).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('合规密码 → 正常提交（不误伤）', () => {
    const onSubmit = vi.fn();
    render(
      <UserModal isOpen onClose={() => {}} onSubmit={onSubmit} isSubmitting={false} />,
    );

    fireEvent.change(screen.getByLabelText(/用户名/), { target: { value: 'normal_user' } });
    fireEvent.change(screen.getByLabelText(/^密码/), { target: { value: 'pass12345' } });
    fireEvent.change(screen.getByLabelText(/^确认密码/), { target: { value: 'pass12345' } });
    fireEvent.click(screen.getByRole('button', { name: /添加用户/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      username: 'normal_user',
      password: 'pass12345',
      role: 'user',
    });
  });
});

/**
 * #2406：用户名规则的前后端一致性。
 *
 * 反例（修复前，现场复现）：前端只允许 `[a-zA-Z0-9_]`、后端无字符集限制 →
 * 建 `stp-tester`（带连字符）时**表单根本不提交**（nginx 访问日志：零
 * `POST /api/v1/users`），用户看到的是「填完了但建不出来」。
 */
describe('usernameRuleError（与后端 UserCreate 同判据）', () => {
  it('带连字符的 stp-tester 放行（现场那次的用户名）', () => {
    expect(usernameRuleError('stp-tester')).toBeUndefined();
  });

  it('字母/数字/下划线/点 放行', () => {
    expect(usernameRuleError('stp_tester')).toBeUndefined();
    expect(usernameRuleError('ops.user2')).toBeUndefined();
  });

  it('太短 / 空 → 拒绝', () => {
    expect(usernameRuleError('  ')).toBe('请输入用户名');
    expect(usernameRuleError('ab')).toBe('用户名至少 3 个字符');
  });

  it('空格与中文 → 拒绝（给出字符集提示）', () => {
    expect(usernameRuleError('bad name')).toBe('用户名只能包含字母、数字、下划线、连字符和点');
    expect(usernameRuleError('测试账号')).toBe('用户名只能包含字母、数字、下划线、连字符和点');
  });
});

describe('UserModal 用户名（#2406 现场：stp-tester 能提交）', () => {
  it('stp-tester + 合规密码 → 正常提交', () => {
    const onSubmit = vi.fn();
    render(
      <UserModal isOpen onClose={() => {}} onSubmit={onSubmit} isSubmitting={false} />,
    );

    fireEvent.change(screen.getByLabelText(/用户名/), { target: { value: 'stp-tester' } });
    fireEvent.change(screen.getByLabelText(/^密码/), { target: { value: 'pass-12345' } });
    fireEvent.change(screen.getByLabelText(/^确认密码/), { target: { value: 'pass-12345' } });
    fireEvent.click(screen.getByRole('button', { name: /添加用户/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      username: 'stp-tester',
      password: 'pass-12345',
      role: 'user',
    });
  });
});

/**
 * #2453：密码管理器自动填充后必须仍能提交。
 *
 * 反例（修复前，现场）：管理器**直接写 `.value`**（常伴非冒泡 input 事件），React 的
 * `onChange` 收不到 → `formData` 仍为空 → 校验报「请输入密码」→ **提交根本不发出**
 * （nginx 访问日志：那几次零 `POST /api/v1/users`；手动键入那次才有请求且 200）。
 */
describe('UserModal 自动填充（#2453）', () => {
  /** 模拟密码管理器：绕过 React 直写 DOM 值 + 派发**不冒泡**的 input 事件。 */
  function autofill(el: HTMLElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: false }));
  }

  it('自动填充用户名+密码+确认密码 → 提交发出且值取自 DOM', () => {
    const onSubmit = vi.fn();
    render(
      <UserModal isOpen onClose={() => {}} onSubmit={onSubmit} isSubmitting={false} />,
    );

    autofill(screen.getByLabelText(/用户名/), 'stp-tester');
    autofill(screen.getByLabelText(/^密码/), 'tPe-KLu-3Uw-3Fb');
    autofill(screen.getByLabelText(/^确认密码/), 'tPe-KLu-3Uw-3Fb');
    fireEvent.click(screen.getByRole('button', { name: /添加用户/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      username: 'stp-tester',
      password: 'tPe-KLu-3Uw-3Fb',
      role: 'user',
    });
  });

  it('自动填充的两次确认密码不一致 → 仍被拦（校验用的是同一份 DOM 值）', () => {
    const onSubmit = vi.fn();
    render(
      <UserModal isOpen onClose={() => {}} onSubmit={onSubmit} isSubmitting={false} />,
    );

    autofill(screen.getByLabelText(/用户名/), 'stp-tester');
    autofill(screen.getByLabelText(/^密码/), 'tPe-KLu-3Uw-3Fb');
    autofill(screen.getByLabelText(/^确认密码/), 'tPe-KLu-3Uw-3Fa');
    fireEvent.click(screen.getByRole('button', { name: /添加用户/ }));

    expect(screen.getByText('两次输入的密码不一致')).toBeInTheDocument();
    expect(onSubmit).not.toHaveBeenCalled();
  });
});

/**
 * #2497：字段必须是**非受控**——受控组件会把外部写入的值回写成 state（state 未同步时
 * 即清空），密码管理器直写 `.value` 后就会陷入「写→清→再写」，占满主线程，
 * 现场表现为浏览器「页面无响应」+ 数秒后重载，且服务端收不到任何请求。
 */
describe('UserModal 非受控字段（#2497）', () => {
  function managerFill(el: HTMLElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: false }));
  }

  const props = {
    isOpen: true,
    onClose: () => {},
    onSubmit: () => {},
    isSubmitting: false,
  };

  it('管理器填值后重渲染不被回写清空（受控实现会在这里清零）', () => {
    const { rerender } = render(<UserModal {...props} />);
    const password = screen.getByLabelText(/^密码/) as HTMLInputElement;
    managerFill(password, 'tPe-KLu-3Uw-3Fb');
    expect(password.value).toBe('tPe-KLu-3Uw-3Fb');

    // 父组件重渲染（真实环境里由轮询/状态变化持续发生）
    rerender(<UserModal {...props} />);

    expect(password.value).toBe('tPe-KLu-3Uw-3Fb');
  });

  it('关闭再打开 → 字段归零（非受控靠卸载重挂载拿到新默认值）', () => {
    const { rerender } = render(<UserModal {...props} />);
    fireEvent.change(screen.getByLabelText(/用户名/), { target: { value: 'stp-tester' } });
    expect(screen.getByLabelText(/用户名/)).toHaveValue('stp-tester');

    rerender(<UserModal {...props} isOpen={false} />); // 关闭：子树卸载
    rerender(<UserModal {...props} />);                // 重开：新默认值

    expect(screen.getByLabelText(/用户名/)).toHaveValue('');
  });
});

/**
 * #2637：编辑态的「密码已填但确认框根本不在 DOM 里」= 提交静默失败。
 *
 * 失败链：管理器直写 `#user-password` 的 `.value`（React 收不到）→ `passwordPresent`
 * 仍为 false ⇒ 确认框不渲染 ⇒ 提交读到 `confirmPassword = ''` 判「不一致」⇒ 提前
 * 返回，而错误文案写在**未渲染**的块里 ⇒ 用户点了保存，界面毫无反应（请求也不发）。
 */
describe('UserModal 编辑态自动填充（#2637）', () => {
  /** 同 #2453：绕过 React 直写 DOM 值 + 派发**不冒泡**的 input 事件。 */
  function managerFill(el: HTMLElement, value: string) {
    const setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype, 'value',
    )!.set!;
    setter.call(el, value);
    el.dispatchEvent(new Event('input', { bubbles: false }));
  }

  const editUser: User = {
    id: 7,
    username: 'stp-tester',
    role: 'user',
    is_active: 'true',
    created_at: '2026-09-01T00:00:00Z',
    last_login: null,
  };

  /** 真实用法：弹窗先以关闭态挂载，再打开——编辑态的表单重置发生在这次转场里。 */
  function openInEditMode(onUpdate: (data: unknown) => void) {
    const { rerender } = render(
      <UserModal isOpen={false} onClose={() => {}} onUpdate={onUpdate} editUser={editUser} isSubmitting={false} />,
    );
    rerender(
      <UserModal isOpen onClose={() => {}} onUpdate={onUpdate} editUser={editUser} isSubmitting={false} />,
    );
  }

  it('直写新密码 → 首次保存给出可读错误 + 确认框就地出现 → 补填后提交发出', () => {
    const onUpdate = vi.fn();
    openInEditMode(onUpdate);

    // 管理器只会填「已存在」的字段；编辑态确认框此前不渲染 ⇒ 它只会落到新密码上
    managerFill(screen.getByLabelText(/新密码/), 'tPe-KLU-3Uw-3Fb');
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));

    // 第一步：不发请求（没确认不能改密），但错误可见、确认框已出现——旧实现两步都静默
    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByText('请再次输入密码以确认修改')).toBeInTheDocument();
    const confirm = screen.getByLabelText(/确认密码/);

    // 第二步：按提示补填（真实键入会冒泡，状态与渲染同步）→ 提交发出
    fireEvent.change(confirm, { target: { value: 'tPe-KLU-3Uw-3Fb' } });
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));
    expect(onUpdate).toHaveBeenCalledWith(
      expect.objectContaining({ password: 'tPe-KLU-3Uw-3Fb' }),
    );
  });

  it('人工键入新密码 → 确认框立刻出现（不等到提交）', () => {
    const onUpdate = vi.fn();
    openInEditMode(onUpdate);

    expect(screen.queryByLabelText(/确认密码/)).not.toBeInTheDocument();
    // 浏览器键入每敲一键都会派发**冒泡**的 input 事件（管理器直写那条不冒泡，见上）
    fireEvent.input(screen.getByLabelText(/新密码/), { target: { value: 'tPe-KLU-3Uw-3Fb' } });
    expect(screen.getByLabelText(/确认密码/)).toBeInTheDocument();
  });

  it('只直写新密码 → 不静默：给出可读错误且确认框就地渲染', () => {
    const onUpdate = vi.fn();
    openInEditMode(onUpdate);

    managerFill(screen.getByLabelText(/新密码/), 'tPe-KLU-3Uw-3Fb');
    fireEvent.click(screen.getByRole('button', { name: /保存/ }));

    // 不发出请求（没确认就不能改密）——但**必须看得见原因**，且确认框就地出现
    expect(onUpdate).not.toHaveBeenCalled();
    expect(screen.getByText('请再次输入密码以确认修改')).toBeInTheDocument();
    expect(screen.getByLabelText(/确认密码/)).toBeInTheDocument();
  });
});
