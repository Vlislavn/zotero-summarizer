// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readStoredJson, readStorage, removeStorage, writeStorage } from './safeStorage.js';

afterEach(() => vi.unstubAllGlobals());

describe('safeStorage', () => {
  it('keeps reads, writes, and JSON shape validation optional when storage is blocked', () => {
    const blocked = { getItem: () => { throw new DOMException('Blocked', 'SecurityError'); },
      setItem: () => { throw new DOMException('Blocked', 'SecurityError'); } };
    vi.stubGlobal('localStorage', blocked);

    expect(readStorage('key')).toBeNull();
    expect(writeStorage('key', 'value')).toBe(false);
    expect(removeStorage('key')).toBe(false);
    expect(readStoredJson('order', [])).toEqual([]);
  });

  it('falls back when stored JSON has the wrong shape', () => {
    vi.stubGlobal('localStorage', { getItem: () => '{"unexpected":true}' });

    expect(readStoredJson('order', [], Array.isArray)).toEqual([]);
  });
});
