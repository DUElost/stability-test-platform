import React from 'react';
import AppRouter from './router';
import { QueryProvider } from './components/QueryProvider';
import { Toaster } from './components/ui/Toaster';
import { ConfirmProvider } from './hooks/useConfirm';
import { ErrorBoundary } from './components/ErrorBoundary';

const App: React.FC = () => {
  return (
    <ErrorBoundary>
      <QueryProvider>
        <ConfirmProvider>
          <AppRouter />
        </ConfirmProvider>
        <Toaster />
      </QueryProvider>
    </ErrorBoundary>
  );
};

export default App;
