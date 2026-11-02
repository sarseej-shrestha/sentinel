// Session storage is local draft recovery, never the committed decision authority.
export function readSession<T>(key: string, fallback: T): T {
  try {
    return (
      JSON.parse(sessionStorage.getItem("sentinel:" + key) ?? "null") ??
      fallback
    );
  } catch {
    return fallback;
  }
}
export function writeSession(key: string, value: unknown): boolean {
  try {
    sessionStorage.setItem("sentinel:" + key, JSON.stringify(value));
    return true;
  } catch {
    return false;
  }
}
