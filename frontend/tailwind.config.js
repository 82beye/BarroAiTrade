/** @type {import('tailwindcss').Config} */
module.exports = {
  // 'media' 기본값이면 OS 다크 모드가 티마 라이트 셸의 dark: variant 를 강제 활성화
  // → class 전략으로 전환, 관리자 셸 루트에만 .dark 명시 ((admin)/layout.tsx)
  darkMode: 'class',
  content: [
    './app/**/*.{js,ts,jsx,tsx}',
    './components/**/*.{js,ts,jsx,tsx}',
    './pages/**/*.{js,ts,jsx,tsx}',
  ],
  theme: {
    extend: {
      colors: {
        border: 'rgb(var(--border) / <alpha-value>)',
        input: 'rgb(var(--input) / <alpha-value>)',
        ring: 'rgb(var(--ring) / <alpha-value>)',
        background: 'rgb(var(--background) / <alpha-value>)',
        foreground: 'rgb(var(--foreground) / <alpha-value>)',
        primary: {
          DEFAULT: 'rgb(var(--primary) / <alpha-value>)',
          foreground: 'rgb(var(--primary-foreground) / <alpha-value>)',
        },
        secondary: {
          DEFAULT: 'rgb(var(--secondary) / <alpha-value>)',
          foreground: 'rgb(var(--secondary-foreground) / <alpha-value>)',
        },
        destructive: {
          DEFAULT: 'rgb(var(--destructive) / <alpha-value>)',
          foreground: 'rgb(var(--destructive-foreground) / <alpha-value>)',
        },
        muted: {
          DEFAULT: 'rgb(var(--muted) / <alpha-value>)',
          foreground: 'rgb(var(--muted-foreground) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          foreground: 'rgb(var(--accent-foreground) / <alpha-value>)',
        },
        popover: {
          DEFAULT: 'rgb(var(--popover) / <alpha-value>)',
          foreground: 'rgb(var(--popover-foreground) / <alpha-value>)',
        },
        card: {
          DEFAULT: 'rgb(var(--card) / <alpha-value>)',
          foreground: 'rgb(var(--card-foreground) / <alpha-value>)',
        },
        success: '#10B981',
        warning: '#F59E0B',
        danger: '#EF4444',
        // ── 티마(TIMA) 벤치마크 토큰 (PRD §6.8, 실측 hex) ──
        tima: {
          teal: '#10B8A8',    // 테마 헤더·브랜드 (모드 불변)
          bg: '#E0E8E8',      // 라이트 배경(민트그레이)
          bgDark: '#202020',  // 다크 배경
          card: '#F8F8F8',    // 카드/행 배경
          cardDark: '#383838',
          up: '#D00010',      // 상승 (한국 관례)
          down: '#2060C0',    // 하락
          active: '#E0C008',  // 활성 서브탭 (황색)
          surge: '#F8F880',   // 급등 하이라이트
          select: '#D83870',  // 선택 탭·뱃지 (분홍)
          emph: '#E08040',    // 기준가 강조 박스
          tabbar: '#E8F0F0',  // 하단 5탭바
          tickerNews: '#E8D8C8', // 특징주 뉴스 배너(베이지)
          tickerIndex: '#D8D0E8', // 지수 바(연보라)
          brand: '#D8232A',   // BARRO 로고(빨강 계열)
          text: '#1A1A1A',    // 본문 검정
          sub: '#777777',     // 보조 회색(면책 대비 확보)
          line: '#E0E0E0',    // 1px 구분선
        },
        strategyLine: {
          sf: '#5820B8',
          b1: '#38B068',
          b2: '#3090E0',
          b3: '#7B40C8',
          g: '#E0A000',
          j: '#C81880',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
    },
  },
  plugins: [],
}
