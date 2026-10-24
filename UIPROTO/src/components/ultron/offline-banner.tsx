import { useEffect, useRef, useState } from 'react';

/**
 * Honest connectivity banner.
 *
 * The browser's `navigator.onLine` is a guess at *internet* connectivity, and it
 * is routinely wrong for a local app: a machine talking only to a localhost
 * backend, a VPN, or a blocked OS connectivity probe all report `false` while
 * ULTRON is perfectly reachable. Flagging that as "OFFLINE" is the opposite of
 * the "never stale-as-live" rule it is trying to honour.
 *
 * So the banner is driven by the thing that actually matters: whether the
 * backend answers. `/health` is the cheap liveness probe — no database, no
 * dependencies — and a failure to reach it is the real "showing last known
 * state" signal. `navigator.onLine` is kept only as a fast negative hint (a
 * definite `false` short-circuits without waiting out the fetch timeout).
 */
const POLL_MS = 8000;
const PROBE_TIMEOUT_MS = 4000;

async function backendReachable(): Promise<boolean> {
  if (typeof navigator !== 'undefined' && !navigator.onLine) return false;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
  try {
    const res = await fetch('/health', { signal: controller.signal, cache: 'no-store' });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timer);
  }
}

export function OfflineBanner() {
  const [offline, setOffline] = useState(false);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;

    const check = async () => {
      const ok = await backendReachable();
      if (!mounted.current) return;
      setOffline(!ok);
      timer = setTimeout(check, POLL_MS);
    };

    const onNetworkChange = () => void check();
    window.addEventListener('online', onNetworkChange);
    window.addEventListener('offline', onNetworkChange);
    void check();

    return () => {
      mounted.current = false;
      clearTimeout(timer);
      window.removeEventListener('online', onNetworkChange);
      window.removeEventListener('offline', onNetworkChange);
    };
  }, []);

  if (!offline) return null;
  return (
    <div
      style={{
        position: 'fixed',
        bottom: 0,
        left: 0,
        right: 0,
        background: '#1a1a1a',
        color: '#ffb84d',
        padding: '0.5rem',
        textAlign: 'center',
        zIndex: 9999,
      }}
    >
      BACKEND UNREACHABLE - Showing last known state (never stale-as-live)
    </div>
  );
}
