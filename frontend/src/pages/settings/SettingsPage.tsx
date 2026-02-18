import { useState } from 'react';
import { useToast } from '../../components/ui/toast';
import { api } from '../../utils/api';

export default function SettingsPage() {
  const toast = useToast();
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newPassword !== confirmPassword) {
      toast.error('两次输入的新密码不一致');
      return;
    }
    if (newPassword.length < 6) {
      toast.error('新密码长度不能少于6位');
      return;
    }
    setLoading(true);
    try {
      await api.users.changePassword({ old_password: oldPassword, new_password: newPassword });
      toast.success('密码修改成功');
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: any) {
      toast.error(err.response?.data?.detail || '密码修改失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-semibold text-gray-900 mb-1">系统设置</h2>
        <p className="text-sm text-gray-400">管理账号安全和系统配置</p>
      </div>

      <div className="bg-white rounded-xl border border-gray-200 p-6 max-w-lg">
        <h3 className="text-lg font-medium text-gray-900 mb-4">修改密码</h3>
        <form onSubmit={handleChangePassword} className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">当前密码</label>
            <input
              type="password"
              value={oldPassword}
              onChange={(e) => setOldPassword(e.target.value)}
              required
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">新密码</label>
            <input
              type="password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={6}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">确认新密码</label>
            <input
              type="password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={6}
              className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-gray-900/10 focus:border-gray-300"
            />
          </div>
          <button
            type="submit"
            disabled={loading}
            className="inline-flex items-center gap-2 px-4 py-2 bg-gray-900 text-white rounded-lg text-sm font-medium hover:bg-gray-800 transition-colors disabled:opacity-50"
          >
            {loading ? '修改中...' : '修改密码'}
          </button>
        </form>
      </div>
    </div>
  );
}
