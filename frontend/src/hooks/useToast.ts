import { toast as sonnerToast } from 'sonner';

export interface ToastPromiseOptions<T> {
  loading: string;
  success: string | ((data: T) => string);
  error: string | ((error: Error) => string);
}

export interface ToastAPI {
  success: (message: string) => void;
  error: (message: string) => void;
  info: (message: string) => void;
  promise: <T>(promise: Promise<T>, options: ToastPromiseOptions<T>) => Promise<T>;
}

export function useToast(): ToastAPI {
  return {
    success: (message: string) => sonnerToast.success(message, { duration: 3000 }),
    error: (message: string) => sonnerToast.error(message, { duration: Infinity }),
    info: (message: string) => sonnerToast.info(message, { duration: 4000 }),
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
