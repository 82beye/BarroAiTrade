'use client';

/**
 * 차트 랩 v3 — 티마(현행) 가격 차트
 * 원본: frontend/components/dashboard/price-chart.tsx (현행 프로덕션 컴포넌트)
 * 라이브러리: lightweight-charts — 캔들 + 이평 5종 + 거래량 + 전략 기준선(createPriceLine)
 *
 * 재구현 금지 지침에 따라 기존 PriceChart 를 그대로 재사용(다크 테마).
 * 심볼별 /api/chart/levels 자동 조회로 전략 기준선(SF/B/G/J)이 오버레이됨.
 */

import { PriceChart } from '@/components/dashboard/price-chart';

export function V3Tima({ symbol = '005930' }: { symbol?: string }) {
  return <PriceChart defaultSymbol={symbol} defaultTimeframe="1d" theme="dark" hideControls height={420} />;
}
