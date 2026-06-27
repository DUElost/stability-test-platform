import React from 'react';
import AppRouter from './router';
import { QueryProvider } from './components/QueryProvider';
import { Toaster } from './components/ui/Toaster';
import { ConfirmProvider } from './hooks/useConfirm';
import { ErrorBoundary } from './components/ErrorBoundary';
import { HeaderSlotProvider } from './contexts/HeaderSlotContext';

const App: React.FC = () => {
  return (
    <ErrorBoundary>
      <QueryProvider>
        <ConfirmProvider>
          <HeaderSlotProvider>
            <AppRouter />
          </HeaderSlotProvider>
        </ConfirmProvider>
        <Toaster />
      </QueryProvider>
    </ErrorBoundary>
  );
};

export default App;
