import { render, screen, fireEvent } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ErrorBoundary } from './ErrorBoundary';

const CHUNK_RECOVERY_KEY = 'stp.chunk-recovery-attempted-at';

function ThrowChild({ error }: { error: Error }): ReactNode {
  throw error;
}

describe('ErrorBoundary', () => {
  let reload: ReturnType<typeof vi.fn>;
  let removeItemSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    const storage = window.sessionStorage;
    storage.clear();
    removeItemSpy = vi.fn((key: string) => storage.removeItem(key));
    const stubbedStorage = {
      getItem: (key: string) => storage.getItem(key),
      setItem: (key: string, value: string) => storage.setItem(key, value),
      removeItem: removeItemSpy,
      clear: () => storage.clear(),
      key: (index: number) => storage.key(index),
      get length() {
        return storage.length;
      },
    } as Storage;
    vi.stubGlobal('sessionStorage', stubbedStorage);
    reload = vi.fn();
    vi.stubGlobal('location', { ...window.location, reload });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('renders the generic error page for unrelated errors and skips clearing the cooldown', () => {
    render(
      <ErrorBoundary>
        <ThrowChild error={new Error('Cannot read properties of undefined')} />
      </ErrorBoundary>
    );

    expect(screen.getByText('页面出错了')).toBeInTheDocument();
    expect(
      screen.getByText('抱歉，页面遇到了意外错误。请尝试刷新页面。')
    ).toBeInTheDocument();
    expect(
      screen.getByText('Cannot read properties of undefined')
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText('刷新页面'));

    expect(reload).toHaveBeenCalledOnce();
    expect(removeItemSpy).not.toHaveBeenCalledWith(CHUNK_RECOVERY_KEY);
  });

  it('renders the dedicated copy on chunk-load failure and clears the cooldown before reload', () => {
    window.sessionStorage.setItem(CHUNK_RECOVERY_KEY, '10000');
    render(
      <ErrorBoundary>
        <ThrowChild
          error={new Error(
            'Failed to fetch dynamically imported module: http://172.21.15.253/assets/PlanListPage-CiyN-NnY.js'
          )}
        />
      </ErrorBoundary>
    );

    expect(screen.getByText('页面需要刷新以加载最新版本')).toBeInTheDocument();
    expect(
      screen.getByText(
        '检测到当前页面所需资源已被新版本替换。请点击下方按钮刷新，加载完成后即可正常访问。'
      )
    ).toBeInTheDocument();

    fireEvent.click(screen.getByText('刷新页面'));

    expect(reload).toHaveBeenCalledOnce();
    expect(removeItemSpy).toHaveBeenCalledWith(CHUNK_RECOVERY_KEY);
  });
});
