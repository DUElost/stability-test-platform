import { useState, useEffect } from 'react';
import { X, UserPlus, Loader2 } from 'lucide-react';
import type { User } from '@/utils/api';

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

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/50">
      <div className="bg-white rounded-lg shadow-xl w-full max-w-md">
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200">
          <div className="flex items-center gap-2">
            <UserPlus className="text-blue-600" size={20} />
            <h2 className="text-lg font-semibold text-slate-900">
              {isEditMode ? 'Edit User' : 'Add New User'}
            </h2>
          </div>
          <button
            onClick={handleClose}
            disabled={isSubmitting}
            className="text-slate-400 hover:text-slate-600 transition-colors disabled:opacity-50"
          >
            <X size={20} />
          </button>
        </div>

        {/* Form */}
        <form onSubmit={handleSubmit} className="p-6 space-y-4">
          {/* Username */}
          <div>
            <label htmlFor="user-username" className="block text-sm font-medium text-slate-700 mb-1">
              Username <span className="text-red-500">*</span>
            </label>
            <input
              id="user-username"
              type="text"
              value={formData.username}
              onChange={(e) => setFormData({ ...formData, username: e.target.value })}
              placeholder="e.g., john_doe"
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.username ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.username && <p className="mt-1 text-sm text-red-600">{errors.username}</p>}
          </div>

          {/* Password (only for new users or password change) */}
          <div>
            <label htmlFor="user-password" className="block text-sm font-medium text-slate-700 mb-1">
              {isEditMode ? 'New Password' : 'Password'} {!isEditMode && <span className="text-red-500">*</span>}
            </label>
            <input
              id="user-password"
              type="password"
              value={formData.password}
              onChange={(e) => setFormData({ ...formData, password: e.target.value })}
              placeholder={isEditMode ? 'Leave blank to keep current' : 'e.g., ********'}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.password ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.password && <p className="mt-1 text-sm text-red-600">{errors.password}</p>}
          </div>

          {/* Confirm Password */}
          {(formData.password || !isEditMode) && (
            <div>
              <label htmlFor="user-confirm-password" className="block text-sm font-medium text-slate-700 mb-1">
                Confirm Password
              </label>
              <input
                id="user-confirm-password"
                type="password"
                value={formData.confirmPassword}
                onChange={(e) => setFormData({ ...formData, confirmPassword: e.target.value })}
                placeholder="Re-enter password"
                className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                  errors.confirmPassword ? 'border-red-300' : 'border-slate-300'
                }`}
                disabled={isSubmitting}
              />
              {errors.confirmPassword && <p className="mt-1 text-sm text-red-600">{errors.confirmPassword}</p>}
            </div>
          )}

          {/* Role */}
          <div>
            <label htmlFor="user-role" className="block text-sm font-medium text-slate-700 mb-1">
              Role <span className="text-red-500">*</span>
            </label>
            <select
              id="user-role"
              value={formData.role}
              onChange={(e) => setFormData({ ...formData, role: e.target.value })}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.role ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            >
              <option value="user">User</option>
              <option value="admin">Admin</option>
            </select>
            {errors.role && <p className="mt-1 text-sm text-red-600">{errors.role}</p>}
            <p className="mt-1 text-xs text-slate-500">
              Admins can manage users and access all features
            </p>
          </div>

          {/* Actions */}
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={handleClose}
              disabled={isSubmitting}
              className="px-4 py-2 text-slate-700 bg-slate-100 hover:bg-slate-200 rounded-lg transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={isSubmitting}
              className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg transition-colors disabled:opacity-50"
            >
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {isEditMode ? 'Saving...' : 'Adding...'}
                </>
              ) : (
                isEditMode ? 'Save Changes' : 'Add User'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
