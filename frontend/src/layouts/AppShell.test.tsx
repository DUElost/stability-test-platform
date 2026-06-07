import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import { HeaderSlotProvider } from '@/contexts/HeaderSlotContext';
import AppShell from './AppShell';

const socketState = vi.hoisted(() => ({
  isConnected: false,
}));

vi.mock('@/hooks/useSocketIO', () => ({
  useSocketIO: () => ({
    isConnected: socketState.isConnected,
    connectionStatus: socketState.isConnected ? 'connected' : 'disconnected',
    lastMessage: null,
    sendMessage: vi.fn(),
    reconnectAttempt: 0,
    connect: vi.fn(),
    disconnect: vi.fn(),
  }),
  disconnectDashSocket: vi.fn(),
}));

vi.mock('@/hooks/useAuthSession', () => ({
  useAuthSession: () => ({
    data: { username: 'tester', role: 'ADMIN' },
  }),
}));

vi.mock('@/utils/api', () => ({
  api: {
    auth: {
      logout: vi.fn(),
    },
  },
}));

vi.mock('@/components/QueryProvider', () => ({
  clearAppQueryCache: vi.fn(),
}));

function renderShell() {
  return render(
    <HeaderSlotProvider>
      <MemoryRouter initialEntries={['/']}>
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route index element={<div>首页内容</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </HeaderSlotProvider>,
  );
}

describe('AppShell', () => {
  it('shows disconnected dashboard socket badge', () => {
    socketState.isConnected = false;
    renderShell();
    expect(screen.getByText('已断开')).toBeInTheDocument();
  });

  it('shows connected dashboard socket badge', () => {
    socketState.isConnected = true;
    renderShell();
    expect(screen.getByText('实时连接')).toBeInTheDocument();
  });
});
