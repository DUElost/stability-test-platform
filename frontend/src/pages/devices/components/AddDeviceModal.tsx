import { useState, useEffect } from 'react';
import { X, Smartphone, Loader2, Tag } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { FORM, MODAL } from '@/design-system';
import { cn } from '@/lib/utils';

interface AddDeviceModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSubmit: (data: { serial: string; model?: string; host_id?: number; tags?: string[] }) => void;
  isSubmitting?: boolean;
}

export function AddDeviceModal({ isOpen, onClose, onSubmit, isSubmitting }: AddDeviceModalProps) {
  const [formData, setFormData] = useState({
    serial: '',
    model: '',
    host_id: '',
    tags: '',
  });
  const [errors, setErrors] = useState<Record<string, string>>({});

  useEffect(() => {
    if (isOpen) {
      setFormData({ serial: '', model: '', host_id: '', tags: '' });
      setErrors({});
    }
  }, [isOpen]);

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!formData.serial.trim()) newErrors.serial = '请输入设备序列号';
    if (formData.host_id && (!Number.isInteger(Number(formData.host_id)) || Number(formData.host_id) < 1)) {
      newErrors.host_id = '主机 ID 须为正整数';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    const data: { serial: string; model?: string; host_id?: number; tags?: string[] } = {
      serial: formData.serial.trim(),
    };
    if (formData.model.trim()) data.model = formData.model.trim();
    if (formData.host_id) data.host_id = Number(formData.host_id);
    if (formData.tags.trim()) {
      data.tags = formData.tags.split(',').map((t) => t.trim()).filter(Boolean);
    }
    onSubmit(data);
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
            <Smartphone className="text-primary" size={20} />
            <h2 className={MODAL.title}>添加设备</h2>
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
            <label htmlFor="device-serial" className={FORM.label}>
              序列号 <span className="text-destructive">*</span>
            </label>
            <input
              id="device-serial"
              type="text"
              value={formData.serial}
              onChange={(e) => setFormData({ ...formData, serial: e.target.value })}
              placeholder="例如：A1B2C3D4E5F6"
              className={fieldClass(!!errors.serial)}
              disabled={isSubmitting}
            />
            {errors.serial && <p className={FORM.error}>{errors.serial}</p>}
          </div>

          <div>
            <label htmlFor="device-model" className={FORM.label}>
              型号
            </label>
            <input
              id="device-model"
              type="text"
              value={formData.model}
              onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              placeholder="例如：SM-G991B（可选）"
              className={FORM.input}
              disabled={isSubmitting}
            />
          </div>

          <div>
            <label htmlFor="device-host" className={FORM.label}>
              主机 ID
            </label>
            <input
              id="device-host"
              type="number"
              min={1}
              value={formData.host_id}
              onChange={(e) => setFormData({ ...formData, host_id: e.target.value })}
              placeholder="例如：1（可选）"
              className={fieldClass(!!errors.host_id)}
              disabled={isSubmitting}
            />
            {errors.host_id && <p className={FORM.error}>{errors.host_id}</p>}
            <p className={FORM.hint}>将设备关联到指定主机</p>
          </div>

          <div>
            <label htmlFor="device-tags" className={FORM.label}>
              <span className="flex items-center gap-1">
                <Tag size={14} />
                标签
              </span>
            </label>
            <input
              id="device-tags"
              type="text"
              value={formData.tags}
              onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
              placeholder="例如：android、samsung、test-group（逗号分隔）"
              className={FORM.input}
              disabled={isSubmitting}
            />
            <p className={FORM.hint}>多个标签用英文逗号分隔</p>
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
                '添加设备'
              )}
            </Button>
          </div>
        </form>
      </div>
    </div>
  );
}
