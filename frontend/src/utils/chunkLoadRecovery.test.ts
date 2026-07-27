import { describe, expect, it, vi } from 'vitest';
import { CHUNK_RECOVERY_COOLDOWN_MS, registerChunkLoadRecovery } from './chunkLoadRecovery';

function memoryStorage() {
  const values = new Map<string, string>();
  return {
    getItem: vi.fn((key: string) => values.get(key) ?? null),
    setItem: vi.fn((key: string, value: string) => values.set(key, value)),
  };
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
    };
    registerChunkLoadRecovery({ target, storage, reload });
    const event = new Event('vite:preloadError', { cancelable: true });

    target.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});
