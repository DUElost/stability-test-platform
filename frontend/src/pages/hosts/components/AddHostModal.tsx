import { useState } from 'react';
import { Server, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { FORM } from '@/design-system';
import { cn } from '@/lib/utils';
import type { Host } from '@/utils/api/types';

interface AddHostModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: {
    name: string;
    ip: string;
    ssh_port: number;
    ssh_user: string;
    ssh_password?: string | null;
  }) => void;
  isSubmitting?: boolean;
  /** 编辑模式：传入现有主机则预填，标题改为"编辑主机"。 */
  editingHost?: Host | null;
}

export function AddHostModal({ isOpen, onClose, onSubmit, isSubmitting, editingHost }: AddHostModalProps) {
  const isEdit = !!editingHost;
  const [formData, setFormData] = useState({
    name: '',
    ip: '',
    ssh_port: 22,
    ssh_user: '',
    ssh_password: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  // React 官方"adjust state when prop changes"模式：render 期对比 prev 快照并重置表单。
  // 用「打开状态 + 编辑对象 id」作比较键（id 而非对象引用），避免父组件每次重渲染
  // 传新 editingHost 引用而误触 reset。
  const [prevOpen, setPrevOpen] = useState(isOpen);
  const [prevEditingId, setPrevEditingId] = useState<string | null>(editingHost?.id ?? null);
  if (prevOpen !== isOpen || prevEditingId !== (editingHost?.id ?? null)) {
    setPrevOpen(isOpen);
    setPrevEditingId(editingHost?.id ?? null);
    if (isOpen) {
      if (editingHost) {
        setFormData({
          name: editingHost.name ?? '',
          ip: editingHost.ip ?? '',
          ssh_port: editingHost.ssh_port ?? 22,
          ssh_user: editingHost.ssh_user ?? '',
          ssh_password: '', // 编辑时密码留空表示不改
        });
      } else {
        setFormData({ name: '', ip: '', ssh_port: 22, ssh_user: '', ssh_password: '' });
      }
      setErrors({});
    }
  }

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!formData.name.trim()) newErrors.name = '请输入主机名称';
    if (!formData.ip.trim()) {
      newErrors.ip = '请输入 IP 地址';
    } else if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(formData.ip)) {
      newErrors.ip = 'IP 地址格式不正确';
    }
    if (formData.ssh_port < 1 || formData.ssh_port > 65535) {
      newErrors.ssh_port = '端口须在 1–65535 之间';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (validate()) {
      // 编辑模式且密码留空 → 不传 ssh_password（后端保持原密码）
      const payload: typeof formData = { ...formData };
      if (isEdit && !payload.ssh_password) {
        const { ssh_password: _ssh_password, ...rest } = payload;
        onSubmit(rest);
      } else {
        onSubmit(payload);
      }
    }
  };

  const handleClose = () => {
    // C7：提交中禁止关闭（与 AddDeviceModal 一致），防止提交中途关窗导致状态混乱
    if (!isSubmitting) onClose();
  };

  if (!isOpen) return null;

  const fieldClass = (hasError: boolean) =>
    cn(FORM.input, hasError && FORM.inputInvalid);

  return (
    <Dialog open={isOpen} onOpenChange={(open) => { if (!open) handleClose(); }}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Server className={STATUS_TEXT_COLORS.primary} size={20} />
            {isEdit ? '编辑主机' : '添加主机'}
          </DialogTitle>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="host-name" className={FORM.label}>
              主机名称 <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <input
              id="host-name"
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="例如：测试机 01"
              className={fieldClass(!!errors.name)}
              disabled={isSubmitting}
            />
            {errors.name && <p className={FORM.error}>{errors.name}</p>}
          </div>

          <div>
            <label htmlFor="host-ip" className={FORM.label}>
              IP 地址 <span className={STATUS_TEXT_COLORS.error}>*</span>
            </label>
            <input
              id="host-ip"
              type="text"
              value={formData.ip}
              onChange={(e) => setFormData({ ...formData, ip: e.target.value })}
              placeholder="例如：192.0.2.100"
              className={fieldClass(!!errors.ip)}
              disabled={isSubmitting}
            />
            {errors.ip && <p className={FORM.error}>{errors.ip}</p>}
          </div>

          <div>
            <label htmlFor="host-port" className={FORM.label}>
              SSH 端口
            </label>
            <input
              id="host-port"
              type="number"
              min={1}
              max={65535}
              value={formData.ssh_port}
              onChange={(e) => setFormData({ ...formData, ssh_port: parseInt(e.target.value) || 22 })}
              className={fieldClass(!!errors.ssh_port)}
              disabled={isSubmitting}
            />
            {errors.ssh_port && <p className={FORM.error}>{errors.ssh_port}</p>}
          </div>

          <div>
            <label htmlFor="host-user" className={FORM.label}>
              SSH 用户
            </label>
            <input
              id="host-user"
              type="text"
              value={formData.ssh_user}
              onChange={(e) => setFormData({ ...formData, ssh_user: e.target.value })}
              placeholder="例如：android（可选）"
              className={FORM.input}
              disabled={isSubmitting}
            />
          </div>

          <div>
            <label htmlFor="host-password" className={FORM.label}>
              SSH 密码
            </label>
            <input
              id="host-password"
              type="password"
              value={formData.ssh_password}
              onChange={(e) => setFormData({ ...formData, ssh_password: e.target.value })}
              placeholder={isEdit ? '留空表示不修改密码' : '首次安装/热更新时用于 SSH 认证'}
              className={FORM.input}
              disabled={isSubmitting}
              autoComplete="new-password"
            />
            <p className="mt-1 text-xs text-muted-foreground">
              密码经 Fernet 加密存入数据库，用于首次安装（ansible become）与热更新（paramiko）。
            </p>
          </div>

          <div className="flex justify-end gap-3 pt-4">
            <Button type="button" variant="outline" onClick={handleClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  {isEdit ? '保存中…' : '添加中…'}
                </>
              ) : (
                isEdit ? '保存修改' : '添加主机'
              )}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}
