import { describe, expect, it } from 'vitest';
import {
  isEditableKeyboardTarget,
  isModKey,
  shouldHandleEnterPrimary,
  shouldHandleSelectAllShortcut,
} from './planExecuteKeyboard';

function keyEvent(
  key: string,
  opts: Partial<KeyboardEvent> & { target?: EventTarget | null } = {},
): KeyboardEvent {
  return {
    key,
    metaKey: false,
    ctrlKey: false,
    altKey: false,
    shiftKey: false,
    target: opts.target ?? document.body,
    ...opts,
  } as KeyboardEvent;
}

describe('planExecuteKeyboard', () => {
  it('detects editable targets', () => {
    const input = document.createElement('input');
    expect(isEditableKeyboardTarget(input)).toBe(true);
    expect(isEditableKeyboardTarget(document.body)).toBe(false);
  });

  it('handles Enter primary outside inputs', () => {
    expect(shouldHandleEnterPrimary(keyEvent('Enter'))).toBe(true);
    expect(shouldHandleEnterPrimary(keyEvent('Enter', { target: document.createElement('input') }))).toBe(false);
    expect(shouldHandleEnterPrimary(keyEvent('Enter'), { hasOpenDialog: true })).toBe(false);
  });

  it('handles Ctrl/Meta+A only in select phase within workspace', () => {
    const workspace = document.createElement('div');
    document.body.appendChild(workspace);
    workspace.appendChild(document.createElement('div'));
    workspace.focus?.();

    const withCtrl = keyEvent('a', { ctrlKey: true, target: workspace });
    expect(isModKey(withCtrl)).toBe(true);
    expect(shouldHandleSelectAllShortcut(withCtrl, { phase: 'select', workspace })).toBe(true);
    expect(shouldHandleSelectAllShortcut(withCtrl, { phase: 'plan', workspace })).toBe(false);
    expect(shouldHandleSelectAllShortcut(
      keyEvent('a', { ctrlKey: true, target: document.createElement('input') }),
      { phase: 'select', workspace },
    )).toBe(false);

    workspace.remove();
  });
});
