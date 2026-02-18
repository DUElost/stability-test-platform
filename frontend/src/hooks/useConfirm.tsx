import React, { createContext, useContext, useState, useCallback, useRef } from 'react';
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogCancel,
  AlertDialogAction,
} from '../components/ui/alert-dialog';
import { Button } from '../components/ui/button';

interface ConfirmOptions {
  title?: string;
  description: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'default' | 'destructive';
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn | null>(null);

export function useConfirm(): ConfirmFn {
  const ctx = useContext(ConfirmContext);
  if (!ctx) throw new Error('useConfirm must be used within ConfirmProvider');
  return ctx;
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false);
  const [options, setOptions] = useState<ConfirmOptions>({
    description: '',
  });
  const resolveRef = useRef<((value: boolean) => void) | null>(null);

  const confirm: ConfirmFn = useCallback((opts) => {
    setOptions(opts);
    setOpen(true);
    return new Promise<boolean>((resolve) => {
      resolveRef.current = resolve;
    });
  }, []);

  const handleConfirm = () => {
    setOpen(false);
    resolveRef.current?.(true);
    resolveRef.current = null;
  };

  const handleCancel = () => {
    setOpen(false);
    resolveRef.current?.(false);
    resolveRef.current = null;
  };

  return (
    <ConfirmContext.Provider value={confirm}>
      {children}
      <AlertDialog open={open} onOpenChange={(v) => { if (!v) handleCancel(); }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{options.title || '确认操作'}</AlertDialogTitle>
            <AlertDialogDescription>{options.description}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel asChild>
              <Button variant="outline" onClick={handleCancel}>
                {options.cancelText || '取消'}
              </Button>
            </AlertDialogCancel>
            <AlertDialogAction asChild>
              <Button
                variant={options.variant === 'destructive' ? 'destructive' : 'default'}
                onClick={handleConfirm}
              >
                {options.confirmText || '确认'}
              </Button>
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </ConfirmContext.Provider>
  );
}
