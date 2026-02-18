import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import { X, CheckCircle2, XCircle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';

type ToastVariant = 'success' | 'error' | 'info';

interface Toast {
  id: number;
  message: string;
  variant: ToastVariant;
}

interface ToastContextValue {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
}

const ToastContext = createContext<ToastContextValue | null>(null);

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) throw new Error('useToast must be used within ToastProvider');
  return ctx;
}

const variantConfig: Record<ToastVariant, { icon: React.ElementType; bg: string; border: string; text: string }> = {
  success: { icon: CheckCircle2, bg: 'bg-emerald-50', border: 'border-emerald-200', text: 'text-emerald-800' },
  error: { icon: XCircle, bg: 'bg-red-50', border: 'border-red-200', text: 'text-red-800' },
  info: { icon: Info, bg: 'bg-blue-50', border: 'border-blue-200', text: 'text-blue-800' },
};

function ToastItem({ toast, onDismiss }: { toast: Toast; onDismiss: (id: number) => void }) {
  const config = variantConfig[toast.variant];
  const Icon = config.icon;

  return (
    <div
      className={cn(
        'flex items-start gap-3 px-4 py-3 rounded-lg border shadow-lg max-w-sm animate-in slide-in-from-right-5 fade-in duration-200',
        config.bg, config.border
      )}
    >
      <Icon className={cn('w-5 h-5 mt-0.5 shrink-0', config.text)} />
      <p className={cn('text-sm flex-1', config.text)}>{toast.message}</p>
      <button onClick={() => onDismiss(toast.id)} className="shrink-0 text-gray-400 hover:text-gray-600">
        <X className="w-4 h-4" />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const idRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setToasts(prev => prev.filter(t => t.id !== id));
  }, []);

  const addToast = useCallback((message: string, variant: ToastVariant) => {
    const id = ++idRef.current;
    setToasts(prev => [...prev, { id, message, variant }]);
    setTimeout(() => dismiss(id), 4000);
  }, [dismiss]);

  const value: ToastContextValue = {
    success: useCallback((msg: string) => addToast(msg, 'success'), [addToast]),
    error: useCallback((msg: string) => addToast(msg, 'error'), [addToast]),
    info: useCallback((msg: string) => addToast(msg, 'info'), [addToast]),
  };

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Toast container - fixed bottom-right */}
      <div className="fixed bottom-4 right-4 z-[100] flex flex-col gap-2">
        {toasts.map(t => (
          <ToastItem key={t.id} toast={t} onDismiss={dismiss} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}
