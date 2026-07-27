const PRELOAD_ERROR_EVENT = 'vite:preloadError';
const RECOVERY_ATTEMPT_KEY = 'stp.chunk-recovery-attempted-at';

export const CHUNK_RECOVERY_COOLDOWN_MS = 60_000;

interface ChunkLoadRecoveryOptions {
  target?: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>;
  storage?: Pick<Storage, 'getItem' | 'setItem'>;
  now?: () => number;
  reload?: () => void;
}

/**
 * Recover an open tab after a deployment replaces its lazy-loaded chunks.
 *
 * Vite emits `vite:preloadError` when a dynamic import cannot be fetched. One
 * reload lets the browser obtain the new no-cache index.html. The timestamp
 * guard deliberately allows only one automatic attempt per minute so a real
 * network/server error cannot create a reload loop.
 */
export function registerChunkLoadRecovery(options: ChunkLoadRecoveryOptions = {}): () => void {
  const target = options.target ?? window;
  const storage = options.storage ?? window.sessionStorage;
  const now = options.now ?? Date.now;
  const reload = options.reload ?? (() => window.location.reload());

  const handlePreloadError: EventListener = (event) => {
    const attemptedAt = now();
    let previousAttempt: number | null = null;

    try {
      const raw = storage.getItem(RECOVERY_ATTEMPT_KEY);
      if (raw !== null) {
        const parsed = Number(raw);
        previousAttempt = Number.isFinite(parsed) ? parsed : null;
      }
    } catch {
      // Without the loop guard, an automatic reload would be unsafe.
      return;
    }

    if (
      previousAttempt !== null &&
      attemptedAt - previousAttempt < CHUNK_RECOVERY_COOLDOWN_MS
    ) {
      return;
    }

    try {
      storage.setItem(RECOVERY_ATTEMPT_KEY, String(attemptedAt));
    } catch {
      return;
    }

    event.preventDefault();
    reload();
  };

  target.addEventListener(PRELOAD_ERROR_EVENT, handlePreloadError);
  return () => target.removeEventListener(PRELOAD_ERROR_EVENT, handlePreloadError);
}
