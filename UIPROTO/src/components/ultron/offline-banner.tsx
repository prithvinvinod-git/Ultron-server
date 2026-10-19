import { useEffect, useState } from 'react';

export function OfflineBanner() {
  const [online, setOnline] = useState(typeof navigator !== 'undefined' ? navigator.onLine : true);
  useEffect(() => {
    const handle = () => setOnline(navigator.onLine);
    window.addEventListener('online', handle);
    window.addEventListener('offline', handle);
    return () => {
      window.removeEventListener('online', handle);
      window.removeEventListener('offline', handle);
    };
  }, []);
  if (online) return null;
  return (
    <div style={{ position: 'fixed', bottom: 0, left: 0, right: 0, background: '#1a1a1a', color: '#ffb84d', padding: '0.5rem', textAlign: 'center', zIndex: 9999 }}>
      OFFLINE - Showing last known state (never stale-as-live)
    </div>
  );
}
