import { useState, useEffect } from 'react';
import { X, Server, Loader2 } from 'lucide-react';

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

  // Reset form when modal opens
  useEffect(() => {
    if (isOpen) {
      setFormData({ name: '', ip: '', ssh_port: 22, ssh_user: '' });
      setErrors({});
    }
  }, [isOpen]);

  const validate = (): boolean => {
    const newErrors: Record<string, string> = {};

    if (!formData.name.trim()) {
      newErrors.name = 'Host name is required';
    }

    if (!formData.ip.trim()) {
      newErrors.ip = 'IP address is required';
    } else if (!/^(\d{1,3}\.){3}\d{1,3}$/.test(formData.ip)) {
      newErrors.ip = 'Invalid IP address format';
    }

    if (formData.ssh_port < 1 || formData.ssh_port > 65535) {
      newErrors.ssh_port = 'Port must be between 1 and 65535';
    }

    setErrors(newErrors);
    return Object.keys(newErrors).length === 0;
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (validate()) {
      onSubmit(formData);
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
            <Server className="text-blue-600" size={20} />
            <h2 className="text-lg font-semibold text-slate-900">Add New Host</h2>
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
          {/* Name */}
          <div>
            <label htmlFor="host-name" className="block text-sm font-medium text-slate-700 mb-1">
              Host Name <span className="text-red-500">*</span>
            </label>
            <input
              id="host-name"
              type="text"
              value={formData.name}
              onChange={(e) => setFormData({ ...formData, name: e.target.value })}
              placeholder="e.g., Test Server 01"
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.name ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.name && <p className="mt-1 text-sm text-red-600">{errors.name}</p>}
          </div>

          {/* IP Address */}
          <div>
            <label htmlFor="host-ip" className="block text-sm font-medium text-slate-700 mb-1">
              IP Address <span className="text-red-500">*</span>
            </label>
            <input
              id="host-ip"
              type="text"
              value={formData.ip}
              onChange={(e) => setFormData({ ...formData, ip: e.target.value })}
              placeholder="e.g., 192.168.1.100"
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.ip ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.ip && <p className="mt-1 text-sm text-red-600">{errors.ip}</p>}
          </div>

          {/* SSH Port */}
          <div>
            <label htmlFor="host-port" className="block text-sm font-medium text-slate-700 mb-1">
              SSH Port
            </label>
            <input
              id="host-port"
              type="number"
              min={1}
              max={65535}
              value={formData.ssh_port}
              onChange={(e) => setFormData({ ...formData, ssh_port: parseInt(e.target.value) || 22 })}
              className={`w-full px-3 py-2 border rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all ${
                errors.ssh_port ? 'border-red-300' : 'border-slate-300'
              }`}
              disabled={isSubmitting}
            />
            {errors.ssh_port && <p className="mt-1 text-sm text-red-600">{errors.ssh_port}</p>}
          </div>

          {/* SSH User */}
          <div>
            <label htmlFor="host-user" className="block text-sm font-medium text-slate-700 mb-1">
              SSH User
            </label>
            <input
              id="host-user"
              type="text"
              value={formData.ssh_user}
              onChange={(e) => setFormData({ ...formData, ssh_user: e.target.value })}
              placeholder="e.g., admin (optional)"
              className="w-full px-3 py-2 border border-slate-300 rounded-md focus:outline-none focus:ring-2 focus:ring-blue-500 transition-all"
              disabled={isSubmitting}
            />
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
                'Add Host'
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
