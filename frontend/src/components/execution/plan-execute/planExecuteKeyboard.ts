/** Keyboard helpers for Plan Execute workspace (P5). */

export function isEditableKeyboardTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (target.isContentEditable) return true;
  return Boolean(target.closest('[contenteditable="true"]'));
}

export function isModKey(event: Pick<KeyboardEvent, 'metaKey' | 'ctrlKey'>): boolean {
  return event.metaKey || event.ctrlKey;
}

/**
 * Whether Ctrl/⌘+A should select all filtered devices.
 * Requires select phase + focus inside the workspace (or body when nothing focused).
 */
export function shouldHandleSelectAllShortcut(
  event: KeyboardEvent,
  opts: { phase: string; workspace: HTMLElement | null },
): boolean {
  if (opts.phase !== 'select') return false;
  if (!(event.key === 'a' || event.key === 'A')) return false;
  if (!isModKey(event)) return false;
  if (isEditableKeyboardTarget(event.target)) return false;
  if (!opts.workspace) return false;
  const active = document.activeElement;
  if (active && active !== document.body && !opts.workspace.contains(active)) return false;
  return true;
}

/** Enter triggers phase primary CTA when not typing in a field / open dialog. */
export function shouldHandleEnterPrimary(
  event: KeyboardEvent,
  opts: { hasOpenDialog?: boolean } = {},
): boolean {
  if (event.key !== 'Enter') return false;
  if (isModKey(event) || event.altKey || event.shiftKey) return false;
  if (isEditableKeyboardTarget(event.target)) return false;
  if (opts.hasOpenDialog) return false;
  // Buttons already handle Enter natively — avoid double-fire
  if (event.target instanceof HTMLButtonElement) return false;
  return true;
}
