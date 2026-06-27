import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { registerAuthFailureHandler } from './utils/api';
import { clearAppQueryCache } from './components/QueryProvider';
import { disconnectDashSocket } from './hooks/useSocketIO';

registerAuthFailureHandler(() => {
  clearAppQueryCache();
  disconnectDashSocket();
  window.location.href = '/login';
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
