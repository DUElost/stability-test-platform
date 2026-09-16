/**
 * #2406：密码规则的前端判据——**必须与后端 `PasswordStr` 同一套**
 * （`backend/core/security.py`：`min_length=8`、`max_length=128`，且 UTF-8 编码后 ≤72 字节）。
 *
 * 为什么要单独一个文件：组件文件只应导出组件（`react-refresh/only-export-components`），
 * 而本判据要被用例直接断言，故放在与组件同目录的纯模块里。
 *
 * 反例（修复前）：判据只写在 `UserModal` 内部、且只数**字符**——中文 3 字节/字，
 * 约 24 字即超 72 字节：表单放行、提交后才被后端 422 拒绝，用户只看到一句英文技术串。
 */

/** bcrypt 只使用前 72 字节（不是字符）。 */
export const PASSWORD_MAX_BYTES = 72;

export function passwordByteLength(value: string): number {
  return new TextEncoder().encode(value).length;
}

/** 返回第一条不满足的规则文案；全部满足返回 `undefined`。 */
export function passwordRuleError(value: string): string | undefined {
  if (value.length < 8) return '密码至少 8 个字符';
  if (value.length > 128) return '密码不能超过 128 个字符';
  if (passwordByteLength(value) > PASSWORD_MAX_BYTES) {
    return '密码不能超过 72 字节（bcrypt 限制；中文约 24 字）';
  }
  return undefined;
}
