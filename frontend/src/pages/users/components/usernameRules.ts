/**
 * #2406：用户名规则的前端判据——**必须与后端 UserCreate 同一套**
 * （`backend/api/routes/users.py` 与 `auth.py` 的注册入口共用同一 pattern）。
 *
 * 为什么单独一个文件：组件文件只应导出组件（`react-refresh/only-export-components`），
 * 而本判据要被用例直接断言。
 *
 * 反例（修复前，2026-09-16 现场）：前端只允许 `[a-zA-Z0-9_]`，后端**没有**字符集限制。
 * 于是想建 `stp-tester`（连字符）时**表单根本不会提交**——inline 提示之外没有任何请求，
 * 用户看到的是「填完了但建不出来」（nginx 访问日志：零 `POST /api/v1/users`）。
 * 连字符是常见用户名字符，收紧到「字母/数字/下划线/点/连字符」两边同判据即可。
 */

/** 两端共用的字符集与长度（后端 pattern 与之逐字对应）。 */
export const USERNAME_PATTERN = /^[A-Za-z0-9_.-]+$/;
export const USERNAME_MIN_LENGTH = 3;
export const USERNAME_MAX_LENGTH = 64;

/** 返回第一条不满足的规则文案；全部满足返回 `undefined`。 */
export function usernameRuleError(value: string): string | undefined {
  const trimmed = value.trim();
  if (!trimmed) return '请输入用户名';
  if (trimmed.length < USERNAME_MIN_LENGTH) {
    return `用户名至少 ${USERNAME_MIN_LENGTH} 个字符`;
  }
  if (trimmed.length > USERNAME_MAX_LENGTH) {
    return `用户名不能超过 ${USERNAME_MAX_LENGTH} 个字符`;
  }
  if (!USERNAME_PATTERN.test(trimmed)) {
    return '用户名只能包含字母、数字、下划线、连字符和点';
  }
  return undefined;
}
