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

// jsdom 缺 pointer capture + scrollIntoView（Radix Select/下拉在测试中依赖）
Element.prototype.hasPointerCapture = () => false;
Element.prototype.setPointerCapture = () => {};
Element.prototype.releasePointerCapture = () => {};
Element.prototype.scrollIntoView = () => {};
