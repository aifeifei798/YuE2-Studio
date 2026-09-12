import { useCallback, useEffect, useRef, useState } from "react";
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
  const [page, setPage] = useState(0);
  const [current, setCurrent] = useState<HistoryRecord | null>(null);
  const [logs, setLogs] = useState<string[]>(["[Ready] 系统就绪，等待指令"]);
  const [user, setUser] = useState<QuotaInfo | null>(null);
  const [loginName, setLoginName] = useState("");
  const [loginKey, setLoginKey] = useState("");
  const [mineOnly, setMineOnly] = useState(false);
  const modal = useModal();
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
    const el = audioRef.current;
    if (el) {
      el.src = item.audio_url;
      el.play().catch(() => undefined);
    }
    pushLog(`正在播放: 《${item.title}》`);
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
    } catch (e) {
      pushLog(`删除失败: ${(e as Error).message}`);
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
            // 回到首页并重载（服务端分页下不再本地 unshift）
            backToFirstPage();
            refreshQuota();
            play(t.record);
            props.refreshServer();
          } else if (t.status === "failed") {
            pollingRef.current.delete(taskId);
            pushLog(`任务 ${taskId} 生成失败: ${t.error || "未知错误"}`);
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
      pollTask(data.task_id, data.seed);
    } catch (e) {
      if (pollingRef.current.size === 0) setBusy(false);
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
        <input id="f-title" className="in" maxLength={limits.title} placeholder="给你的歌曲起个名字..." value={title} onChange={(e) => setTitle(e.target.value)} />
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
        <label className="lbl" htmlFor="f-style">曲风描述（{style.length} / {limits.style}）</label>
        <textarea id="f-style" className="in" rows={2} maxLength={limits.style} value={style} onChange={(e) => setStyle(e.target.value)} />
        <label className="lbl" htmlFor="f-lyrics">歌词（{lyricLines} 行 · {lyricChars} 字）</label>
        <textarea id="f-lyrics" className="in" rows={12} maxLength={limits.lyrics} value={lyrics} onChange={(e) => setLyrics(e.target.value)} />
        <div style={{ marginTop: 10 }}>
          <button className="btn" onClick={submit}>{busy ? busyText || "处理中..." : "开始生成全曲"}</button>
          <div className="hint">多人共享显卡时自动排队，可连续提交多首；草稿自动保存在本机。状态：{props.serverState}</div>
          {limits.requireKey && !user && <div className="hint">本站点需登录后才能生成，请先在右侧登录。</div>}
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
        {user ? (
          <div className="userstrip">
            <span>👤 {user.name} · 配额 {user.quota_used}/{user.quota_total <= 0 ? "不限" : user.quota_total}{user.in_flight ? `（在途 ${user.in_flight}）` : ""}</span>
            <span style={{ display: "flex", gap: 6 }}>
              <button className={`mini${mineOnly ? " active" : ""}`} onClick={() => { setMineOnly(!mineOnly); setPage(0); }}>
                {mineOnly ? "只看我的 ✓" : "只看我的"}
              </button>
              <button className="mini" onClick={logout}>退出</button>
            </span>
          </div>
        ) : (
          <div className="userstrip">
            <input className="in" style={{ width: 110 }} placeholder="用户名" value={loginName} onChange={(e) => setLoginName(e.target.value)} />
            <input className="in" style={{ flex: 1 }} type="password" placeholder="Key（找管理员领取）" value={loginKey} onChange={(e) => setLoginKey(e.target.value)} />
            <button className="mini" onClick={login}>登录</button>
          </div>
        )}
        <input className="in" placeholder="搜索歌名或 Seed..." value={query} onChange={(e) => { setQuery(e.target.value); setPage(0); }} />
        <div style={{ marginTop: 10 }}>
          {history.length === 0 && <div className="hint">无匹配的曲目</div>}
          {history.map((item) => (
            <div key={item.task_id} className={`hist-item${current?.task_id === item.task_id ? " playing" : ""}`} onClick={() => play(item)}>
              <div style={{ minWidth: 0 }}>
                <div className="t">{item.title}</div>
                <div className="s">Seed: {item.seed}</div>
              </div>
              {canDelete(item) && <button className="mini danger" onClick={(e) => { e.stopPropagation(); removeItem(item.task_id); }}>删</button>}
            </div>
          ))}
        </div>
        <div className="pager">
          <button className="mini" disabled={page <= 0} onClick={() => setPage(page - 1)}>上一页</button>
          <span className="hint">第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(total / PAGE_SIZE))} 页</span>
          <button className="mini" disabled={(page + 1) * PAGE_SIZE >= total} onClick={() => setPage(page + 1)}>下一页</button>
        </div>
      </section>
    </div>
  );
}
