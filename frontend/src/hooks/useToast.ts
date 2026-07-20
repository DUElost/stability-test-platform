import { toast as sonnerToast } from 'sonner';

export interface ToastPromiseOptions<T> {
  loading: string;
  success: string | ((data: T) => string);
  error: string | ((error: Error) => string);
}

export interface ToastActionOptions {
  label: string;
  onClick: () => void;
  duration?: number;
}

export interface ToastAPI {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  action: (message: string, options: ToastActionOptions) => void;
  promise: <T>(promise: Promise<T>, options: ToastPromiseOptions<T>) => Promise<T>;
}

export function useToast(): ToastAPI {
  return {
    success: (message: string) => sonnerToast.success(message, { duration: 3000 }),
    error: (message: string) => sonnerToast.error(message, { duration: Infinity }),
    info: (message: string) => sonnerToast.info(message, { duration: 4000 }),
    action: (message: string, options: ToastActionOptions) => sonnerToast.info(message, {
      duration: options.duration ?? 5000,
      action: { label: options.label, onClick: options.onClick },
    }),
    promise: async <T,>(promise: Promise<T>, options: ToastPromiseOptions<T>): Promise<T> => {
      sonnerToast.promise(promise, {
        loading: options.loading,
        success: options.success,
        error: options.error,
      });
      return promise;
    },
  };
}
