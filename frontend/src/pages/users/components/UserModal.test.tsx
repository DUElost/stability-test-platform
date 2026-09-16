import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { PASSWORD_MAX_BYTES, passwordByteLength, passwordRuleError } from './passwordRules';
import { UserModal } from './UserModal';

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
