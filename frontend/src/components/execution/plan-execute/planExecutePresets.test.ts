import { describe, expect, it, beforeEach } from 'vitest';
import {
  PRESETS_STORAGE_KEY,
  addPreset,
  applyPresetIntersection,
  createPreset,
  deletePreset,
  loadPresets,
  persistPresets,
} from './planExecutePresets';

function memoryStorage(): Storage {
  const map = new Map<string, string>();
  return {
    get length() { return map.size; },
    clear: () => map.clear(),
    getItem: (k) => (map.has(k) ? map.get(k)! : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: (k) => { map.delete(k); },
    key: (i) => Array.from(map.keys())[i] ?? null,
  };
}

describe('planExecutePresets', () => {
  let storage: Storage;

  beforeEach(() => {
    storage = memoryStorage();
  });

  it('creates and loads presets from storage', () => {
    const created = addPreset('周五回归 · ELA 30 台', [1, 2, 3], storage);
    expect(created.name).toBe('周五回归 · ELA 30 台');
    expect(loadPresets(storage)).toEqual([created]);
  });

  it('rejects empty name or empty selection', () => {
    expect(() => createPreset('  ', [1])).toThrow(/名称/);
    expect(() => createPreset('ok', [])).toThrow(/样机/);
  });

  it('applies intersection and reports missing devices', () => {
    const result = applyPresetIntersection([1, 2, 99, 3], new Set([1, 3, 5]));
    expect(result.appliedIds).toEqual([1, 3]);
    expect(result.missingCount).toBe(2);
  });

  it('deletes a preset by id', () => {
    const a = addPreset('A', [1], storage);
    const b = addPreset('B', [2], storage);
    // B prepended
    expect(loadPresets(storage).map((p) => p.id)).toEqual([b.id, a.id]);
    deletePreset(b.id, storage);
    expect(loadPresets(storage).map((p) => p.id)).toEqual([a.id]);
  });

  it('ignores corrupt storage payloads', () => {
    storage.setItem(PRESETS_STORAGE_KEY, '{not-json');
    expect(loadPresets(storage)).toEqual([]);
    persistPresets([{ id: 'x', name: 'X', deviceIds: [1], createdAt: '2026-01-01T00:00:00Z' }], storage);
    expect(loadPresets(storage)[0]?.name).toBe('X');
  });
});
