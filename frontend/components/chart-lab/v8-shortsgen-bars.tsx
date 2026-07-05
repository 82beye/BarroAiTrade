'use client';

/**
 * 차트 랩 v8 — ShortsGen 분석: 쇼츠별 조회수 바 차트
 * 원본: /Users/beye/workspace/BarroShopping/packages/frontend/src/dashboard.jsx
 *       (Analytics 뷰의 BarChart — 차트 부분만 추출)
 * 라이브러리: 순수 CSS/HTML 바 차트 (원본도 d3 미사용, div 트랙 기반)
 *
 * 원본 시각 스타일 보존:
 *   ShortsGen 다크 팔레트(bg #0B0E14, surface #141A24, border #26303F),
 *   가로 막대: 라벨 130px + 트랙(bg #0B0E14, radius 7) + 값,
 *   막대 그라디언트 핑크→앰버(linear-gradient(90deg,#FF5C8A,#FBBF24)).
 * 데이터는 정적 샘플(배포 쇼츠별 조회수).
 * 참고: v10 과 동일한 ShortsGen 대시보드 소스이며, v10 은 AreaChart+KPI 를 담당.
 */

const BARS = [
  { label: '겨울 캐시미어 롱코트', v: 184200 },
  { label: '노이즈캔슬링 이어버드', v: 96800 },
  { label: '프리미엄 진공 텀블러', v: 41300 },
  { label: '경량 카본 캠핑 체어', v: 28700 },
  { label: '오버사이즈 후드 집업', v: 15400 },
];

const KPIS = [
  { label: '총 조회수', val: '36.6만', c: '#38BDF8' },
  { label: '평균 CTR', val: '6.5%', c: '#FBBF24' },
  { label: '평균 전환율', val: '2.4%', c: '#34D399' },
  { label: '배포 쇼츠', val: '3', c: '#F472B6' },
];

const fmt = (n: number) => (n >= 10000 ? `${(n / 10000).toFixed(1)}만` : n.toLocaleString('ko-KR'));

export function V8ShortsgenBars() {
  const max = Math.max(...BARS.map((b) => b.v));
  return (
    <div className="rounded-xl border border-[#26303F] bg-[#0B0E14] p-5">
      {/* KPI 타일 (원본 border-top 3px 강조) */}
      <div className="mb-5 grid grid-cols-2 gap-3 sm:grid-cols-4">
        {KPIS.map((k) => (
          <div
            key={k.label}
            className="rounded-xl border border-[#26303F] bg-[#141A24] p-4"
            style={{ borderTop: `3px solid ${k.c}` }}
          >
            <div className="text-2xl font-bold tracking-tight" style={{ color: k.c }}>
              {k.val}
            </div>
            <div className="mt-1 text-xs text-[#8A97AD]">{k.label}</div>
          </div>
        ))}
      </div>

      {/* 쇼츠별 조회수 바 차트 */}
      <div className="rounded-xl border border-[#26303F] bg-[#141A24] p-5">
        <div className="mb-4 text-sm font-semibold text-[#E7ECF4]">쇼츠별 조회수</div>
        <div className="flex flex-col gap-3">
          {BARS.map((d, i) => (
            <div key={i} className="grid grid-cols-[130px_1fr_56px] items-center gap-2.5">
              <span className="truncate text-xs text-[#8A97AD]">{d.label}</span>
              <div className="h-3.5 overflow-hidden rounded-md bg-[#0B0E14]">
                <div
                  className="h-full rounded-md"
                  style={{
                    width: `${(d.v / max) * 100}%`,
                    background: 'linear-gradient(90deg,#FF5C8A,#FBBF24)',
                  }}
                />
              </div>
              <span className="text-right font-mono text-[11px] text-[#E7ECF4]">{fmt(d.v)}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
