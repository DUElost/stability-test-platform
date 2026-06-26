import { useState, useEffect } from 'react';
import { X, UserPlus, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import type { User } from '@/utils/api';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { FORM, MODAL } from '@/design-system';
import { cn } from '@/lib/utils';

interface UserModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { username: string; password?: string; role: string }) => void;
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

  useEffect(() => {
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
  }, [isOpen, editUser]);

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!formData.username.trim()) {
      newErrors.username = '请输入用户名';
    } else if (formData.username.length < 3) {
      newErrors.username = '用户名至少 3 个字符';
    } else if (!/^[a-zA-Z0-9_]+$/.test(formData.username)) {
      newErrors.username = '用户名只能包含字母、数字和下划线';
    }

    if (!isEditMode) {
      if (!formData.password) {
        newErrors.password = '请输入密码';
      } else if (formData.password.length < 6) {
        newErrors.password = '密码至少 6 个字符';
      }
    }

    if (formData.password || formData.confirmPassword) {
      if (formData.password !== formData.confirmPassword) {
        newErrors.confirmPassword = '两次输入的密码不一致';
      }
    }

    if (formData.role !== 'user' && formData.role !== 'admin') {
      newErrors.role = '无效的角色';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (validate()) {
      if (isEditMode && onUpdate) {
        const updateData: { username?: string; password?: string; role?: string } = {};
        if (formData.username !== editUser.username) {
          updateData.username = formData.username.trim();
        }
        if (formData.password) {
          updateData.password = formData.password;
        }
        if (formData.role !== editUser.role) {
          updateData.role = formData.role;
        }
        onUpdate(updateData);
      } else {
        onSubmit({
          username: formData.username.trim(),
          password: formData.password,
          role: formData.role,
        });
      }
    }
  };

  const handleClose = () => {
    if (!isSubmitting) {
      onClose();
    }
  };

  if (!isOpen) return null;

  const fieldClass = (hasError: boolean) =>
    cn(FORM.input, hasError && FORM.inputInvalid);

  return (
    <div className={MODAL.overlay}>
      <div className={MODAL.panel}>
        {/* Header */}
        <div className={MODAL.header}>
          <div className="flex items-center gap-2">
            <UserPlus className={STATUS_TEXT_COLORS.primary} size={20} />
            <h2 className={MODAL.title}>
              {isEditMode ? '编辑用户' : '添加用户'}
            </h2>
          </div>
          <button
            type="button"
            onClick={handleClose}
            disabled={isSubmitting}
            className={MODAL.closeButton}
            aria-label="关闭"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Username */}
          <div>
            <label htmlFor="user-username" className={FORM.label}>
              用户名 <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <input
              id="user-username"
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
              type="password"
              value={formData.password}
              onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              placeholder={isEditMode ? '留空表示不修改' : '至少 6 位'}
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
      </div>
    </div>
  );
}
