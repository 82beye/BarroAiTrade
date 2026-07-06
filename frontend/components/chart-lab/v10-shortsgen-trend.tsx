'use client';

/**
 * 차트 랩 v10 — ShortsGen 분석: 7일 조회수 추이 (SVG 에어리어)
 * 원본: /Users/beye/Documents/Claude/Projects/바로쇼핑/shortsgen/frontend/shortsgen-dashboard.jsx
 *       (Analytics 뷰의 AreaChart — 차트 부분만 추출)
 * 라이브러리: 순수 인라인 SVG (polygon 필 + polyline 라인 + 원점 마커, d3 미사용)
 *
 * 원본 시각 스타일 보존:
 *   스카이블루(#38BDF8) 라인/필 그라디언트(위 0.45 → 아래 0),
 *   원점 마커(fill #0B0E14, stroke #38BDF8), viewBox 스케일 렌더.
 * 데이터는 정적 샘플(원본 trend 시드값 [62,78,71,96,88,124,142]).
 * 참고: 원본 소스는 v8(BarroShopping)의 shortsgen-dashboard.jsx 와 동일 내용이며,
 *       본 카드는 AreaChart+요약, v8 은 BarChart 를 담당(같은 대시보드의 다른 차트부).
 */

const TREND = [62, 78, 71, 96, 88, 124, 142]; // 일별 조회수(천)
const DAYS = ['월', '화', '수', '목', '금', '토', '일'];

export function V10ShortsgenTrend() {
  const w = 420;
  const h = 180;
  const pad = 12;
  const max = Math.max(...TREND);
  const min = Math.min(...TREND);
  const x = (i: number) => pad + (i * (w - pad * 2)) / (TREND.length - 1);
  const y = (v: number) => h - pad - ((v - min) / (max - min || 1)) * (h - pad * 2 - 10);
  const line = TREND.map((v, i) => `${x(i)},${y(v)}`).join(' ');
  const area = `${x(0)},${h - pad} ${line} ${x(TREND.length - 1)},${h - pad}`;

  return (
    <div className="rounded-xl border border-[#26303F] bg-[#0B0E14] p-5">
      <div className="rounded-xl border border-[#26303F] bg-[#141A24] p-5">
        <div className="mb-1 text-sm font-semibold text-[#E7ECF4]">최근 7일 조회수 추이 (천)</div>
        <div className="mb-4 text-xs text-[#8A97AD]">
          누적 {TREND.reduce((a, b) => a + b, 0)}천 · 피크 {max}천
        </div>
        <svg viewBox={`0 0 ${w} ${h}`} preserveAspectRatio="none" className="h-44 w-full">
          <defs>
            <linearGradient id="v10ag" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#38BDF8" stopOpacity="0.45" />
              <stop offset="100%" stopColor="#38BDF8" stopOpacity="0" />
            </linearGradient>
          </defs>
          <polygon points={area} fill="url(#v10ag)" />
          <polyline points={line} fill="none" stroke="#38BDF8" strokeWidth="2.2" />
          {TREND.map((v, i) => (
            <circle key={i} cx={x(i)} cy={y(v)} r="3.2" fill="#0B0E14" stroke="#38BDF8" strokeWidth="2" />
          ))}
        </svg>
        <div className="mt-2 flex justify-between px-1 font-mono text-[10px] text-[#8A97AD]">
          {DAYS.map((d) => (
            <span key={d}>{d}</span>
          ))}
        </div>
      </div>
    </div>
  );
}
