/** 霓虹 mesh 封面：按 seed + title 稳定生成，零网络请求。 */
const PALETTES = [
  ["#7e22ce", "#6366f1", "#22d3ee"],
  ["#db2777", "#7e22ce", "#3b82f6"],
  ["#059669", "#22d3ee", "#a855f7"],
  ["#ea580c", "#db2777", "#7e22ce"],
  ["#4f46e5", "#06b6d4", "#a855f7"],
  ["#0ea5e9", "#6366f1", "#e879f9"],
];

function hashStr(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function mulberry(seed: number) {
  let a = seed >>> 0;
  return () => {
    a |= 0; a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export default function Cover({
  seed,
  title,
  size = 56,
  rounded = 12,
}: {
  seed: number | string;
  title: string;
  size?: number;
  rounded?: number;
}) {
  const seedNum = typeof seed === "number" ? seed : hashStr(String(seed));
  const h = hashStr(`${seedNum}:${title}`);
  const pal = PALETTES[h % PALETTES.length];
  const rnd = mulberry(h);
  const gid = `g${h.toString(36)}`;
  const blobs = [0, 1, 2].map(() => ({
    cx: 15 + rnd() * 70,
    cy: 15 + rnd() * 70,
    r: 28 + rnd() * 34,
    c: pal[Math.floor(rnd() * pal.length)],
    o: 0.55 + rnd() * 0.35,
  }));
  const ring = { cx: 20 + rnd() * 60, cy: 20 + rnd() * 60, r: 14 + rnd() * 22, rot: rnd() * 360 };
  const noteY = 34 + rnd() * 30;

  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 100 100"
      style={{ borderRadius: rounded, flexShrink: 0, display: "block", background: "#0b0e1a" }}
      aria-hidden
    >
      <defs>
        <radialGradient id={`${gid}-bg`} cx="30%" cy="20%" r="90%">
          <stop offset="0%" stopColor="#1b2140" />
          <stop offset="100%" stopColor="#07080e" />
        </radialGradient>
        {blobs.map((b, i) => (
          <radialGradient key={i} id={`${gid}-b${i}`} cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor={b.c} stopOpacity={b.o} />
            <stop offset="100%" stopColor={b.c} stopOpacity={0} />
          </radialGradient>
        ))}
      </defs>
      <rect width="100" height="100" fill={`url(#${gid}-bg)`} />
      {blobs.map((b, i) => (
        <circle key={i} cx={b.cx} cy={b.cy} r={b.r} fill={`url(#${gid}-b${i})`} />
      ))}
      <g transform={`rotate(${ring.rot} ${ring.cx} ${ring.cy})`} opacity={0.5}>
        <ellipse cx={ring.cx} cy={ring.cy} rx={ring.r} ry={ring.r * 0.42} fill="none" stroke="#ffffff" strokeOpacity={0.5} strokeWidth={1.4} />
        <ellipse cx={ring.cx} cy={ring.cy} rx={ring.r * 0.7} ry={ring.r * 0.3} fill="none" stroke="#ffffff" strokeOpacity={0.25} strokeWidth={1} />
      </g>
      {/* 音符 */}
      <g opacity={0.92}>
        <ellipse cx={38} cy={noteY + 14} rx={7} ry={5.4} fill="#fff" transform={`rotate(-18 38 ${noteY + 14})`} />
        <ellipse cx={62} cy={noteY + 17} rx={7} ry={5.4} fill="#fff" opacity={0.85} transform={`rotate(-18 62 ${noteY + 17})`} />
        <rect x={43} y={noteY - 14} width={3.4} height={30} rx={1.5} fill="#fff" />
        <rect x={67} y={noteY - 11} width={3.4} height={30} rx={1.5} fill="#fff" opacity={0.85} />
        <path d={`M43 ${noteY - 14} Q55 ${noteY - 20} 67 ${noteY - 11} L67 ${noteY - 5} Q55 ${noteY - 14} 43 ${noteY - 8} Z`} fill="#fff" />
      </g>
      <rect width="100" height="100" fill="none" stroke="rgba(255,255,255,.14)" strokeWidth={2} rx={8} />
    </svg>
  );
}
