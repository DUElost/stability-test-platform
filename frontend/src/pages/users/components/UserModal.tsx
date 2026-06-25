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
      newErrors.username = 'Username is required';
    } else if (formData.username.length < 3) {
      newErrors.username = 'Username must be at least 3 characters';
    } else if (!/^[a-zA-Z0-9_]+$/.test(formData.username)) {
      newErrors.username = 'Username can only contain letters, numbers and underscores';
    }

    if (!isEditMode) {
      if (!formData.password) {
        newErrors.password = 'Password is required';
      } else if (formData.password.length < 6) {
        newErrors.password = 'Password must be at least 6 characters';
      }
    }

    if (formData.password || formData.confirmPassword) {
      if (formData.password !== formData.confirmPassword) {
        newErrors.confirmPassword = 'Passwords do not match';
      }
    }

    if (formData.role !== 'user' && formData.role !== 'admin') {
      newErrors.role = 'Invalid role';
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
              {isEditMode ? 'Edit User' : 'Add New User'}
            </h2>
          </div>
          <button
            onClick={handleClose}
            disabled={isSubmitting}
            className={MODAL.closeButton}
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Username */}
          <div>
            <label htmlFor="user-username" className={FORM.label}>
              Username <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <input
              id="user-username"
              type="text"
              value={formData.username}
              onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              placeholder="e.g., john_doe"
              className={fieldClass(!!errors.username)}
              disabled={isSubmitting}
            />
            {errors.username && <p className={FORM.error}>{errors.username}</p>}
          </div>

          {/* Password (only for new users or password change) */}
          <div>
            <label htmlFor="user-password" className={FORM.label}>
              {isEditMode ? 'New Password' : 'Password'} {!isEditMode && <span className={STATUS_TEXT_COLORS.error}>*</span>}
            </label>
            <input
              id="user-password"
              type="password"
              value={formData.password}
              onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              placeholder={isEditMode ? 'Leave blank to keep current' : 'e.g., ********'}
              className={fieldClass(!!errors.password)}
              disabled={isSubmitting}
            />
            {errors.password && <p className={FORM.error}>{errors.password}</p>}
          </div>

          {/* Confirm Password */}
          {(formData.password || !isEditMode) && (
            <div>
              <label htmlFor="user-confirm-password" className={FORM.label}>
                Confirm Password
              </label>
              <input
                id="user-confirm-password"
                type="password"
                value={formData.confirmPassword}
                onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
                placeholder="Re-enter password"
                className={fieldClass(!!errors.confirmPassword)}
                disabled={isSubmitting}
              />
              {errors.confirmPassword && <p className={FORM.error}>{errors.confirmPassword}</p>}
            </div>
          )}

          {/* Role */}
          <div>
            <label htmlFor="user-role" className={FORM.label}>
              Role <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <select
              id="user-role"
              value={formData.role}
              onChange={(e) => setFormData({ ...formData, role: e.target.value })}
              className={cn(FORM.select, 'w-full', errors.role && FORM.inputInvalid)}
              disabled={isSubmitting}
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
            {errors.role && <p className={FORM.error}>{errors.role}</p>}
            <p className={FORM.hint}>
              Admins can manage users and access all features
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
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {isEditMode ? 'Saving...' : 'Adding...'}
                </>
              ) : (
                isEditMode ? 'Save Changes' : 'Add User'
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
