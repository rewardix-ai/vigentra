import { useEffect, useRef } from "react";

/**
 * Make the browser's Back button close an overlay (the full-screen wall, a focused feed) instead of
 * leaving the page. Opening it adds one history entry on the same URL; Back pops that entry and
 * calls `close`; closing it any other way (Esc, a button) removes the entry again, so history
 * stays as the user left it. Without this, Back from the full-screen wall jumped to whatever page
 * was open before the live wall (9 Oct: the federated events page).
 */
export function useBackCloses(open: boolean, close: () => void) {
  const closeRef = useRef(close);
  closeRef.current = close;

  useEffect(() => {
    if (!open) return;
    const id = `${Date.now()}-${Math.random()}`;
    // keep Next.js's own state on the entry, so its router treats the pop as the same page
    window.history.pushState({ ...(window.history.state ?? {}), vigentraOverlay: id }, "");
    let popped = false;
    const onPop = () => {
      popped = true;
      closeRef.current();
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      if (!popped && window.history.state?.vigentraOverlay === id) window.history.back();
    };
  }, [open]);
}
