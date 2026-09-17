import { useState } from 'react';
import { UserPlus, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import type { User } from '@/utils/api';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { FORM } from '@/design-system';
import { cn } from '@/lib/utils';
// #2406：密码规则（含 bcrypt 的 72 **字节**上限）与后端 PasswordStr 同判据。
import { passwordRuleError } from './passwordRules';
import { usernameRuleError } from './usernameRules';

interface UserModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** 创建态提交；编辑态走 onUpdate（M1 死代码清理后编辑态不再传 onSubmit）。 */
  onSubmit?: (data: { username: string; password?: string; role: string }) => void;
  onUpdate?: (data: { username?: string; password?: string; role?: string; is_active?: string }) => void;
  isSubmitting?: boolean;
  editUser?: User | null;
}

export function UserModal({ isOpen, onClose, onSubmit, onUpdate, isSubmitting, editUser }: UserModalProps) {
  const [formData, setFormData] = useState({
    username: '',
    password: '',
    confirmPassword: '',
    role: 'user',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  const isEditMode = !!editUser;

  // React 官方"adjust state when prop changes"模式：render 期对比 prev 快照并重置表单。
  // 用「打开状态 + 编辑对象 id」作比较键（id 而非对象引用），避免父组件每次重渲染
  // 传新 editUser 引用而误触 reset。
  const [prevOpen, setPrevOpen] = useState(isOpen);
  const [prevEditingId, setPrevEditingId] = useState<number | null>(editUser?.id ?? null);
  if (prevOpen !== isOpen || prevEditingId !== (editUser?.id ?? null)) {
    setPrevOpen(isOpen);
    setPrevEditingId(editUser?.id ?? null);
    if (isOpen) {
      if (editUser) {
        setFormData({
          username: editUser.username,
          password: '',
          confirmPassword: '',
          role: editUser.role,
        });
      } else {
        setFormData({ username: '', password: '', confirmPassword: '', role: 'user' });
      }
      setErrors({});
    }
  }

  /** 提交时以**表单 DOM 实际值**为准（#2453）。
   *
   * 密码管理器/浏览器自动填充是**直接写 `.value`**（常伴非冒泡 input 事件），React 的
   * `onChange` 收不到 → state 仍为空；只认 state 会把「明明填好了」的表单判成空、在
   * 提交前拦下（服务端连请求都收不到）。故提交与校验都改用 FormData 读到的值，
   * state 只作回落。 */
  const readFormValues = (form: HTMLFormElement) => {
    const data = new FormData(form);
    const pick = (key: string, fallback: string) => {
      const value = data.get(key);
      return typeof value === 'string' ? value : fallback;
    };
    return {
      username: pick('username', formData.username),
      password: pick('password', formData.password),
      confirmPassword: pick('confirmPassword', formData.confirmPassword),
      role: pick('role', formData.role),
    };
  };

  const validate = (values = formData): boolean => {
    const newErrors: Record<string, string> = {};

    const usernameError = usernameRuleError(values.username);
    if (usernameError) newErrors.username = usernameError;

    if (!isEditMode) {
      if (!values.password) {
        newErrors.password = '请输入密码';
      } else {
        const passwordError = passwordRuleError(values.password);
        if (passwordError) newErrors.password = passwordError;
      }
    } else if (values.password) {
      // 编辑模式改了密码也要过同一约束(#281 CR 意见 / #2406 字节维度)
      const passwordError = passwordRuleError(values.password);
      if (passwordError) newErrors.password = passwordError;
    }

    if (values.password || values.confirmPassword) {
      if (values.password !== values.confirmPassword) {
        newErrors.confirmPassword = '两次输入的密码不一致';
      }
    }

    if (values.role !== 'user' && values.role !== 'admin') {
      newErrors.role = '无效的角色';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const values = readFormValues(e.currentTarget as HTMLFormElement);
    if (validate(values)) {
      if (isEditMode && onUpdate) {
        const updateData: { username?: string; password?: string; role?: string } = {};
        if (values.username !== editUser.username) {
          updateData.username = values.username.trim();
        }
        if (values.password) {
          updateData.password = values.password;
        }
        if (values.role !== editUser.role) {
          updateData.role = values.role;
        }
        onUpdate(updateData);
      } else {
        onSubmit?.({
          username: values.username.trim(),
          password: values.password,
          role: values.role,
        });
      }
    }
  };

  const handleClose = () => {
    if (!isSubmitting) {
      onClose();
    }
  };

  if (!isOpen) return null; // Radix 挂载即动画；保持早退，open 变化驱动开关

  const fieldClass = (hasError: boolean) =>
    cn(FORM.input, hasError && FORM.inputInvalid);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) handleClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <UserPlus className={STATUS_TEXT_COLORS.primary} size={20} />
            {isEditMode ? '编辑用户' : '添加用户'}
          </DialogTitle>
        </DialogHeader>

        {/* Form */}
        <form onSubmit={handleSubmit} className="space-y-4">
          {/* Username */}
          <div>
            <label htmlFor="user-username" className={FORM.label}>
              用户名 <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <input
              id="user-username"
              name="username"
              type="text"
              value={formData.username}
              onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              placeholder="例如：zhang_san"
              className={fieldClass(!!errors.username)}
              disabled={isSubmitting}
            />
            {errors.username && <p className={FORM.error}>{errors.username}</p>}
          </div>

          {/* Password (only for new users or password change) */}
          <div>
            <label htmlFor="user-password" className={FORM.label}>
              {isEditMode ? '新密码' : '密码'} {!isEditMode && <span className={STATUS_TEXT_COLORS.error}>*</span>}
            </label>
            <input
              id="user-password"
              name="password"
              type="password"
              value={formData.password}
              onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '至少 8 位'}
              maxLength={128}
              className={fieldClass(!!errors.password)}
              disabled={isSubmitting}
            />
            {errors.password && <p className={FORM.error}>{errors.password}</p>}
          </div>

          {/* Confirm Password */}
          {(formData.password || !isEditMode) && (
            <div>
              <label htmlFor="user-confirm-password" className={FORM.label}>
                确认密码
              </label>
              <input
                id="user-confirm-password"
                name="confirmPassword"
                type="password"
                value={formData.confirmPassword}
                onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
                placeholder="再次输入密码"
                className={fieldClass(!!errors.confirmPassword)}
                disabled={isSubmitting}
              />
              {errors.confirmPassword && <p className={FORM.error}>{errors.confirmPassword}</p>}
            </div>
          )}

          {/* Role */}
          <div>
            <label htmlFor="user-role" className={FORM.label}>
              角色 <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <select
              id="user-role"
              name="role"
              value={formData.role}
              onChange={(e) => setFormData({ ...formData, role: e.target.value })}
              className={cn(FORM.select, 'w-full', errors.role && FORM.inputInvalid)}
              disabled={isSubmitting}
            >
              <option value="user">普通用户</option>
              <option value="admin">管理员</option>
            </select>
            {errors.role && <p className={FORM.error}>{errors.role}</p>}
            <p className={FORM.hint}>
              管理员可管理用户并访问全部功能
            </p>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <Button
              type="button"
              variant="outline"
              onClick={handleClose}
              disabled={isSubmitting}
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {isEditMode ? '保存中…' : '添加中…'}
                </>
              ) : (
                isEditMode ? '保存修改' : '添加用户'
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
