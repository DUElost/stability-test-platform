import '@testing-library/jest-dom';
import { vi } from 'vitest';

// Mock ResizeObserver which is used by Radix UI components
class ResizeObserverMock {
  observe = vi.fn();
  unobserve = vi.fn();
  disconnect = vi.fn();
}

window.ResizeObserver = ResizeObserverMock;

// Mock ScrollToOptions
window.scrollTo = vi.fn();
