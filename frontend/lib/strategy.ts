/**
 * 전략 메타(라벨·컬러) 공용 헬퍼 — 알림센터·스크리너·종목상세에서 재사용.
 * 색상은 PRD §2.3 전략 뱃지 규칙: F존 청록 · SF존 분홍 · 골드존 황색 · 38스윙 보라.
 */

export interface StrategyStyle {
  key: string;
  label: string;
  color: string; // 원형 뱃지·밑줄 컬러 (hex)
}

const STRATEGY_STYLES: Record<string, StrategyStyle> = {
  f_zone: { key: 'f_zone', label: 'F존', color: '#10B8A8' }, // 청록 (tima.teal)
  sf_zone: { key: 'sf_zone', label: 'SF존', color: '#D83870' }, // 분홍 (tima.select)
  gold_zone: { key: 'gold_zone', label: '골드존', color: '#E0C008' }, // 황색 (tima.active)
  swing_38: { key: 'swing_38', label: '38스윙', color: '#7B40C8' }, // 보라 (strategyLine.b3)
};

const FALLBACK: StrategyStyle = { key: '', label: '전략', color: '#94a3b8' };

export function strategyStyle(key?: string | null): StrategyStyle {
  if (!key) return FALLBACK;
  return STRATEGY_STYLES[key] ?? { ...FALLBACK, key, label: key };
}

/** 알림센터 필터 탭 순서 (전체 + 4종) */
export const STRATEGY_FILTERS: StrategyStyle[] = [
  STRATEGY_STYLES.f_zone,
  STRATEGY_STYLES.sf_zone,
  STRATEGY_STYLES.gold_zone,
  STRATEGY_STYLES.swing_38,
];
