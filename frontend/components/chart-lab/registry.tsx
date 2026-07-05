'use client';

/**
 * 차트 랩 버전 레지스트리
 * 각 버전의 메타(원본 프로젝트/파일/라이브러리/특징)와 자립형 컴포넌트를 매핑.
 */

import type { ComponentType } from 'react';
import { V1Crypto } from './v1-crypto';
import { V2GridOrder } from './v2-grid-order';
import { V3Tima } from './v3-tima';
import { V4Performance } from './v4-performance';
import { V5Backtest } from './v5-backtest';
import { V6Balance } from './v6-balance';
import { V7Reports } from './v7-reports';
import { V8ShortsgenBars } from './v8-shortsgen-bars';
import { V9ForceGraph } from './v9-forcegraph';
import { V10ShortsgenTrend } from './v10-shortsgen-trend';

export interface ChartVersion {
  id: number; // ?v=<id> 딥링크 키
  key: string; // v1 ~ v10
  project: string; // 원본 프로젝트명
  file: string; // 원본 파일 경로
  lib: string; // 라이브러리 뱃지
  title: string;
  oneLiner: string; // 한 줄 특징
  Component: ComponentType<any>;
  live?: boolean; // 실데이터(api) 사용 여부
}

export const CHART_VERSIONS: ChartVersion[] = [
  {
    id: 1,
    key: 'v1',
    project: 'nextjs-aibitgo',
    file: 'src/components/chart/crypto-chart.tsx',
    lib: 'lightweight-charts · Area',
    title: '크립토 에어리어',
    oneLiner: '종가 단일 라인 + 초록 그라디언트 필. 가장 미니멀한 시세 차트.',
    Component: V1Crypto,
    live: true,
  },
  {
    id: 2,
    key: 'v2',
    project: 'nextjs-aibitgo',
    file: 'src/components/trade/grid/grid-order-chart.tsx',
    lib: 'lightweight-charts · Candle',
    title: '그리드 주문선 캔들',
    oneLiner: '캔들 위에 Long/Short 그리드 주문선(점선) + 진입가(골드 실선) 오버레이.',
    Component: V2GridOrder,
    live: true,
  },
  {
    id: 3,
    key: 'v3',
    project: 'BarroAiTrade (현행)',
    file: 'components/dashboard/price-chart.tsx',
    lib: 'lightweight-charts · TIMA',
    title: '티마 전략 차트',
    oneLiner: '캔들 + 이평 5종 + 거래량 + 전략 기준선(SF/B/G/J) 자동 오버레이.',
    Component: V3Tima,
    live: true,
  },
  {
    id: 4,
    key: 'v4',
    project: 'nextjs-aibitgo',
    file: 'src/components/trade/grid/grid-performance-chart.tsx',
    lib: 'recharts · Area',
    title: '누적 수익률',
    oneLiner: '수익/손실 구간 색 반전 에어리어 + 시간축 토글 + 커스텀 툴팁.',
    Component: V4Performance,
  },
  {
    id: 5,
    key: 'v5',
    project: 'nextjs-aibitgo',
    file: 'src/components/trade/grid/grid-backtest-panel.tsx',
    lib: 'recharts · Area',
    title: '백테스트 Equity',
    oneLiner: '6개 결과 스탯 그리드 + Equity Curve(초기자본 기준선).',
    Component: V5Backtest,
  },
  {
    id: 6,
    key: 'v6',
    project: 'BarroAiTrade (현행)',
    file: 'app/(admin)/balance/page.tsx',
    lib: 'recharts · Area+Line',
    title: '잔고 자산 추이',
    oneLiner: '인디고 총자산 에어리어 + 초록 예수금 점선. 원화 축약 포맷.',
    Component: V6Balance,
  },
  {
    id: 7,
    key: 'v7',
    project: 'BarroAiTrade (현행)',
    file: 'app/(admin)/reports/page.tsx',
    lib: 'recharts · Composed',
    title: '매매 추이 리포트',
    oneLiner: '이중 Y축: 매매건수 막대 + 수익률 라인. 30일 활동 요약.',
    Component: V7Reports,
  },
  {
    id: 8,
    key: 'v8',
    project: 'BarroShopping (ShortsGen)',
    file: 'packages/frontend/src/dashboard.jsx',
    lib: 'CSS/HTML Bar (no d3)',
    title: 'ShortsGen 조회수 바',
    oneLiner: 'KPI 타일 + 핑크→앰버 그라디언트 가로 막대. 순수 CSS 차트.',
    Component: V8ShortsgenBars,
  },
  {
    id: 9,
    key: 'v9',
    project: 'BarroUs',
    file: 'src/components/graph/force-graph-canvas.tsx',
    lib: 'canvas force (d3 근사)',
    title: '지식 포스 그래프',
    oneLiner: '반발·스프링·중심 힘의 캔버스 포스 시뮬. 호버 이웃 강조.',
    Component: V9ForceGraph,
  },
  {
    id: 10,
    key: 'v10',
    project: 'ShortsGen (shortsgen-dashboard)',
    file: 'shortsgen/frontend/shortsgen-dashboard.jsx',
    lib: 'inline SVG Area (no d3)',
    title: 'ShortsGen 조회수 추이',
    oneLiner: '스카이블루 SVG 에어리어 7일 추이. v8 과 동일 대시보드의 다른 차트부.',
    Component: V10ShortsgenTrend,
  },
];
