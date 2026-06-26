import { useState, useEffect } from 'react';
import { X, Server, Loader2 } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { STATUS_TEXT_COLORS } from '@/design-system/colors';
import { FORM, MODAL } from '@/design-system';
import { cn } from '@/lib/utils';

interface AddHostModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { name: string; ip: string; ssh_port: number; ssh_user: string }) => void;
  isSubmitting?: boolean;
}

export function AddHostModal({ isOpen, onClose, onSubmit, isSubmitting }: AddHostModalProps) {
  const [formData, setFormData] = useState({
    name: '',
    ip: '',
    ssh_port: 22,
    ssh_user: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (isOpen) {
      setFormData({ name: '', ip: '', ssh_port: 22, ssh_user: '' });
      setErrors({});
    }
  }, [isOpen]);

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
    if (validate()) onSubmit(formData);
  };

  const handleClose = () => {
    if (!isSubmitting) onClose();
  };

  if (!isOpen) return null;

  const fieldClass = (hasError: boolean) =>
    cn(FORM.input, hasError && FORM.inputInvalid);

  return (
    <div className={MODAL.overlay}>
      <div className={MODAL.panel}>
        <div className={MODAL.header}>
          <div className="flex items-center gap-2">
            <Server className={STATUS_TEXT_COLORS.primary} size={20} />
            <h2 className={MODAL.title}>添加主机</h2>
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

        <form onSubmit={handleSubmit} className="space-y-4 p-6">
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
              placeholder="例如：192.168.1.100"
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
              placeholder="例如：admin（可选）"
              className={FORM.input}
              disabled={isSubmitting}
            />
          </div>

          <div className="flex justify-end gap-3 pt-4">
            <Button type="button" variant="outline" onClick={handleClose} disabled={isSubmitting}>
              取消
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? (
                <>
                  <Loader2 size={18} className="animate-spin" />
                  添加中…
                </>
              ) : (
                '添加主机'
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
