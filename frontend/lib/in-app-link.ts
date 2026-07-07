export function toInAppHref(rawUrl?: string | null): string {
  if (!rawUrl) return '#';

  try {
    const url = new URL(rawUrl);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '#';
    return `/link?url=${encodeURIComponent(url.toString())}`;
  } catch {
    return '#';
  }
}

