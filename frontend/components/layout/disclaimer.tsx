/**
 * 공용 면책 문구 (PRD §4.7 / FR-S-10)
 * 시그널·테마·차트 화면 말미에 배치. 고정 하단이 아닌 페이지 흐름 하단 고정.
 * WCAG AA 대비 확보를 위해 text-slate-400 이상, 최소 text-xs 사용.
 */
export function Disclaimer({ className = '' }: { className?: string }) {
  return (
    <p
      className={`mt-8 border-t border-slate-800 pt-4 text-xs leading-relaxed text-slate-400 ${className}`}
    >
      본 정보는 알고리즘에 의해 산출된 참고용 계산값으로 매수·매도 권유가 아닙니다. B1·B2·B3,
      SF, G1·G2·G3, J1·J2·J3 지표는 시스템에 의한 단순 계산값입니다. 투자의 책임은 투자자 본인에게
      있습니다.
    </p>
  );
}
