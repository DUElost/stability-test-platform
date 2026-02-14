import '@testing-library/jest-dom';
import { vi, afterEach } from 'vitest';

// Mock ResizeObserver which is used by Radix UI components
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

window.ResizeObserver = ResizeObserverMock;

// Mock ScrollToOptions
window.scrollTo = vi.fn();

// Store WebSocket instances for testing
let wsInstances: WebSocketMock[] = [];

// Mock WebSocket for hook testing
class WebSocketMock {
  url: string;
  readyState: number = 0; // CONNECTING
  onopen: (() => void) | null = null;
  onclose: ((event: any) => void) | null = null;
  onmessage: ((event: any) => void) | null = null;
  onerror: ((error: any) => void) | null = null;
  send = vi.fn();
  close = vi.fn(function(this: WebSocketMock) {
    this.readyState = 3; // CLOSED
    if (this.onclose) this.onclose({ code: 1000, wasClean: true });
  });

  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;

  constructor(url: string) {
    this.url = url;
    wsInstances.push(this);
    // Don't auto-connect - let tests control this
  }

  // Helper method to simulate connection open
  simulateOpen() {
    this.readyState = 1; // OPEN
    if (this.onopen) this.onopen();
  }

  // Helper method to simulate receiving a message
  simulateMessage(data: any) {
    if (this.onmessage) {
      this.onmessage({ data: JSON.stringify(data) });
    }
  }

  // Helper method to simulate connection close
  simulateClose(code: number = 1006) {
    this.readyState = 3; // CLOSED
    if (this.onclose) {
      this.onclose({ code, wasClean: code === 1000 });
    }
  }

  // Helper method to simulate error
  simulateError() {
    this.readyState = 3; // CLOSED
    if (this.onerror) {
      this.onerror(new Error('WebSocket error'));
    }
  }
}

// Expose instances for tests
(globalThis as any).WebSocket = WebSocketMock;
(globalThis as any).getWebSocketInstances = () => wsInstances;
(globalThis as any).clearWebSocketInstances = () => {
  wsInstances = [];
};

// Clear instances after each test
afterEach(() => {
  wsInstances = [];
});
