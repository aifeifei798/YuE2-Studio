import { useCallback, useEffect, useRef, useState } from "react";
import {
  Clock,
  Dices,
  Disc3,
  Download,
  History,
  ListMusic,
  LogOut,
  Pause,
  Play,
  Search,
  Sparkles,
  Terminal,
  Trash2,
  User,
  Wand2,
} from "lucide-react";
import {
  apiFetch,
  clearUserCreds,
  getAdminToken,
  getUserCreds,
  isSafeAudioUrl,
  setUserCreds,
  type HistoryRecord,
  type PublicConfig,
  type QuotaInfo,
} from "../lib/api";
import { useModal } from "../components/Modal";
import { useToast } from "../components/Toast";
import Cover from "../components/Cover";
import Waveform from "../components/Waveform";
import { EmptyState } from "../components/Stat";

const DRAFT_KEY = "yue2-draft-v1";
const TASK_ID_RE = /^[0-9a-f]{8}$/;
const SEED_RE = /^\d+$/;
const PAGE_SIZE = 20;
const POLL_TIMEOUT_MS = 60 * 60 * 1000;
const PRESET = {
  title: "今晚不眠",
  style: "City Pop, upbeat, danceable, groovy bass\nelectric guitar, synth, energetic, joyful\nneon city night, emotional male vocal",
  lyrics: "[Intro]\n\n[Verse]\n路灯眨着眼睛 偷看谁的身影\n街道哼着小调 节奏多轻盈\n晚风染成霓虹 吹乱发际线\n脚步踩着鼓点 不需要终点\n\n[Pre-Chorus]\n旋转的唱片 划破了寂静\n气泡在上升 快乐在飞行\n把烦恼抛去 别再去在意\n这里的空气 充满了魔力\n\n[Chorus]\n今晚不眠 快乐无限\n城市在狂欢 我们在中间\n自由摇摆 光芒盛开\n跟着这节拍 把心打开\n\n[Outro]\n霓虹色的风 吹向那梦\n摇摆\n闪耀\nYeah",
  cot: "full",
  seed: "12300",
};

const STYLE_PRESETS = [
  { name: "🌃 City Pop", style: "City Pop, upbeat, danceable, groovy bass, electric guitar, synth, neon city night" },
  { name: "🏮 古风", style: "Chinese ancient style, guzheng, pipa, bamboo flute, ethereal female vocal, poetic" },
  { name: "🎸 民谣", style: "Folk, warm acoustic guitar, gentle male vocal, nostalgic, storytelling" },
  { name: "🎧 电子", style: "EDM, energetic synth, four-on-the-floor, euphoric drop, futuristic" },
  { name: "🥁 摇滚", style: "Rock, powerful electric guitar, punchy drums, passionate male vocal, anthemic" },
  { name: "🎷 爵士", style: "Jazz, smooth saxophone, walking bass, brushed drums, smoky female vocal, late night" },
];

export default function Studio(props: { serverState: string; refreshServer: () => void }) {
  const [title, setTitle] = useState("");
  const [style, setStyle] = useState("");
  const [lyrics, setLyrics] = useState("");
  const [seed, setSeed] = useState("");
  const [cot, setCot] = useState("full");
  const [busy, setBusy] = useState(false);
  const [busyText, setBusyText] = useState("");
  const [history, setHistory] = useState<HistoryRecord[]>([]);
  const [histLoading, setHistLoading] = useState(false);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(0);
  const [current, setCurrent] = useState<HistoryRecord | null>(null);
  const [playing, setPlaying] = useState(false);
  const [centerTab, setCenterTab] = useState<"profile" | "logs">("profile");
  const [logs, setLogs] = useState<string[]>(["[Ready] 系统就绪，等待指令"]);
  const [user, setUser] = useState<QuotaInfo | null>(null);
  const [loginName, setLoginName] = useState("");
  const [loginKey, setLoginKey] = useState("");
  const [mineOnly, setMineOnly] = useState(false);
  const modal = useModal();
  const { toast } = useToast();
  // 删除是管理操作：实时跟随 admin token（同页登录管理后无需刷新即显示）
  const [isAdmin, setIsAdmin] = useState(() => getAdminToken() !== "");
  useEffect(() => {
    const sync = () => setIsAdmin(getAdminToken() !== "");
    window.addEventListener("focus", sync);
    window.addEventListener("hashchange", sync);
    window.addEventListener("storage", sync);
    sync();
    return () => {
      window.removeEventListener("focus", sync);
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("storage", sync);
    };
  }, []);
  // 长度上限与登录开关走后端 /api/config，前端不再硬编码（兜底值为当前默认）
  const [limits, setLimits] = useState({ title: 100, style: 2000, lyrics: 10000, requireKey: false });
  useEffect(() => {
    apiFetch<PublicConfig>("/api/config")
      .then((c) => setLimits({ title: c.max_title_len, style: c.max_style_len, lyrics: c.max_lyrics_len, requireKey: c.require_api_key }))
      .catch(() => undefined);
  }, []);
  const audioRef = useRef<HTMLAudioElement>(null);
  // 多任务轮询：一次提交只加一个 id，单 interval 轮询全部在途任务
  const pollingRef = useRef<Map<string, { seed: number; started: number }>>(new Map());
  const pollTimerRef = useRef<number | null>(null);

  const pushLog = useCallback((msg: string) => {
    setLogs((prev) => [...prev.slice(-199), `[${new Date().toLocaleTimeString()}] ${msg}`]);
  }, []);

  const loadHistory = useCallback(async () => {
    setHistLoading(true);
    try {
      const params = new URLSearchParams({ limit: String(PAGE_SIZE), offset: String(page * PAGE_SIZE) });
      if (query.trim()) params.set("q", query.trim());
      // 登录后可切“只看我的”（走 key 鉴权的个人历史接口）
      const mine = mineOnly && getUserCreds() !== null;
      const base = mine ? "/api/auth/history" : "/api/history";
      const data = await apiFetch<{ total: number; items: HistoryRecord[] }>(
        `${base}?${params}`,
        {},
        false,
        mine,
      );
      setHistory(data.items || []);
      setTotal(data.total ?? (data.items || []).length);
    } catch (e) {
      pushLog(`拉取历史失败: ${(e as Error).message}`);
    } finally {
      setHistLoading(false);
    }
  }, [query, page, mineOnly, pushLog]);

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
    // 历史由下面的 debounce effect 加载，这里只恢复草稿，避免首屏 double-fetch
  }, []);

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
  }, [query, page, mineOnly, loadHistory]);
  // 启动时用存着的凭证静默校验，失效就清掉（上一轮遗留：不要只信本地）
  useEffect(() => {
    if (!getUserCreds()) return;
    apiFetch<QuotaInfo>("/api/auth/me", {}, false, true)
      .then(setUser)
      .catch(() => {
        clearUserCreds();
        setUser(null);
      });
  }, []);

  useEffect(() => () => { if (pollTimerRef.current) window.clearInterval(pollTimerRef.current); }, []);

  // 播放状态跟随 audio 元素
  useEffect(() => {
    const el = audioRef.current;
    if (!el) return;
    const onPlay = () => setPlaying(true);
    const onPause = () => setPlaying(false);
    const onEnded = () => setPlaying(false);
    el.addEventListener("play", onPlay);
    el.addEventListener("pause", onPause);
    el.addEventListener("ended", onEnded);
    return () => {
      el.removeEventListener("play", onPlay);
      el.removeEventListener("pause", onPause);
      el.removeEventListener("ended", onEnded);
    };
  }, []);

  async function refreshQuota() {
    if (!getUserCreds()) return;
    try {
      setUser(await apiFetch<QuotaInfo>("/api/auth/me", {}, false, true));
    } catch {
      /* 忽略，后台轮询/下次操作会提示 */
    }
  }

  async function login() {
    if (!loginName.trim() || !loginKey.trim()) {
      await modal.alert("请输入用户名和 Key（找管理员领取）。");
      return;
    }
    try {
      const data = await apiFetch<QuotaInfo & { ok: boolean }>("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: loginName.trim(), key: loginKey.trim() }),
      });
      setUserCreds(loginName.trim(), loginKey.trim());
      setLoginKey("");
      setUser(data);
      setMineOnly(false);
      setPage(0);
      pushLog(`已登录：${data.name}（配额 ${data.quota_used}/${data.quota_total <= 0 ? "不限" : data.quota_total}）`);
      toast(`欢迎回来，${data.name} 🎉`);
    } catch (e) {
      pushLog(`登录失败: ${(e as Error).message}`);
      await modal.alert(`登录失败：${(e as Error).message}`);
    }
  }

  function logout() {
    clearUserCreds();
    setUser(null);
    setMineOnly(false);
    setPage(0);
    pushLog("已退出登录");
  }

  /** 回到首页；已在首页则直接重载（setState 无变化不会触发 effect，这是之前的坑） */
  function backToFirstPage() {
    if (query === "" && page === 0 && !mineOnly) loadHistory();
    else {
      setQuery("");
      setPage(0);
      setMineOnly(false);
    }
  }

  function play(item: HistoryRecord) {
    if (!isSafeAudioUrl(item.audio_url)) {
      pushLog("拒绝了不合法的音频地址");
      return;
    }
    setCurrent(item);
    setCenterTab("profile");
    const el = audioRef.current;
    if (el) {
      el.src = item.audio_url;
      el.play().catch(() => undefined);
    }
    pushLog(`正在播放: 《${item.title}》`);
  }

  function togglePlay() {
    const el = audioRef.current;
    if (!el || !current) return;
    if (el.paused) el.play().catch(() => undefined);
    else el.pause();
  }

  function randomSeed() {
    setSeed(String(Math.floor(Math.random() * 2147483647)));
  }

  /** 管理员删任意；登录用户只能删归属自己的（匿名旧歌谁都不显示按钮）。 */
  function canDelete(item: HistoryRecord) {
    if (isAdmin) return true;
    return user !== null && !!item.owner && item.owner === user.name;
  }

  async function removeItem(id: string) {
    if (!TASK_ID_RE.test(id)) return;
    if (!(await modal.confirm("确定删除该曲目吗？音频文件也会一起删除。"))) return;
    try {
      if (isAdmin) {
        await apiFetch(`/api/admin/history/${id}`, { method: "DELETE" }, true);
      } else {
        // 普通用户走归属接口：只能删自己的歌
        await apiFetch(`/api/auth/history/${id}`, { method: "DELETE" }, false, true);
      }
      // 删掉本页最后一条且不在首页时退一页，否则重载本页
      if (history.length <= 1 && page > 0) setPage(page - 1);
      else loadHistory();
      pushLog(`已删除曲目 ${id}`);
      toast("已删除该曲目");
    } catch (e) {
      pushLog(`删除失败: ${(e as Error).message}`);
      toast(`删除失败：${(e as Error).message}`, "err");
    }
  }

  function ensurePollTimer() {
    if (pollTimerRef.current) return;
    pollTimerRef.current = window.setInterval(async () => {
      const entries = [...pollingRef.current.entries()];
      if (entries.length === 0) {
        if (pollTimerRef.current) window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        setBusy(false);
        return;
      }
      let running = false;
      let pendingText = "";
      for (const [taskId, meta] of entries) {
        if (Date.now() - meta.started > POLL_TIMEOUT_MS) {
          pollingRef.current.delete(taskId);
          pushLog(`任务 ${taskId} 超时（60分钟），请在历史中查看`);
          continue;
        }
        try {
          const t = await apiFetch<{ status: string; queue_position?: number; record?: HistoryRecord; error?: string }>(`/api/tasks/${taskId}`);
          if (t.status === "running") {
            running = true;
          } else if (t.status === "pending") {
            pendingText = `已入队等待显卡${t.queue_position ? ` (第 ${t.queue_position} 位)` : ""}...`;
          } else if (t.status === "succeeded" && t.record) {
            pollingRef.current.delete(taskId);
            pushLog(`创作成功！task=${taskId} 种子: ${meta.seed}`);
            toast(`🎉《${t.record.title}》创作成功！`);
            // 回到首页并重载（服务端分页下不再本地 unshift）
            backToFirstPage();
            refreshQuota();
            play(t.record);
            props.refreshServer();
          } else if (t.status === "failed") {
            pollingRef.current.delete(taskId);
            pushLog(`任务 ${taskId} 生成失败: ${t.error || "未知错误"}`);
            toast(`生成失败：${t.error || "未知错误"}`, "err");
          }
        } catch (e) {
          pushLog(`轮询失败 ${taskId}: ${(e as Error).message}`);
        }
      }
      const left = pollingRef.current.size;
      if (left === 0) {
        if (pollTimerRef.current) window.clearInterval(pollTimerRef.current);
        pollTimerRef.current = null;
        setBusy(false);
      } else {
        setBusy(true);
        setBusyText(running ? "正在创作全曲 (GPU 生成中)..." : pendingText || "正在入队...");
      }
    }, 2500);
  }

  function pollTask(taskId: string, taskSeed: number) {
    pollingRef.current.set(taskId, { seed: taskSeed, started: Date.now() });
    setBusy(true);
    setBusyText("正在入队...");
    ensurePollTimer();
  }

  async function submit() {
    // 强制登录站点：未登录直接提示，不发请求
    if (limits.requireKey && !getUserCreds()) {
      await modal.alert("本站点要求登录后才能生成，请先在右侧用用户名 + Key 登录（找管理员领取）。");
      return;
    }
    let seedNum: number | null = null;
    if (seed.trim() !== "") {
      // Number("1e3") 会绕过整数校验，必须纯数字正则先行
      if (!SEED_RE.test(seed.trim())) {
        await modal.alert("种子必须是 0 ~ 2147483647 的整数，留空则随机。");
        return;
      }
      const n = Number(seed.trim());
      if (!Number.isInteger(n) || n < 0 || n > 2147483647) {
        await modal.alert("种子必须是 0 ~ 2147483647 的整数，留空则随机。");
        return;
      }
      seedNum = n;
    }
    if (!style.trim() || !lyrics.trim()) {
      await modal.alert("曲风描述 (Style) 和 歌词 (Lyrics) 为必填项！");
      return;
    }
    if (title.trim().length > limits.title || style.trim().length > limits.style || lyrics.trim().length > limits.lyrics) {
      await modal.alert(`标题最多 ${limits.title} 字、曲风最多 ${limits.style} 字、歌词最多 ${limits.lyrics} 字。`);
      return;
    }
    setBusy(true);
    setBusyText("正在入队...");
    pushLog(`提交创作: 《${title.trim() || "未命名歌曲"}》`);
    try {
      const data = await apiFetch<{ task_id: string; queue_position: number; seed: number }>(
        "/api/generate",
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: title.trim() || "未命名歌曲", style: style.trim(), lyrics: lyrics.trim(), cot, seed: seedNum }),
        },
        false,
        true, // 登录后自动带 X-API-Key，记到个人名下并扣配额
      );
      pushLog(`已入队 task=${data.task_id}，第 ${data.queue_position} 位`);
      toast(`已入队，第 ${data.queue_position} 位 🎶`);
      pollTask(data.task_id, data.seed);
    } catch (e) {
      if (pollingRef.current.size === 0) setBusy(false);
      pushLog(`提交失败: ${(e as Error).message}`);
      toast(`提交失败：${(e as Error).message}`, "err");
    }
  }

  const lyricLines = lyrics ? lyrics.split("\n").length : 0;
  const lyricChars = lyrics.replace(/\s/g, "").length;
  const quotaPct = user && user.quota_total > 0
    ? Math.min(100, Math.round(((user.quota_used + (user.in_flight || 0)) / user.quota_total) * 100))
    : 0;
  const inFlight = pollingRef.current.size;

  return (
    <div className="layout cols3">
      {/* 左：创作 */}
      <section className="panel studio-left">
        <div className="section-title">
          <h3 style={{ margin: 0 }}><Wand2 size={15} /> 创作灵感</h3>
          <button
            className="mini"
            onClick={() => { setTitle(PRESET.title); setStyle(PRESET.style); setLyrics(PRESET.lyrics); setCot(PRESET.cot); setSeed(PRESET.seed); toast("已填入示例《今晚不眠》"); }}
          >
            <Sparkles size={12} /> 试试《今晚不眠》
          </button>
        </div>

        <label className="lbl" htmlFor="f-title"><span>歌曲标题</span><span className={`count${title.length > limits.title ? " over" : ""}`}>{title.length} / {limits.title}</span></label>
        <input id="f-title" className="in" maxLength={limits.title} placeholder="给你的歌曲起个名字..." value={title} onChange={(e) => setTitle(e.target.value)} />

        <label className="lbl" htmlFor="f-style"><span>曲风描述 Style</span><span className={`count${style.length > limits.style ? " over" : ""}`}>{style.length} / {limits.style}</span></label>
        <textarea id="f-style" className="in" rows={3} maxLength={limits.style} placeholder="City Pop, upbeat, groovy bass..." value={style} onChange={(e) => setStyle(e.target.value)} />
        {/* 曲风预设：单行横向滚动胶囊 */}
        <div className="chips-row">
          {STYLE_PRESETS.map((p) => (
            <button key={p.name} className="chip" onClick={() => setStyle(p.style)} title={p.style}>{p.name}</button>
          ))}
        </div>

        <label className="lbl" htmlFor="f-lyrics"><span>歌词 Lyrics</span><span className="count">{lyricLines} 行 · {lyricChars} 字</span></label>
        <textarea id="f-lyrics" className="in lyrics-box" rows={8} maxLength={limits.lyrics} placeholder="[Verse]&#10;..." value={lyrics} onChange={(e) => setLyrics(e.target.value)} />

        {/* 高级设置：默认收起的小抽屉 */}
        <details className="adv">
          <summary>
            <span className="caret">▶</span> 高级设置
            <span className="adv-hint">Seed · 思维模式</span>
          </summary>
          <div className="adv-body">
            <div className="row">
              <div>
                <label className="lbl" htmlFor="f-seed"><span>随机种子</span></label>
                <div style={{ display: "flex", gap: 6 }}>
                  <input id="f-seed" className="in" type="number" min={0} max={2147483647} placeholder="留空随机" value={seed} onChange={(e) => setSeed(e.target.value)} style={{ fontFamily: "monospace" }} />
                  <button className="icon-btn" title="随机一个种子" onClick={randomSeed} style={{ width: 36, height: 36, borderColor: "rgba(255,255,255,0.08)", background: "#0d1019" }}><Dices size={15} /></button>
                </div>
              </div>
              <div>
                <label className="lbl"><span>思维模式</span></label>
                <div className="seg">
                  <button className={cot === "full" ? "on" : ""} onClick={() => setCot("full")}>Full · 推荐</button>
                  <button className={cot === "none" ? "on" : ""} onClick={() => setCot("none")}>None · 直出</button>
                </div>
              </div>
            </div>
            <div className="hint">相同 Seed + 相同词曲可复现结果；Full 质量更高，None 速度更快。</div>
          </div>
        </details>

        <div className="submit-bar">
          <button className={`btn${busy ? " busy" : ""}`} onClick={submit} disabled={busy}>
            {busy ? (busyText || "处理中...") : (<><Wand2 size={16} /> 开始生成全曲</>)}
          </button>
          {busy && <div className="progress indeterminate"><div /></div>}
          <div className="hint">多人共享显卡时自动排队，可连续提交多首{inFlight > 0 ? `（在途 ${inFlight}）` : ""}；草稿自动保存在本机。状态：{props.serverState}</div>
          {limits.requireKey && !user && <div className="hint">本站点需登录后才能生成，请先在右侧登录。</div>}
        </div>
      </section>

      {/* 中：播放器 */}
      <section className="studio-center" style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
        <div className="hero">
          <div className="hero-bg" aria-hidden>
            {current
              ? <Cover seed={current.seed} title={current.title} size={420} rounded={0} />
              : (
                <svg viewBox="0 0 400 220" preserveAspectRatio="xMidYMid slice">
                  <rect width="400" height="220" fill="#0d1019" />
                  <ellipse cx="200" cy="40" rx="220" ry="90" fill="#8b5cf6" opacity="0.14" />
                  <ellipse cx="200" cy="200" rx="260" ry="100" fill="#6366f1" opacity="0.1" />
                </svg>
              )}
          </div>
          <div className="hero-fg">
            <div className="hero-top">
              {current
                ? <span className="hero-cover"><Cover seed={current.seed} title={current.title} size={64} rounded={12} /></span>
                : <span className="hero-cover placeholder">🎧</span>}
              <div className="hero-meta">
                <div className="hero-title">
                  {playing && <span className="eq"><i /><i /><i /></span>}
                  <span className="title-text">{current ? current.title : "等待聆听"}</span>
                </div>
                <div className="hero-sub">
                  {current ? (
                    <>
                      <span>SEED {current.seed}</span>
                      <span>·</span><span>{current.cot === "full" ? "Full 深度创作" : "None 直出"}</span>
                      <span>·</span><span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Clock size={11} /> {current.created_at}</span>
                    </>
                  ) : "在右侧点一首歌，或在左侧开始你的第一首创作"}
                </div>
              </div>
              <div className="player-controls">
                <button className="play-btn" onClick={togglePlay} disabled={!current} title={playing ? "暂停" : "播放"}>
                  {playing ? <Pause /> : <Play style={{ marginLeft: 2 }} />}
                </button>
              </div>
            </div>
            <div className="wave-wrap">
              {current ? (
                <Waveform audioRef={audioRef} audioUrl={current.audio_url} playing={playing} />
              ) : (
                <div className="wave-fallback" aria-hidden>
                  {Array.from({ length: 48 }, (_, i) => 10 + ((i * 37) % 50)).map((h, i) => (
                    <i key={i} style={{ height: h, opacity: .5 }} />
                  ))}
                </div>
              )}
            </div>
            <div className="player-foot">
              {current && (
                <a className="mini" href={current.audio_url} download title="下载 FLAC" style={{ textDecoration: "none", padding: "8px 14px" }}>
                  <Download size={12} /> FLAC
                </a>
              )}
              <span className="hint" style={{ margin: 0 }}>
                {current ? (playing ? "正在播放…" : "已暂停，点击 ▶ 继续") : "暂无选中曲目"}
              </span>
            </div>
            {/* 真实 audio 元素：wavesurfer 绑定它，保证单音源 */}
            <audio ref={audioRef} preload="none" style={{ display: "none" }} />
          </div>
        </div>

        <div className="panel center-panel">
          <div className="subtabs">
            <button className={centerTab === "profile" ? "on" : ""} onClick={() => setCenterTab("profile")}><Disc3 size={12} /> 制作档案</button>
            <button className={centerTab === "logs" ? "on" : ""} onClick={() => setCenterTab("logs")}><Terminal size={12} /> 日志</button>
          </div>
          {centerTab === "profile" && (
            current ? (
              <div className="reader">
                <div className="reader-meta">
                  <span>种子 <b>{current.seed}</b></span>
                  <span>模式 <b>{current.cot}</b></span>
                  <span>编号 <b>{current.task_id}</b></span>
                  <span>归属 <b>{current.owner || "匿名"}</b></span>
                  <span>时间 <b>{current.created_at}</b></span>
                </div>
                <div className="reader-label">风格 Style</div>
                <div className="reader-body">{current.style}</div>
                <div className="reader-label">歌词 Lyrics</div>
                <div className="reader-body lyrics">{current.lyrics}</div>
              </div>
            ) : (<EmptyState emoji="💿" title="还没有选中曲目" sub="右侧历史里点一首，档案会出现在这里" />)
          )}
          {centerTab === "logs" && (
            <div className="logbox grow">{logs.map((l, i) => <div key={i}>{l}</div>)}</div>
          )}
        </div>
      </section>

      {/* 右：历史 */}
      <section className="panel studio-right" style={{ minWidth: 0 }}>
        <div className="section-title">
          <h3 style={{ margin: 0 }}><ListMusic size={15} /> 创作历史 ({total})</h3>
        </div>
        {user ? (
          <div className="userstrip compact">
            <span className="avatar"><User size={14} /></span>
            <span className="user-name">
              <span className="user-name-row">
                <span style={{ fontWeight: 800, color: "#fff", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{user.name}</span>
                <span style={{ color: "var(--mut)", whiteSpace: "nowrap", fontSize: 11 }}>{user.quota_total <= 0 ? "无限配额" : `剩 ${user.quota_left ?? "?"} 首`}{user.in_flight ? `（在途 ${user.in_flight}）` : ""}</span>
              </span>
              {user.quota_total > 0 && <span className="quota-bar"><div style={{ width: `${quotaPct}%` }} /></span>}
            </span>
            <span style={{ display: "flex", gap: 6, flexShrink: 0 }}>
              <button className={`mini${mineOnly ? " active" : ""}`} onClick={() => { setMineOnly(!mineOnly); setPage(0); }}>
                我的
              </button>
              <button className="icon-btn" title="退出登录" onClick={logout} style={{ borderColor: "rgba(255,255,255,0.08)", background: "#0d1019" }}><LogOut size={13} /></button>
            </span>
          </div>
        ) : (
          <div className="userstrip compact">
            <input className="in" style={{ width: 88, flexShrink: 0 }} placeholder="用户名" value={loginName} onChange={(e) => setLoginName(e.target.value)} />
            <input className="in" style={{ flex: 1, minWidth: 0 }} type="password" placeholder="Key（找管理员领取）" value={loginKey} onChange={(e) => setLoginKey(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") login(); }} />
            <button className="mini primary" onClick={login} style={{ flexShrink: 0 }}>登录</button>
          </div>
        )}
        <div style={{ position: "relative" }}>
          <Search size={13} style={{ position: "absolute", left: 11, top: 11, color: "#4b5468" }} />
          <input className="in" placeholder="搜索歌名或 Seed..." value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} style={{ paddingLeft: 30 }} />
        </div>
        <div className="hist-scroll">
          {histLoading && history.length === 0 && (<><div className="skel" /><div className="skel" /><div className="skel" /></>)}
          {!histLoading && history.length === 0 && (
            <EmptyState emoji="🎼" title="还没有作品" sub={query ? "换个关键词试试" : "左侧写好词曲，点生成开始第一首"} />
          )}
          {history.map((item) => (
            <div key={item.task_id} className={`hist-item${current?.task_id === item.task_id ? " playing" : ""}`} onClick={() => play(item)}>
              <Cover seed={item.seed} title={item.title} size={44} />
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="t">
                  {current?.task_id === item.task_id && playing
                    ? <span className="eq"><i /><i /><i /></span>
                    : <History size={12} style={{ color: "#5d6579", flexShrink: 0 }} />}
                  <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{item.title}</span>
                </div>
                <div className="s"><span>Seed {item.seed}</span>{item.owner && <span className="owner-tag">{item.owner}</span>}</div>
              </div>
              {canDelete(item) && (
                <button
                  className="icon-btn danger"
                  title="删除该曲目"
                  onClick={(e) => { e.stopPropagation(); removeItem(item.task_id); }}
                >
                  <Trash2 size={13} />
                </button>
              )}
            </div>
          ))}
        </div>
        <div className="pager">
          <button className="mini" disabled={page <= 0} onClick={() => setPage(page - 1)}>上一页</button>
          <span className="hint">第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页</span>
          <button className="mini" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      </section>

      {/* 移动端迷你播放条 */}
      <div className={`minibar${current ? " show" : ""}`}>
        {current && (
          <>
            <Cover seed={current.seed} title={current.title} size={38} rounded={10} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 800, color: "#fff", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{current.title}</div>
              <div style={{ fontSize: 11, color: "var(--mut)", fontFamily: "monospace" }}>Seed {current.seed}</div>
            </div>
            <button className="play-btn" style={{ width: 38, height: 38 }} onClick={togglePlay}>
              {playing ? <Pause size={16} /> : <Play size={16} style={{ marginLeft: 2 }} />}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
