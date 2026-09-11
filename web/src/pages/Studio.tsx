import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, getAdminToken, isSafeAudioUrl, type HistoryRecord } from "../lib/api";

const DRAFT_KEY = "yue2-draft-v1";
const TASK_ID_RE = /^[0-9a-f]{8}$/;
const PRESET = {
  title: "今晚不眠",
  style: "City Pop, upbeat, danceable, groovy bass\nelectric guitar, synth, energetic, joyful\nneon city night, emotional male vocal",
  lyrics: "[Intro]\n\n[Verse]\n路灯眨着眼睛 偷看谁的身影\n街道哼着小调 节奏多轻盈\n晚风染成霓虹 吹乱发际线\n脚步踩着鼓点 不需要终点\n\n[Pre-Chorus]\n旋转的唱片 划破了寂静\n气泡在上升 快乐在飞行\n把烦恼抛去 别再去在意\n这里的空气 充满了魔力\n\n[Chorus]\n今晚不眠 快乐无限\n城市在狂欢 我们在中间\n自由摇摆 光芒盛开\n跟着这节拍 把心打开\n\n[Outro]\n霓虹色的风 吹向那梦\n摇摆\n闪耀\nYeah",
  cot: "full",
  seed: "12300",
};

export default function Studio(props: { serverState: string; refreshServer: () => void }) {
  const [title, setTitle] = useState("");
  const [style, setStyle] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [seed, setSeed] = useState("");
  const [cot, setCot] = useState("full");
  const [busy, setBusy] = useState(false);
  const [busyText, setBusyText] = useState("");
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [current, setCurrent] = useState<HistoryRecord | null>(null);
  const [logs, setLogs] = useState<string[]>(["[Ready] 系统就绪，等待指令"]);
  // 删除是管理操作：无 admin token 时不展示删除按钮
  const [isAdmin] = useState(() => getAdminToken() !== "");
  const audioRef = useRef<HTMLAudioElement>(null);
  const pollRef = useRef<number | null>(null);

  const pushLog = useCallback((msg: string) => {
    setLogs((prev) => [...prev.slice(-199), `[${new Date().toLocaleTimeString()}] ${msg}`]);
  }, []);

  const loadHistory = useCallback(async () => {
    try {
      const params = new URLSearchParams({ limit: "100", offset: "0" });
      if (query.trim()) params.set("q", query.trim());
      const data = await apiFetch<{ total: number; items: HistoryRecord[] }>(`/api/history?${params}`);
      setHistory(data.items || []);
      setTotal(data.total ?? (data.items || []).length);
    } catch (e) {
      pushLog(`拉取历史失败: ${(e as Error).message}`);
    }
  }, [query, pushLog]);

  useEffect(() => {
    try {
      const raw = localStorage.getItem(DRAFT_KEY);
      if (raw) {
        const d = JSON.parse(raw);
        if (d.title) setTitle(d.title);
        if (d.style) setStyle(d.style);
        if (d.lyrics) setLyrics(d.lyrics);
        if (d.cot) setCot(d.cot);
        if (d.seed !== undefined) setSeed(String(d.seed ?? ""));
      }
    } catch { /* ignore */ }
    loadHistory();
  }, [loadHistory]);

  useEffect(() => {
    const t = setTimeout(() => {
      try {
        localStorage.setItem(DRAFT_KEY, JSON.stringify({ title, style, lyrics, cot, seed }));
      } catch { /* ignore */ }
    }, 500);
    return () => clearTimeout(t);
  }, [title, style, lyrics, cot, seed]);

  useEffect(() => {
    const t = setTimeout(loadHistory, 300);
    return () => clearTimeout(t);
  }, [query, loadHistory]);

  useEffect(() => () => { if (pollRef.current) window.clearInterval(pollRef.current); }, []);

  function play(item: HistoryRecord) {
    if (!isSafeAudioUrl(item.audio_url)) {
      pushLog("拒绝了不合法的音频地址");
      return;
    }
    setCurrent(item);
    const el = audioRef.current;
    if (el) {
      el.src = item.audio_url;
      el.play().catch(() => undefined);
    }
    pushLog(`正在播放: 《${item.title}》`);
  }

  async function removeItem(id: string) {
    if (!TASK_ID_RE.test(id)) return;
    if (!confirm("确定删除该曲目吗？音频文件也会一起删除。")) return;
    try {
      await apiFetch(`/api/admin/history/${id}`, { method: "DELETE" }, true);
      setHistory((h) => h.filter((x) => x.task_id !== id));
      setTotal((t) => Math.max(0, t - 1));
      pushLog(`已删除曲目 ${id}`);
    } catch (e) {
      pushLog(`删除失败: ${(e as Error).message}`);
    }
  }

  function pollTask(taskId: string, taskSeed: number) {
    const started = Date.now();
    if (pollRef.current) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(async () => {
      if (Date.now() - started > 15 * 60 * 1000) {
        if (pollRef.current) window.clearInterval(pollRef.current);
        setBusy(false);
        pushLog("任务超时（15分钟），请在历史中查看");
        return;
      }
      try {
        const t = await apiFetch<{ status: string; queue_position?: number; record?: HistoryRecord; error?: string }>(`/api/tasks/${taskId}`);
        if (t.status === "running") {
          setBusyText("正在创作全曲 (GPU 生成中)...");
        } else if (t.status === "pending") {
          setBusyText(`已入队等待显卡${t.queue_position ? ` (第 ${t.queue_position} 位)` : ""}...`);
        } else if (t.status === "succeeded" && t.record) {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setBusy(false);
          pushLog(`创作成功！种子: ${taskSeed}`);
          setHistory((h) => [t.record as HistoryRecord, ...h]);
          setTotal((x) => x + 1);
          setQuery("");
          play(t.record);
          props.refreshServer();
        } else if (t.status === "failed") {
          if (pollRef.current) window.clearInterval(pollRef.current);
          setBusy(false);
          pushLog(`生成失败: ${t.error || "未知错误"}`);
        }
      } catch (e) {
        pushLog(`轮询失败: ${(e as Error).message}`);
      }
    }, 2500);
  }

  async function submit() {
    let seedNum: number | null = null;
    if (seed.trim() !== "") {
      const n = Number(seed.trim());
      if (!Number.isInteger(n) || n < 0 || n > 2147483647) {
        alert("种子必须是 0 ~ 2147483647 的整数，留空则随机。");
        return;
      }
      seedNum = n;
    }
    if (!style.trim() || !lyrics.trim()) {
      alert("曲风描述 (Style) 和 歌词 (Lyrics) 为必填项！");
      return;
    }
    if (style.length > 2000 || lyrics.length > 10000) {
      alert("曲风最多 2000 字、歌词最多 10000 字。");
      return;
    }
    setBusy(true);
    setBusyText("正在入队...");
    pushLog(`提交创作: 《${title.trim() || "未命名歌曲"}》`);
    try {
      const data = await apiFetch<{ task_id: string; queue_position: number; seed: number }>("/api/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: title.trim() || "未命名歌曲", style: style.trim(), lyrics: lyrics.trim(), cot, seed: seedNum }),
      });
      pushLog(`已入队 task=${data.task_id}，第 ${data.queue_position} 位`);
      pollTask(data.task_id, data.seed);
    } catch (e) {
      setBusy(false);
      pushLog(`提交失败: ${(e as Error).message}`);
    }
  }

  const lyricLines = lyrics ? lyrics.split("\n").length : 0;
  const lyricChars = lyrics.replace(/\s/g, "").length;

  return (
    <div className="layout cols3">
      <section className="panel">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <label className="lbl" htmlFor="f-title">歌曲标题</label>
          <button className="mini" onClick={() => { setTitle(PRESET.title); setStyle(PRESET.style); setLyrics(PRESET.lyrics); setCot(PRESET.cot); setSeed(PRESET.seed); }}>填入《今晚不眠》</button>
        </div>
        <input id="f-title" className="in" maxLength={100} placeholder="给你的歌曲起个名字..." value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className="row">
          <div>
            <label className="lbl" htmlFor="f-seed">随机种子</label>
            <input id="f-seed" className="in" type="number" min={0} max={2147483647} placeholder="留空则随机" value={seed} onChange={(e) => setSeed(e.target.value)} />
          </div>
          <div>
            <label className="lbl" htmlFor="f-cot">思维模式</label>
            <select id="f-cot" className="in" value={cot} onChange={(e) => setCot(e.target.value)}>
              <option value="full">Full (推荐)</option>
              <option value="none">None (直出)</option>
            </select>
          </div>
        </div>
        <label className="lbl" htmlFor="f-style">曲风描述（{style.length} / 2000）</label>
        <textarea id="f-style" className="in" rows={2} maxLength={2000} value={style} onChange={(e) => setStyle(e.target.value)} />
        <label className="lbl" htmlFor="f-lyrics">歌词（{lyricLines} 行 · {lyricChars} 字）</label>
        <textarea id="f-lyrics" className="in" rows={12} maxLength={10000} value={lyrics} onChange={(e) => setLyrics(e.target.value)} />
        <div style={{ marginTop: 10 }}>
          <button className="btn" disabled={busy} onClick={submit}>{busy ? busyText || "处理中..." : "开始生成全曲"}</button>
          <div className="hint">多人共享显卡时自动排队；草稿自动保存在本机。状态：{props.serverState}</div>
        </div>
      </section>

      <section style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="panel">
          <h3>{current ? current.title : "等待聆听"}</h3>
          <div className="hint">{current ? `Seed: ${current.seed}` : "右侧点击曲目立即载入"}</div>
          <audio ref={audioRef} controls preload="none" style={{ width: "100%", marginTop: 8 }} />
        </div>
        <div className="panel" style={{ flex: 1 }}>
          <h3>本曲制作档案</h3>
          {current ? (
            <>
              <div className="kv">
                <span>种子：{current.seed}</span>
                <span>模式：{current.cot}</span>
                <span style={{ gridColumn: "1 / -1" }}>时间：{current.created_at}</span>
              </div>
              <div className="hint">风格</div>
              <div className="pre">{current.style}</div>
              <div className="hint">歌词</div>
              <div className="pre">{current.lyrics}</div>
            </>
          ) : (<div className="hint">暂无选中曲目</div>)}
        </div>
        <div className="panel">
          <h3>控制台日志</h3>
          <div className="logbox">{logs.map((l, i) => <div key={i}>{l}</div>)}</div>
        </div>
      </section>

      <section className="panel">
        <h3>创作历史 ({total})</h3>
        <input className="in" placeholder="搜索歌名或 Seed..." value={query} onChange={(e) => setQuery(e.target.value)} />
        <div style={{ marginTop: 10 }}>
          {history.length === 0 && <div className="hint">无匹配的曲目</div>}
          {history.map((item) => (
            <div key={item.task_id} className={`hist-item${current?.task_id === item.task_id ? " playing" : ""}`} onClick={() => play(item)}>
              <div style={{ minWidth: 0 }}>
                <div className="t">{item.title}</div>
                <div className="s">Seed: {item.seed}</div>
              </div>
              {isAdmin && <button className="mini danger" onClick={(e) => { e.stopPropagation(); removeItem(item.task_id); }}>删</button>}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
