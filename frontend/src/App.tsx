import React from 'react';
import AppRouter from './router';
import { QueryProvider } from './components/QueryProvider';
import { ToastProvider } from './components/ui/toast';
import { ConfirmProvider } from './hooks/useConfirm';
import { ErrorBoundary } from './components/ErrorBoundary';

const App: React.FC = () => {
  return (
    <ErrorBoundary>
      <QueryProvider>
        <ToastProvider>
          <ConfirmProvider>
            <AppRouter />
          </ConfirmProvider>
        </ToastProvider>
      </QueryProvider>
    </ErrorBoundary>
  );
};

export default App;
