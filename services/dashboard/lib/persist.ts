"use client";

import { useEffect, useState } from "react";

/**
 * useState that survives a reload of the tab.
 *
 * Kept in sessionStorage: per tab, gone when the tab closes, never shared with
 * another tab or account. Restored after mount rather than during render, so
 * the server-rendered HTML and the first client render agree. Never hand it a
 * password.
 */
export function usePersisted<T>(key: string, initial: T, restore = true) {
  const [value, setValue] = useState<T>(initial);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    if (restore) {
      try {
        const raw = sessionStorage.getItem(key);
        if (raw !== null) setValue(JSON.parse(raw) as T);
      } catch {
        // storage unavailable or unreadable: keep the default
      }
    }
    setReady(true);
  }, [key, restore]);

  useEffect(() => {
    if (!ready) return;
    try {
      sessionStorage.setItem(key, JSON.stringify(value));
    } catch {
      // storage full or blocked: the page still works, it just forgets on reload
    }
  }, [key, value, ready]);

  return [value, setValue] as const;
}
