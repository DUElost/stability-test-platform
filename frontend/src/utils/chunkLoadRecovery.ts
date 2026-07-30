const PRELOAD_ERROR_EVENT = 'vite:preloadError';
const RECOVERY_ATTEMPT_KEY = 'stp.chunk-recovery-attempted-at';

export const CHUNK_RECOVERY_COOLDOWN_MS = 60_000;

interface ChunkLoadRecoveryOptions {
  target?: Pick<EventTarget, 'addEventListener' | 'removeEventListener'>;
  storage?: Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>;
  now?: () => number;
  reload?: () => void;
}

const CHUNK_LOAD_ERROR_PATTERNS: readonly RegExp[] = [
  /Failed to fetch dynamically imported module/i,
  /Importing a module script failed/i,
  /error loading dynamically imported module/i,
];

/**
 * Heuristically determine whether an error raised from a lazy `import()` is a
 * "deployment replaced my chunk" failure (HTTP 404 on a hashed asset) and not
 * a real application bug. We match by message because browsers like Chrome,
 * Firefox and Safari each word the failure differently and none expose a
 * stable `code`. The `vite:preloadError` event payload carries the same
 * underlying `Error` object, so this helper works for both paths.
 */
export function isChunkLoadError(error: unknown): boolean {
  if (error === null || typeof error !== 'object' || !('message' in error)) {
    return false;
  }
  const message = String((error as { message?: unknown }).message ?? '');
  if (!message) {
    return false;
  }
  return CHUNK_LOAD_ERROR_PATTERNS.some((pattern) => pattern.test(message));
}

/**
 * Reset the one-shot cooldown so that the next `vite:preloadError` can attempt
 * an automatic reload again. The cooldown exists to stop *automatic* reload
 * loops when the server itself is broken. When the user has taken an explicit
 * action (clicked the "刷新页面" button in ErrorBoundary, or run a manual
 * reload), they are signaling "I want to retry now" and must not be held off
 * by a guard designed for an unattended loop.
 */
export function clearChunkRecoveryAttempt(
  storage: Pick<Storage, 'removeItem'> = window.sessionStorage,
): void {
  try {
    storage.removeItem(RECOVERY_ATTEMPT_KEY);
  } catch {
    // Without session storage support the cooldown cannot be enforced anyway,
    // and registerChunkLoadRecovery already refuses to auto-reload. There is
    // nothing else we can do here, so swallow.
  }
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
