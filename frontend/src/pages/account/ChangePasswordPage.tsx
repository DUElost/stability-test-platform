import { useState } from 'react';
import { api } from '@/utils/api';
import { KeyRound } from 'lucide-react';
import { PageContainer, PageHeader } from '@/components/layout';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { FORM, STATUS_CHIP, TEXT } from '@/design-system/tokens';
import { cn } from '@/lib/utils';

export default function ChangePasswordPage() {
  const [oldPassword, setOldPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleChangePassword = async (e: React.FormEvent) => {
    e.preventDefault();
    setMessage(null);

    if (newPassword !== confirmPassword) {
      setMessage({ type: 'error', text: '两次输入的新密码不一致' });
      return;
    }
    if (newPassword.length < 6) {
      setMessage({ type: 'error', text: '新密码长度不能少于6位' });
      return;
    }

    setLoading(true);
    try {
      await api.users.changePassword({ old_password: oldPassword, new_password: newPassword });
      setMessage({ type: 'success', text: '密码修改成功' });
      setOldPassword('');
      setNewPassword('');
      setConfirmPassword('');
    } catch (err: unknown) {
      const detail = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail;
      setMessage({ type: 'error', text: detail || '密码修改失败' });
    } finally {
      setLoading(false);
    }
  };

  return (
    <PageContainer width="narrow">
      <PageHeader title="修改密码" subtitle="更新您的账号登录密码" />

      <Card className="max-w-lg">
        <CardHeader className="flex flex-row items-center gap-2 pb-2">
          <KeyRound className={cn('w-5 h-5', TEXT.subtitle)} />
          <CardTitle className="text-lg">密码修改</CardTitle>
        </CardHeader>
        <CardContent>
          {message && (
            <div
              className={cn(
                'mb-4 p-3 rounded-lg text-sm',
                message.type === 'success' ? STATUS_CHIP.success : STATUS_CHIP.destructive,
              )}
            >
              {message.text}
            </div>
          )}

          <form onSubmit={handleChangePassword} className="space-y-4">
            <div>
              <label className={FORM.label}>当前密码</label>
              <input
                type="password"
                value={oldPassword}
                onChange={(e) => setOldPassword(e.target.value)}
                required
                className={FORM.input}
              />
            </div>
            <div>
              <label className={FORM.label}>新密码</label>
              <input
                type="password"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
                className={FORM.input}
              />
            </div>
            <div>
              <label className={FORM.label}>确认新密码</label>
              <input
                type="password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
                className={FORM.input}
              />
            </div>
            <Button type="submit" disabled={loading}>
              {loading ? '修改中...' : '确认修改'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </PageContainer>
  );
}
