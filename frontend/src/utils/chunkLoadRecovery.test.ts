import { describe, expect, it, vi } from 'vitest';
import {
  CHUNK_RECOVERY_COOLDOWN_MS,
  clearChunkRecoveryAttempt,
  isChunkLoadError,
  registerChunkLoadRecovery,
} from './chunkLoadRecovery';

function memoryStorage() {
  const values = new Map<string, string>();
  const storage = {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => void values.set(key, value)),
    removeItem: vi.fn((key: string) => void values.delete(key)),
  };
  return { ...storage, values };
}

describe('registerChunkLoadRecovery', () => {
  it('prevents the failed import and reloads once', () => {
    const target = new EventTarget();
    const storage = memoryStorage();
    const reload = vi.fn();
    const unregister = registerChunkLoadRecovery({
      target,
      storage,
      now: () => 10_000,
      reload,
    });
    const event = new Event('vite:preloadError', { cancelable: true });

    target.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
    expect(storage.setItem).toHaveBeenCalledWith(
      'stp.chunk-recovery-attempted-at',
      '10000'
    );
    unregister();
  });

  it('does not enter a reload loop during the cooldown', () => {
    const target = new EventTarget();
    const storage = memoryStorage();
    const reload = vi.fn();
    let now = 10_000;
    registerChunkLoadRecovery({ target, storage, now: () => now, reload });

    target.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    now += CHUNK_RECOVERY_COOLDOWN_MS - 1;
    const repeatedEvent = new Event('vite:preloadError', { cancelable: true });
    target.dispatchEvent(repeatedEvent);

    expect(reload).toHaveBeenCalledOnce();
    expect(repeatedEvent.defaultPrevented).toBe(false);
  });

  it('leaves the error visible when session storage is unavailable', () => {
    const target = new EventTarget();
    const reload = vi.fn();
    const storage = {
      getItem: vi.fn(() => {
        throw new Error('storage disabled');
      }),
      setItem: vi.fn(),
      removeItem: vi.fn(),
    };
    registerChunkLoadRecovery({ target, storage, reload });
    const event = new Event('vite:preloadError', { cancelable: true });

    target.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe('clearChunkRecoveryAttempt', () => {
  it('removes the cooldown marker so the next preload error can auto-reload', () => {
    const storage = memoryStorage();
    storage.setItem('stp.chunk-recovery-attempted-at', '10000');

    clearChunkRecoveryAttempt(storage);

    expect(storage.removeItem).toHaveBeenCalledWith('stp.chunk-recovery-attempted-at');
    expect(storage.values.has('stp.chunk-recovery-attempted-at')).toBe(false);
  });

  it('rearms auto-reload after the user has manually cleared the cooldown', () => {
    const target = new EventTarget();
    const storage = memoryStorage();
    const reload = vi.fn();
    let now = 10_000;
    registerChunkLoadRecovery({ target, storage, now: () => now, reload });

    target.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    now += 1_000;
    target.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    expect(reload).toHaveBeenCalledOnce();

    clearChunkRecoveryAttempt(storage);
    now += 1_000;
    target.dispatchEvent(new Event('vite:preloadError', { cancelable: true }));
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('swallows when session storage throws', () => {
    const storage = {
      removeItem: vi.fn(() => {
        throw new Error('blocked');
      }),
    };
    expect(() => clearChunkRecoveryAttempt(storage)).not.toThrow();
    expect(storage.removeItem).toHaveBeenCalled();
  });
});

describe('isChunkLoadError', () => {
  it('matches the Chrome failure message', () => {
    expect(
      isChunkLoadError(
        new Error('Failed to fetch dynamically imported module: http://x/assets/Foo-Abc.js')
      )
    ).toBe(true);
  });

  it('matches the Firefox failure message', () => {
    expect(
      isChunkLoadError(new Error('Importing a module script failed.'))
    ).toBe(true);
  });

  it('matches the Safari failure message', () => {
    expect(
      isChunkLoadError(
        new Error('error loading dynamically imported module: /assets/Foo-Abc.js')
      )
    ).toBe(true);
  });

  it('rejects unrelated errors and non-error payloads', () => {
    expect(isChunkLoadError(new Error('Cannot read properties of undefined'))).toBe(false);
    expect(isChunkLoadError(null)).toBe(false);
    expect(isChunkLoadError(undefined)).toBe(false);
    expect(isChunkLoadError({ message: 'something else' })).toBe(false);
    expect(isChunkLoadError({})).toBe(false);
  });
});
