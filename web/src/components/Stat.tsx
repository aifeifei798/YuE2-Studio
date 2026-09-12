import type { ReactNode } from "react";

export function StatCard({
  icon,
  tint,
  value,
  label,
}: {
  icon: ReactNode;
  tint: string;
  value: string;
  label: string;
}) {
  return (
    <div className="stat">
      <div className="ic" style={{ background: tint }}>
        {icon}
      </div>
      <div style={{ minWidth: 0 }}>
        <div className="v">{value}</div>
        <div className="k">{label}</div>
      </div>
    </div>
  );
}

export function EmptyState({ emoji, title, sub }: { emoji: string; title: string; sub?: string }) {
  return (
    <div className="empty">
      <div className="big">{emoji}</div>
      <div className="t">{title}</div>
      {sub && <div className="s">{sub}</div>}
    </div>
  );
}
