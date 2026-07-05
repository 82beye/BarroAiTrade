// ── 마켓일정 카테고리 매핑 (PRD §3.5: 테마 빨강 / 개별 주황 / 정책 초록) ──
// 캘린더·종목 상세가 공용 사용. 백엔드 기존 event_type 값들을 3카테고리로 흡수.
export interface CategoryStyle {
  label: string;
  dot: string; // 원형 뱃지 배경색
  chip: string; // 파스텔 칩 클래스
}

export function categoryStyle(eventType: string): CategoryStyle {
  const t = (eventType || '').toLowerCase();
  if (t === 'theme' || t === 'earnings')
    return { label: '테마', dot: '#D00010', chip: 'bg-rose-100 text-rose-700' };
  if (t === 'individual' || t === 'dividend' || t === 'split')
    return { label: '개별', dot: '#E08040', chip: 'bg-amber-100 text-amber-700' };
  if (t === 'policy' || t === 'macro' || t === 'holiday')
    return { label: '정책', dot: '#38B068', chip: 'bg-emerald-100 text-emerald-700' };
  if (t === 'ipo' || t === 'listing')
    return { label: '공모', dot: '#7B40C8', chip: 'bg-purple-100 text-purple-700' };
  if (t === 'us' || t === 'macro_us')
    return { label: '해외', dot: '#3090E0', chip: 'bg-blue-100 text-blue-700' };
  return { label: eventType || '기타', dot: '#94a3b8', chip: 'bg-slate-200 text-slate-600' };
}
