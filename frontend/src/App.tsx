import React from 'react';
import AppRouter from './router';
import { QueryProvider } from './components/QueryProvider';

const App: React.FC = () => {
  return (
    <QueryProvider>
      <AppRouter />
    </QueryProvider>
  );
};

export default App;
