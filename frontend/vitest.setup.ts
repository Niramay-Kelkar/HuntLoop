import "@testing-library/jest-dom/vitest";

// Work around a real environment conflict on this machine (and any
// Node >= 22 runtime with the native Storage API active): Node's own
// built-in `localStorage` global shadows jsdom's Storage implementation
// - `window.localStorage` ends up being Node's version, which lacks
// getItem/setItem/removeItem/clear entirely (confirmed directly:
// `typeof window.localStorage.clear` was "undefined", and
// `globalThis.localStorage === window.localStorage`). This silently
// breaks any code that reads/writes localStorage under test (it doesn't
// throw where the app code wraps calls in try/catch, like
// lib/draftSettings.ts does - it just always sees an empty store),
// which the earlier drafting-feature commit's tests never exercised
// directly. A minimal in-memory polyfill restores real getItem/setItem/
// removeItem/clear semantics for every test file, regardless of how
// vitest is invoked (avoids relying on a NODE_OPTIONS flag at the
// npm-script or CI level).
class MemoryStorage implements Storage {
  private store = new Map<string, string>();

  get length(): number {
    return this.store.size;
  }
  clear(): void {
    this.store.clear();
  }
  getItem(key: string): string | null {
    return this.store.has(key) ? this.store.get(key)! : null;
  }
  key(index: number): string | null {
    return Array.from(this.store.keys())[index] ?? null;
  }
  removeItem(key: string): void {
    this.store.delete(key);
  }
  setItem(key: string, value: string): void {
    this.store.set(key, String(value));
  }
}

if (typeof window !== "undefined" && typeof window.localStorage?.clear !== "function") {
  Object.defineProperty(window, "localStorage", {
    value: new MemoryStorage(),
    writable: true,
    configurable: true,
  });
}
