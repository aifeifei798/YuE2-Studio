import { useEffect, useRef, useState } from "react";

/** 波形可视化：绑定外部 <audio>，单音源不重播。失败时降级为均衡器条。 */
export default function Waveform({
  audioRef,
  audioUrl,
  playing,
}: {
  audioRef: React.RefObject<HTMLAudioElement>;
  audioUrl: string;
  playing: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const wsRef = useRef<{ destroy: () => void; load: (u: string) => void } | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let ws: { destroy: () => void } | null = null;
    (async () => {
      try {
        if (!boxRef.current || !audioRef.current || !audioUrl) return;
        const { default: WaveSurfer } = await import("wavesurfer.js");
        if (cancelled || !boxRef.current || !audioRef.current) return;
        // 清掉旧实例（切歌时）
        wsRef.current?.destroy();
        boxRef.current.innerHTML = "";
        const inst = (WaveSurfer as unknown as { create: (o: unknown) => { destroy: () => void; load: (u: string) => void } }).create({
          container: boxRef.current,
          media: audioRef.current,
          waveColor: "rgba(168,85,247,.45)",
          progressColor: "#a855f7",
          cursorColor: "rgba(255,255,255,.6)",
          cursorWidth: 1,
          height: 64,
          barWidth: 2,
          barGap: 2,
          barRadius: 2,
          normalize: true,
        });
        ws = inst;
        wsRef.current = inst;
        inst.load(audioUrl);
      } catch {
        if (!cancelled) setFailed(true);
      }
    })();
    return () => {
      cancelled = true;
      try { ws?.destroy(); } catch { /* ignore */ }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [audioUrl]);

  useEffect(() => () => { try { wsRef.current?.destroy(); } catch { /* ignore */ } }, []);

  if (failed || !audioUrl) {
    const bars = Array.from({ length: 48 }, (_, i) => 10 + ((i * 37) % 50));
    return (
      <div className={`wave-fallback${playing ? " playing" : ""}`} aria-hidden>
        {bars.map((h, i) => (
          <i key={i} style={{ height: h }} />
        ))}
      </div>
    );
  }
  return <div ref={boxRef} className="ws-container" />;
}
