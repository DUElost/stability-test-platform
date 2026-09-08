import { useState } from 'react';
import { Smartphone, Loader2, Tag } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { FORM } from '@/design-system';
import { cn } from '@/lib/utils';

interface AddDeviceModalProps {
  isOpen: boolean;
  onClose: () => void;
  // #953: Host.id 是字符串 PK（如 "192-168-1-200"）——host_id 不再收窄为
  // 正整数，提交原样字符串，存在性由后端校验。
  onSubmit: (data: { serial: string; model?: string; host_id?: string; tags?: string[] }) => void;
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

  const [prevOpen, setPrevOpen] = useState(isOpen);
  if (prevOpen !== isOpen) {
    setPrevOpen(isOpen);
    if (isOpen) {
      setFormData({ serial: '', model: '', host_id: '', tags: '' });
      setErrors({});
    }
  }

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};
    if (!formData.serial.trim()) newErrors.serial = '请输入设备序列号';
    if (formData.host_id.trim() && !/^[A-Za-z0-9._:-]+$/.test(formData.host_id.trim())) {
      newErrors.host_id = '主机 ID 格式不合法（字母/数字/._:-）';
    }
    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (!validate()) return;

    const data: { serial: string; model?: string; host_id?: string; tags?: string[] } = {
      serial: formData.serial.trim(),
    };
    if (formData.model.trim()) data.model = formData.model.trim();
    const hostId = formData.host_id.trim();
    if (hostId) data.host_id = hostId;
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
    <Dialog open={isOpen} onOpenChange={(open) => !open && handleClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Smartphone className="text-primary" size={20} />
            添加设备
          </DialogTitle>
          <DialogDescription>
            登记新设备到资源池，序列号为必填项
          </DialogDescription>
        </DialogHeader>

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label htmlFor="device-serial" className={FORM.label}>
              序列号 <span className="text-destructive">*</span>
            </label>
            <input
              id="device-serial"
              type="text"
              value={formData.serial}
              onChange={(e) => setFormData({ ...formData, serial: e.target.value })}
              placeholder="例如：A1B2C3******"
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
              type="text"
              value={formData.host_id}
              onChange={(e) => setFormData({ ...formData, host_id: e.target.value })}
              placeholder="例如：192-168-1-200（可选）"
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
      </DialogContent>
    </Dialog>
  );
}
