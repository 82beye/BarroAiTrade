export const DAILY_LIMIT_CHANGE_PCT = 30;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

export function toDailyLimitScalePct(changePct?: number | null): number | null {
  if (changePct === null || changePct === undefined || !Number.isFinite(changePct)) {
    return null;
  }
  return clamp((changePct / DAILY_LIMIT_CHANGE_PCT) * 100, -100, 100);
}

export function formatSignedPct(value: number): string {
  const sign = value > 0 ? '+' : value < 0 ? '-' : '';
  return `${sign}${Math.abs(value).toFixed(2)}%`;
}

export function formatDailyLimitScalePct(changePct?: number | null): string {
  const scaled = toDailyLimitScalePct(changePct);
  return scaled === null ? '-' : formatSignedPct(scaled);
}
