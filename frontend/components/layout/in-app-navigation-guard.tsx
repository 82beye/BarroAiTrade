'use client';

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';

function findAnchor(target: EventTarget | null): HTMLAnchorElement | null {
  if (!(target instanceof Element)) return null;
  return target.closest('a[href]');
}

function isInternalAppUrl(url: URL): boolean {
  if (url.origin !== window.location.origin) return false;
  if (url.pathname.startsWith('/api/')) return false;
  if (url.pathname.startsWith('/_next/')) return false;
  return true;
}

function isStandaloneWebApp(): boolean {
  const navigatorWithStandalone = window.navigator as Navigator & { standalone?: boolean };
  return (
    navigatorWithStandalone.standalone === true ||
    window.matchMedia?.('(display-mode: standalone)').matches === true ||
    window.matchMedia?.('(display-mode: fullscreen)').matches === true
  );
}

export function InAppNavigationGuard() {
  const router = useRouter();

  useEffect(() => {
    const handleClick = (event: MouseEvent) => {
      if (event.defaultPrevented) return;
      if (event.button !== 0) return;
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;

      const anchor = findAnchor(event.target);
      if (!anchor) return;
      if (anchor.hasAttribute('download')) return;
      if (anchor.target && anchor.target.toLowerCase() !== '_self') return;
      if ((anchor.getAttribute('rel') ?? '').split(/\s+/).includes('external')) return;

      let url: URL;
      try {
        url = new URL(anchor.href, window.location.href);
      } catch {
        return;
      }

      if (!isInternalAppUrl(url)) return;

      event.preventDefault();
      if (isStandaloneWebApp()) {
        window.location.href = url.href;
        return;
      }

      router.push(`${url.pathname}${url.search}${url.hash}`);
    };

    document.addEventListener('click', handleClick, true);
    return () => document.removeEventListener('click', handleClick, true);
  }, [router]);

  return null;
}
