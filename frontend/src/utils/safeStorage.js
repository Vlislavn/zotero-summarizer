// Browser storage can throw in privacy modes or when quota/security policies
// disable it. Keep optional preferences best-effort so routes still render.
export function readStorage(key, storageName = 'localStorage') {
  try {
    return typeof window === 'undefined' ? null : window[storageName]?.getItem(key) ?? null;
  } catch {
    return null;
  }
}

export function writeStorage(key, value, storageName = 'localStorage') {
  try {
    if (typeof window === 'undefined') return false;
    window[storageName]?.setItem(key, value);
    return true;
  } catch {
    return false;
  }
}

export function removeStorage(key, storageName = 'localStorage') {
  try {
    if (typeof window === 'undefined') return false;
    window[storageName]?.removeItem(key);
    return true;
  } catch {
    return false;
  }
}

export function readStoredJson(key, fallback, isValid = () => true, storageName = 'localStorage') {
  try {
    const raw = readStorage(key, storageName);
    if (raw == null) return fallback;
    const value = JSON.parse(raw);
    return isValid(value) ? value : fallback;
  } catch {
    return fallback;
  }
}
