import React from 'react';
import ReactDOM from 'react-dom/client';
import App from './App';
import './index.css';
import { registerAuthFailureHandler } from './utils/api';
import { clearAppQueryCache } from './components/QueryProvider';
import { disconnectDashSocket } from './hooks/useSocketIO';
import { registerChunkLoadRecovery } from './utils/chunkLoadRecovery';

// 字体声明走异步，不挡首屏渲染；理由见 ./fonts.ts
void import('./fonts');

registerChunkLoadRecovery();

registerAuthFailureHandler(() => {
  clearAppQueryCache();
  disconnectDashSocket();
  // 硬跳转无法携带 router state，深链经 ?next= 传给 LoginPage 回跳
  // （GUI 评测 2026-09-14：此前恒丢深链，登录后只能落首页）
  const next = encodeURIComponent(window.location.pathname + window.location.search);
  window.location.href = `/login?next=${next}`;
});

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
