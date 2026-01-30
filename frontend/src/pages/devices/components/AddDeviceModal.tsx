import { useState, useEffect } from 'react';
import { X, Smartphone, Loader2, Tag } from 'lucide-react';

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

  // Reset form when modal opens
  useEffect(() => {
    if (isOpen) {
      setFormData({ serial: '', model: '', host_id: '', tags: '' });
      setErrors({});
    }
  }, [isOpen]);

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!formData.serial.trim()) {
      newErrors.serial = 'Device serial is required';
    }

    if (formData.host_id && (!Number.isInteger(Number(formData.host_id)) || Number(formData.host_id) < 1)) {
      newErrors.host_id = 'Host ID must be a positive integer';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (validate()) {
      const data: { serial: string; model?: string; host_id?: number; tags?: string[] } = {
        serial: formData.serial.trim(),
      };

      if (formData.model.trim()) {
        data.model = formData.model.trim();
      }

      if (formData.host_id) {
        data.host_id = Number(formData.host_id);
      }

      if (formData.tags.trim()) {
        data.tags = formData.tags.split(',').map(t => t.trim()).filter(Boolean);
      }

      onSubmit(data);
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
            <Smartphone className="text-blue-600" size={20} />
            <h2 className="text-lg font-semibold text-slate-900">Add New Device</h2>
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
          {/* Serial */}
          <div>
            <label htmlFor="device-serial" className="block text-sm font-medium text-slate-700 mb-1">
              Serial Number <span className="text-red-500">*</span>
            </label>
            <input
              id="device-serial"
              type="text"
              value={formData.serial}
              onChange={(e) => setFormData({ ...formData, serial: e.target.value })}
              placeholder="e.g., A1B2C3D4E5F6"
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.serial ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.serial && <p className="mt-1 text-sm text-red-600">{errors.serial}</p>}
          </div>

          {/* Model */}
          <div>
            <label htmlFor="device-model" className="block text-sm font-medium text-slate-700 mb-1">
              Model
            </label>
            <input
              id="device-model"
              type="text"
              value={formData.model}
              onChange={(e) => setFormData({ ...formData, model: e.target.value })}
              placeholder="e.g., SM-G991B (optional)"
              className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
              disabled={isSubmitting}
            />
          </div>

          {/* Host ID */}
          <div>
            <label htmlFor="device-host" className="block text-sm font-medium text-slate-700 mb-1">
              Host ID
            </label>
            <input
              id="device-host"
              type="number"
              min={1}
              value={formData.host_id}
              onChange={(e) => setFormData({ ...formData, host_id: e.target.value })}
              placeholder="e.g., 1 (optional)"
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.host_id ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.host_id && <p className="mt-1 text-sm text-red-600">{errors.host_id}</p>}
            <p className="mt-1 text-xs text-slate-500">
              Associate this device with a specific host
            </p>
          </div>

          {/* Tags */}
          <div>
            <label htmlFor="device-tags" className="block text-sm font-medium text-slate-700 mb-1">
              <span className="flex items-center gap-1">
                <Tag size={14} />
                Tags
              </span>
            </label>
            <input
              id="device-tags"
              type="text"
              value={formData.tags}
              onChange={(e) => setFormData({ ...formData, tags: e.target.value })}
              placeholder="e.g., android, samsung, test-group (comma separated)"
              className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
              disabled={isSubmitting}
            />
            <p className="mt-1 text-xs text-slate-500">
              Separate multiple tags with commas
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
                  Adding...
                </>
              ) : (
                'Add Device'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
