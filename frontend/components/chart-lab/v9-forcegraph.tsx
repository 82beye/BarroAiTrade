'use client';

/**
 * 차트 랩 v9 — 지식 그래프 (포스 시뮬레이션 캔버스)
 * 원본: /Users/beye/workspace/BarroUs/src/components/graph/force-graph-canvas.tsx
 * 라이브러리(원본): react-force-graph-2d + d3-force (canvas)
 *
 * d3 가 본 프로젝트 의존성에 없어 순수 canvas 로 근사 재현(물리 시뮬 단순화):
 *   - charge 반발력 + 링크 스프링 + 중심 인력의 경량 힘 적분 루프(rAF)
 *   - 노드 크기 = sqrt(1 + degree*1.5)*4 (원본 nodeVal 공식 보존)
 *   - 호버 시 1차 이웃 하이라이트, 그 외 페이드(원본 인터랙션 보존)
 * 색/배경은 BarroUs 팔레트 그대로:
 *   배경 #F2EFE7, 라벨 #0E0E0E, 노드색 person #E54A28 / artist #7B61FF /
 *   album #C68A4A / year #4A8FB4 / genre #3F8E5C / track #8A8A82 / playlist #0E0E0E
 * 데이터는 정적 샘플(노드 12 / 엣지 15).
 */

import { useEffect, useRef, useState } from 'react';

const NODE_COLOR: Record<string, string> = {
  person: '#E54A28',
  playlist: '#0E0E0E',
  track: '#8A8A82',
  artist: '#7B61FF',
  album: '#C68A4A',
  year: '#4A8FB4',
  genre: '#3F8E5C',
};
const BG = '#F2EFE7';
const INK = '#0E0E0E';
const EDGE = 'rgba(14,14,14,0.18)';

interface SNode {
  id: string;
  type: string;
  title: string;
  degree: number;
  x: number;
  y: number;
  vx: number;
  vy: number;
}
interface SEdge {
  source: string;
  target: string;
}

const RAW_NODES: { id: string; type: string; title: string }[] = [
  { id: 'me', type: 'person', title: '나' },
  { id: 'pl1', type: 'playlist', title: '주말 드라이브' },
  { id: 'pl2', type: 'playlist', title: '집중 모드' },
  { id: 't1', type: 'track', title: 'Nightcall' },
  { id: 't2', type: 'track', title: 'Resonance' },
  { id: 't3', type: 'track', title: 'Midnight' },
  { id: 'ar1', type: 'artist', title: 'Kavinsky' },
  { id: 'ar2', type: 'artist', title: 'HOME' },
  { id: 'al1', type: 'album', title: 'OutRun' },
  { id: 'gn1', type: 'genre', title: 'Synthwave' },
  { id: 'yr1', type: 'year', title: '2013' },
  { id: 'nt1', type: 'text_note', title: '메모: 러닝 BGM' },
];
const RAW_EDGES: SEdge[] = [
  { source: 'me', target: 'pl1' },
  { source: 'me', target: 'pl2' },
  { source: 'me', target: 'nt1' },
  { source: 'pl1', target: 't1' },
  { source: 'pl1', target: 't2' },
  { source: 'pl2', target: 't3' },
  { source: 'pl2', target: 't2' },
  { source: 't1', target: 'ar1' },
  { source: 't2', target: 'ar2' },
  { source: 't3', target: 'ar2' },
  { source: 't1', target: 'al1' },
  { source: 'al1', target: 'ar1' },
  { source: 'al1', target: 'gn1' },
  { source: 'al1', target: 'yr1' },
  { source: 't2', target: 'gn1' },
];

export function V9ForceGraph() {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const [hovered, setHovered] = useState<string | null>(null);
  const hoveredRef = useRef<string | null>(null);
  hoveredRef.current = hovered;

  useEffect(() => {
    const canvas = canvasRef.current;
    const wrap = wrapRef.current;
    if (!canvas || !wrap) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // degree 계산
    const degree = new Map<string, number>();
    for (const e of RAW_EDGES) {
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    // 이웃 인접
    const neighbors = new Map<string, Set<string>>();
    for (const e of RAW_EDGES) {
      if (!neighbors.has(e.source)) neighbors.set(e.source, new Set());
      if (!neighbors.has(e.target)) neighbors.set(e.target, new Set());
      neighbors.get(e.source)!.add(e.target);
      neighbors.get(e.target)!.add(e.source);
    }

    let W = wrap.clientWidth || 600;
    let H = 440;
    const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;

    const nodes: SNode[] = RAW_NODES.map((n, i) => {
      const a = (i / RAW_NODES.length) * Math.PI * 2;
      return {
        ...n,
        degree: degree.get(n.id) ?? 0,
        x: Math.cos(a) * 120 + (Math.random() - 0.5) * 20,
        y: Math.sin(a) * 120 + (Math.random() - 0.5) * 20,
        vx: 0,
        vy: 0,
      };
    });
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const radiusOf = (n: SNode) => Math.max(3, Math.sqrt(1 + n.degree * 1.5) * 4);

    const resize = () => {
      W = wrap.clientWidth || 600;
      canvas.width = W * dpr;
      canvas.height = H * dpr;
      canvas.style.width = `${W}px`;
      canvas.style.height = `${H}px`;
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    };
    resize();
    const ro = new ResizeObserver(resize);
    ro.observe(wrap);

    // 힘 파라미터 (원본 preset 근사)
    const REPEL = 2600;
    const LINK_DIST = 78;
    const LINK_K = 0.04;
    const CENTER_K = 0.02;
    const DAMP = 0.82;

    let raf = 0;
    const step = () => {
      // charge 반발
      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const a = nodes[i];
          const b = nodes[j];
          let dx = a.x - b.x;
          let dy = a.y - b.y;
          let d2 = dx * dx + dy * dy;
          if (d2 < 1) d2 = 1;
          const f = REPEL / d2;
          const d = Math.sqrt(d2);
          const fx = (dx / d) * f;
          const fy = (dy / d) * f;
          a.vx += fx;
          a.vy += fy;
          b.vx -= fx;
          b.vy -= fy;
        }
      }
      // 링크 스프링
      for (const e of RAW_EDGES) {
        const a = byId.get(e.source)!;
        const b = byId.get(e.target)!;
        const dx = b.x - a.x;
        const dy = b.y - a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 1;
        const f = (d - LINK_DIST) * LINK_K;
        const fx = (dx / d) * f;
        const fy = (dy / d) * f;
        a.vx += fx;
        a.vy += fy;
        b.vx -= fx;
        b.vy -= fy;
      }
      // 중심 인력 + 적분
      for (const n of nodes) {
        n.vx += -n.x * CENTER_K;
        n.vy += -n.y * CENTER_K;
        n.vx *= DAMP;
        n.vy *= DAMP;
        n.x += n.vx;
        n.y += n.vy;
      }
      draw();
      raf = requestAnimationFrame(step);
    };

    const draw = () => {
      const hov = hoveredRef.current;
      const nb = hov ? neighbors.get(hov) ?? new Set<string>() : null;
      ctx.fillStyle = BG;
      ctx.fillRect(0, 0, W, H);
      ctx.save();
      ctx.translate(W / 2, H / 2);

      // 엣지
      for (const e of RAW_EDGES) {
        const a = byId.get(e.source)!;
        const b = byId.get(e.target)!;
        const active = hov && (e.source === hov || e.target === hov);
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = hov && !active ? fade(EDGE, 0.25) : active ? 'rgba(229,74,40,0.6)' : EDGE;
        ctx.lineWidth = active ? 2 : 0.8;
        ctx.stroke();
      }

      // 노드 + 라벨
      for (const n of nodes) {
        const r = radiusOf(n);
        const dim = hov && n.id !== hov && !nb?.has(n.id);
        const base = NODE_COLOR[n.type] ?? '#8A8A82';
        ctx.beginPath();
        ctx.arc(n.x, n.y, r, 0, Math.PI * 2);
        ctx.fillStyle = dim ? fade(base, 0.18) : base;
        ctx.fill();
        // 라벨 (degree 낮은 노드는 호버 시에만)
        if (n.degree >= 2 || (hov && (n.id === hov || nb?.has(n.id)))) {
          ctx.font = '600 11px system-ui, sans-serif';
          ctx.textAlign = 'left';
          ctx.textBaseline = 'middle';
          ctx.fillStyle = dim ? fade(INK, 0.35) : INK;
          ctx.fillText(n.title, n.x + r + 4, n.y);
        }
      }
      ctx.restore();
    };

    const onMove = (ev: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      const mx = ev.clientX - rect.left - W / 2;
      const my = ev.clientY - rect.top - H / 2;
      let hit: string | null = null;
      for (const n of nodes) {
        const r = radiusOf(n) + 3;
        if ((n.x - mx) ** 2 + (n.y - my) ** 2 <= r * r) {
          hit = n.id;
          break;
        }
      }
      canvas.style.cursor = hit ? 'pointer' : 'default';
      if (hit !== hoveredRef.current) setHovered(hit);
    };
    canvas.addEventListener('mousemove', onMove);
    canvas.addEventListener('mouseleave', () => setHovered(null));

    raf = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(raf);
      ro.disconnect();
      canvas.removeEventListener('mousemove', onMove);
    };
  }, []);

  return (
    <div>
      <div className="mb-2 flex flex-wrap items-center gap-3 text-xs">
        {Object.entries(NODE_COLOR).map(([type, color]) => (
          <span key={type} className="flex items-center gap-1 text-slate-400">
            <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ backgroundColor: color }} />
            {type}
          </span>
        ))}
      </div>
      <div ref={wrapRef} className="w-full overflow-hidden rounded-lg border border-slate-700">
        <canvas ref={canvasRef} />
      </div>
      <p className="mt-2 text-xs text-slate-500">노드에 마우스를 올리면 1차 이웃이 강조됩니다.</p>
    </div>
  );
}

function fade(color: string, alpha: number): string {
  if (color.startsWith('rgba(')) {
    return color.replace(/rgba\(([^)]+)\)/, (_, inner) => {
      const p = inner.split(',').map((s: string) => s.trim());
      return `rgba(${p[0]}, ${p[1]}, ${p[2]}, ${alpha})`;
    });
  }
  if (color.startsWith('#')) {
    const hex = color.slice(1);
    const full = hex.length === 3 ? hex.split('').map((c) => c + c).join('') : hex;
    const r = parseInt(full.slice(0, 2), 16);
    const g = parseInt(full.slice(2, 4), 16);
    const b = parseInt(full.slice(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${alpha})`;
  }
  return color;
}
