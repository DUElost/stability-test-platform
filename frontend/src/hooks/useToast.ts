import { useMemo } from 'react';
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
  // 引用必须跨渲染稳定：消费方把它放进 useCallback/useEffect 依赖数组，
  // 每渲染新对象会让依赖链上的 effect 反复重跑（#314：SchedulesPage 曾因此
  // 以网络往返速度无限重发请求）。回归兜底见 toast.test.tsx 引用稳定性用例。
  return useMemo(() => ({
    success: (message: string) => sonnerToast.success(message, { duration: 3000 }),
    error: (message: string) => sonnerToast.error(message, { duration: 10_000 }),
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
  }), []);
}
