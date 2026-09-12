import { useCallback, useEffect, useRef, useState } from "react";
import {
  Cpu,
  HardDrive,
  KeyRound,
  ListOrdered,
  LogOut,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Terminal,
  XCircle,
} from "lucide-react";
import { apiFetch, formatBytes, getAdminToken, setAdminToken } from "../lib/api";
import { useModal } from "../components/Modal";
import { useToast } from "../components/Toast";
import { EmptyState, StatCard } from "../components/Stat";

interface QueueResp {
  pending_count: number;
  running: { task_id: string; title?: string; status: string } | null;
  pending: { task_id: string; title?: string; status: string; queue_position?: number }[];
  max_queue: number;
}

interface KeyRow {
  id: number;
  name: string;
  key_prefix: string;
  quota_total: number;
  used_count: number;
  enabled: number;
  note: string;
  created_at: string;
  last_used_at: string;
}

type Tab = "queue" | "disk" | "logs" | "config" | "keys";
const TABS: { id: Tab; name: string; icon: typeof ListOrdered }[] = [
  { id: "queue", name: "队列", icon: ListOrdered },
  { id: "disk", name: "磁盘", icon: HardDrive },
  { id: "logs", name: "日志", icon: Terminal },
  { id: "config", name: "配置", icon: Settings2 },
  { id: "keys", name: "密钥", icon: KeyRound },
];

export default function Admin() {
  const [token, setToken] = useState(getAdminToken());
  const [authed, setAuthed] = useState(!!getAdminToken());
  const [tab, setTab] = useState<Tab>("queue");
  const [queue, setQueue] = useState<QueueResp | null>(null);
  const [health, setHealth] = useState<{ model_loaded: boolean; model_error?: string; worker_alive?: boolean } | null>(null);
  const [disk, setDisk] = useState<Record<string, number | string> | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [maxQueue, setMaxQueue] = useState("10");
  const [submitPerHour, setSubmitPerHour] = useState("20");
  const [maxPendingPerIp, setMaxPendingPerIp] = useState("2");
  const [maxPendingPerKey, setMaxPendingPerKey] = useState("2");
  const [requireKey, setRequireKey] = useState(false);
  const [err, setErr] = useState("");
  const modal = useModal();
  const { toast } = useToast();
  // keys 标签状态
  const [keys, setKeys] = useState<KeyRow[]>([]);
  const [newName, setNewName] = useState("");
  const [newQuota, setNewQuota] = useState("10");
  const [newNote, setNewNote] = useState("");
  const [createdSecret, setCreatedSecret] = useState<string | null>(null);
  const [selId, setSelId] = useState<number | null>(null);
  const [editQuota, setEditQuota] = useState("");
  const [editEnabled, setEditEnabled] = useState(true);
  const [editNote, setEditNote] = useState("");
  const [keyHist, setKeyHist] = useState<{ name: string; total: number; items: { task_id: string; title: string; created_at: string }[] } | null>(null);
  // config 输入框只在首次加载时从服务端回填，之后 5s 自动刷新不再覆盖用户输入
  const cfgSynced = useRef(false);

  const load = useCallback(async () => {
    setErr("");
    try {
      if (tab === "queue") {
        setQueue(await apiFetch<QueueResp>("/api/admin/queue", {}, true));
        setHealth(await apiFetch<{ model_loaded: boolean; model_error?: string; worker_alive?: boolean }>("/api/admin/health", {}, true));
      }
      if (tab === "disk") setDisk(await apiFetch<Record<string, number | string>>("/api/admin/disk", {}, true));
      if (tab === "logs") {
        const d = await apiFetch<{ items: string[] }>("/api/admin/logs?tail=200", {}, true);
        setLogs(d.items || []);
      }
      if (tab === "config") {
        const d = await apiFetch<{ config: Record<string, unknown> }>("/api/admin/config", {}, true);
        setCfg(d.config);
        if (!cfgSynced.current) {
          cfgSynced.current = true;
          if (d.config.max_queue !== undefined) setMaxQueue(String(d.config.max_queue));
          if (d.config.submit_per_hour !== undefined) setSubmitPerHour(String(d.config.submit_per_hour));
          if (d.config.max_pending_per_ip !== undefined) setMaxPendingPerIp(String(d.config.max_pending_per_ip));
          if (d.config.max_pending_per_key !== undefined) setMaxPendingPerKey(String(d.config.max_pending_per_key));
          if (d.config.require_api_key !== undefined) setRequireKey(d.config.require_api_key === true);
        }
      }
      if (tab === "keys") {
        const d = await apiFetch<{ total: number; items: KeyRow[] }>("/api/admin/keys", {}, true);
        setKeys(d.items || []);
      }
    } catch (e) {
      const msg = (e as Error).message;
      setErr(msg);
      if (msg.includes("401") || msg.includes("403") || msg.includes("鉴权") || msg.includes("启用")) setAuthed(false);
    }
  }, [tab]);

  useEffect(() => { if (authed) load(); }, [authed, load]);
  useEffect(() => {
    if (!authed) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [authed, load]);

  async function login() {
    const tok = token.trim();
    if (!tok) return;
    setErr("");
    try {
      // 登录即探活：token 错立刻提示，不放行进空白页
      const res = await fetch("/api/admin/queue", { headers: { Authorization: `Bearer ${tok}` } });
      if (!res.ok) {
        const detail = await res.json().then((j) => j.detail).catch(() => `HTTP ${res.status}`);
        throw new Error(typeof detail === "string" ? detail : `HTTP ${res.status}`);
      }
      setAdminToken(tok);
      setAuthed(true);
      toast("已进入管理后台 🛠️");
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function cancel(id: string) {
    if (!(await modal.confirm(`取消排队任务 ${id}？`))) return;
    try {
      await apiFetch(`/api/admin/tasks/${id}/cancel`, { method: "POST" }, true);
      toast("已取消该排队任务");
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  async function saveConfig() {
    const body: Record<string, number | boolean> = {};
    const mq = Number(maxQueue);
    const sph = Number(submitPerHour);
    const ppi = Number(maxPendingPerIp);
    const ppk = Number(maxPendingPerKey);
    if (!Number.isInteger(mq) || mq < 1 || mq > 100) {
      await modal.alert("max_queue 必须是 1~100 的整数");
      return;
    }
    body.max_queue = mq;
    if (!Number.isInteger(sph) || sph < 0 || sph > 10000) {
      await modal.alert("submit_per_hour 必须是不超过 10000 的整数（0 表示不限）");
      return;
    }
    body.submit_per_hour = sph;
    if (!Number.isInteger(ppi) || ppi < 0 || ppi > 100) {
      await modal.alert("max_pending_per_ip 必须是不超过 100 的整数（0 表示不限）");
      return;
    }
    body.max_pending_per_ip = ppi;
    if (!Number.isInteger(ppk) || ppk < 0 || ppk > 100) {
      await modal.alert("max_pending_per_key 必须是不超过 100 的整数（0 表示不限，防一人塞满队列）");
      return;
    }
    body.max_pending_per_key = ppk;
    body.require_api_key = requireKey;
    try {
      await apiFetch("/api/admin/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }, true);
      toast("配置已热更新 ✅");
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  async function createKey() {
    const name = newName.trim();
    const quota = Number(newQuota);
    if (!name) {
      await modal.alert("用户名不能为空");
      return;
    }
    if (name.includes(":") || /\s/.test(name)) {
      await modal.alert("用户名不能含冒号或空白（鉴权格式为 用户名:Key）");
      return;
    }
    if (!Number.isInteger(quota) || quota < 0 || quota > 100000) {
      await modal.alert("配额必须是不超过 100000 的整数（0 表示不限）");
      return;
    }
    try {
      const res = await apiFetch<{ api_key: string }>("/api/admin/keys", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, quota_total: quota, note: newNote.trim() }),
      }, true);
      setCreatedSecret(res.api_key);
      setNewName("");
      setNewNote("");
      toast(`已为「${name}」创建 Key 🎉`);
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  function selectKey(row: KeyRow) {
    setSelId(row.id);
    setEditQuota(String(row.quota_total));
    setEditEnabled(row.enabled !== 0);
    setEditNote(row.note || "");
    setKeyHist(null);
    apiFetch<{ name: string; total: number; items: { task_id: string; title: string; created_at: string }[] }>(
      `/api/admin/keys/${row.id}/history?limit=20&offset=0`, {}, true,
    ).then(setKeyHist).catch(() => setKeyHist(null));
  }

  async function saveKey() {
    if (selId === null) return;
    const quota = Number(editQuota);
    if (!Number.isInteger(quota) || quota < 0 || quota > 100000) {
      await modal.alert("配额必须是不超过 100000 的整数（0 表示不限）");
      return;
    }
    try {
      await apiFetch(`/api/admin/keys/${selId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ quota_total: quota, enabled: editEnabled, note: editNote }),
      }, true);
      toast("已保存 ✅");
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  async function delKey(id: number, name: string) {
    if (!(await modal.confirm(`删除 Key「${name}」？其历史歌曲保留，之后该用户无法再登录/生成。`))) return;
    try {
      await apiFetch(`/api/admin/keys/${id}`, { method: "DELETE" }, true);
      if (selId === id) {
        setSelId(null);
        setKeyHist(null);
      }
      toast(`已删除「${name}」的 Key`);
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  async function resetKey(id: number, name: string) {
    if (!(await modal.confirm(`给「${name}」换一个新的 Key？旧 Key 立即失效，配额与历史保留。新 Key 只显示一次，请立即发给用户。`))) return;
    try {
      const res = await apiFetch<{ api_key: string }>(`/api/admin/keys/${id}/reset`, { method: "POST" }, true);
      setCreatedSecret(res.api_key);
      toast(`已为「${name}」换新 Key，请立即复制`);
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  if (!authed) {
    return (
      <div className="layout" style={{ maxWidth: 480 }}>
        <section className="panel" style={{ textAlign: "center" }}>
          <div style={{ fontSize: 40 }}>🛡️</div>
          <h3 style={{ justifyContent: "center", fontSize: 16, color: "#fff", marginTop: 8 }}><ShieldCheck size={16} /> 管理登录</h3>
          <div className="hint">输入后端 ADMIN_TOKEN（请求头 Bearer）。未设置则管理接口禁用，属正常安全默认。</div>
          <input
            className="in"
            type="password"
            placeholder="ADMIN_TOKEN"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") login(); }}
            style={{ marginTop: 10, fontFamily: "monospace" }}
          />
          <div style={{ marginTop: 10 }}>
            <button className="btn" onClick={login}>进入管理</button>
          </div>
          {err && <div className="hint" style={{ color: "#fda4af" }}>{err}</div>}
        </section>
      </div>
    );
  }

  const diskUsed = Number(disk?.disk_used_bytes ?? 0);
  const diskTotal = Number(disk?.disk_total_bytes ?? 0);
  const diskPct = diskTotal > 0 ? Math.min(100, Math.round((diskUsed / diskTotal) * 100)) : 0;

  return (
    <div className="layout">
      <section className="panel">
        <div className="section-title" style={{ flexWrap: "wrap", gap: 8 }}>
          <div className="tabs" style={{ margin: 0 }}>
            {TABS.map((t) => {
              const Icon = t.icon;
              return (
                <button key={t.id} className={tab === t.id ? "active" : ""} onClick={() => setTab(t.id)}>
                  <Icon size={13} /> {t.name}
                </button>
              );
            })}
          </div>
          <div style={{ display: "flex", gap: 6 }}>
            <button className="mini" onClick={load} title="刷新"><RefreshCw size={12} /> 刷新</button>
            <button className="mini" onClick={() => { setAdminToken(""); setAuthed(false); }} title="退出管理"><LogOut size={12} /> 退出</button>
          </div>
        </div>
        {err && <div className="hint" style={{ color: "#fda4af" }}>{err}</div>}

        {tab === "queue" && (
          <>
            <div className="stat-grid">
              <StatCard icon={<ListOrdered size={18} />} tint="rgba(168,85,247,.18)" value={`${queue?.pending_count ?? "-"} / ${queue?.max_queue ?? "-"}`} label="排队 / 上限" />
              <StatCard icon={<Cpu size={18} />} tint={queue?.running ? "rgba(251,191,36,.15)" : "rgba(52,211,153,.15)"} value={queue?.running ? "生成中" : "空闲"} label={queue?.running ? (queue.running.title || queue.running.task_id) : "GPU 状态"} />
              <StatCard icon={<ShieldCheck size={18} />} tint={health?.model_loaded ? "rgba(52,211,153,.15)" : "rgba(251,113,133,.15)"} value={health ? (health.model_loaded ? "已加载" : "未加载") : "-"} label={`模型 · worker ${health?.worker_alive ? "存活" : "异常"}`} />
            </div>
            {health && !health.model_loaded && health.model_error && (
              <div className="health-banner bad"><XCircle size={15} /> {health.model_error.slice(0, 200)}</div>
            )}
            <h3>⚡ 正在生成</h3>
            {queue?.running
              ? <div className="pre" style={{ borderColor: "rgba(251,191,36,.4)" }}>🎵 {queue.running.task_id} · {queue.running.title}<div className="progress indeterminate" style={{ marginTop: 8 }}><div /></div></div>
              : <EmptyState emoji="☕" title="GPU 空闲" sub="暂无正在生成的任务" />}
            <h3 style={{ marginTop: 12 }}>📋 等待队列 ({queue?.pending.length ?? 0})</h3>
            {queue && queue.pending.length > 0 ? (
              <table className="tbl">
                <thead><tr><th>task</th><th>title</th><th>pos</th><th>op</th></tr></thead>
                <tbody>
                  {queue.pending.map((p) => (
                    <tr key={p.task_id}>
                      <td style={{ fontFamily: "monospace" }}>{p.task_id}</td><td>{p.title}</td><td>{p.queue_position ?? "-"}</td>
                      <td><button className="mini danger" onClick={() => cancel(p.task_id)}>取消</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <div className="hint">队列为空 🎉</div>}
          </>
        )}

        {tab === "disk" && (
          disk ? (
            <>
              <div className="stat-grid">
                <StatCard icon={<HardDrive size={18} />} tint="rgba(59,130,246,.15)" value={formatBytes(diskUsed)} label={`已用 / 共 ${formatBytes(diskTotal)}`} />
                <StatCard icon={<ListOrdered size={18} />} tint="rgba(168,85,247,.15)" value={String(disk.song_count ?? disk.file_count ?? "-")} label="曲目文件数" />
              </div>
              <div className="disk-bar"><div style={{ width: `${diskPct}%` }} /></div>
              <div className="hint">{diskPct}% 已用</div>
              <table className="tbl" style={{ marginTop: 10 }}>
                <tbody>
                  {Object.entries(disk).map(([k, v]) => (
                    <tr key={k}><td style={{ fontFamily: "monospace", color: "#8b94a7" }}>{k}</td><td>{typeof v === "number" && (k.includes("bytes") || k.startsWith("disk")) ? formatBytes(v) : String(v)}</td></tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : <div className="hint">加载中…</div>
        )}

        {tab === "logs" && (
          <div className="logbox" style={{ height: 420 }}>{logs.map((l, i) => <div key={i}>{l}</div>)}</div>
        )}

        {tab === "config" && cfg && (
          <>
            <div className="health-banner ok">⚡ 热更新立即生效；重启后以环境变量为准。登录用户走 Key 配额 + 每用户并存上限，不再叠加 IP 限制。</div>
            <div className="row">
              <div>
                <label className="lbl" htmlFor="cfg-max-queue"><span>max_queue · 全局排队上限</span></label>
                <input id="cfg-max-queue" className="in" inputMode="numeric" value={maxQueue} onChange={(e) => setMaxQueue(e.target.value)} style={{ fontFamily: "monospace" }} />
              </div>
              <div>
                <label className="lbl" htmlFor="cfg-submit"><span>submit_per_hour · 每 IP 每小时</span></label>
                <input id="cfg-submit" className="in" inputMode="numeric" value={submitPerHour} onChange={(e) => setSubmitPerHour(e.target.value)} style={{ fontFamily: "monospace" }} />
              </div>
            </div>
            <div className="row">
              <div>
                <label className="lbl" htmlFor="cfg-per-ip"><span>max_pending_per_ip · 匿名并存</span></label>
                <input id="cfg-per-ip" className="in" inputMode="numeric" value={maxPendingPerIp} onChange={(e) => setMaxPendingPerIp(e.target.value)} style={{ fontFamily: "monospace" }} />
              </div>
              <div>
                <label className="lbl" htmlFor="cfg-per-key"><span>max_pending_per_key · 每用户并存</span></label>
                <input id="cfg-per-key" className="in" inputMode="numeric" value={maxPendingPerKey} onChange={(e) => setMaxPendingPerKey(e.target.value)} style={{ fontFamily: "monospace" }} />
              </div>
            </div>
            <label className="lbl" htmlFor="cfg-require-key" style={{ display: "flex", alignItems: "center", gap: 8, textTransform: "none", fontSize: 12.5 }}>
              <input id="cfg-require-key" type="checkbox" checked={requireKey} onChange={(e) => setRequireKey(e.target.checked)} style={{ width: 16, height: 16, accentColor: "#a855f7" }} />
              require_api_key：强制登录后才能生成（防公网滥用）
            </label>
            <div style={{ marginTop: 10, display: "flex", gap: 8 }}>
              <button className="btn sm" onClick={saveConfig} style={{ width: "auto", padding: "9px 28px" }}>💾 保存配置</button>
            </div>
            <details style={{ marginTop: 12 }}>
              <summary className="hint" style={{ cursor: "pointer" }}>查看服务端原始 JSON</summary>
              <div className="pre" style={{ marginTop: 6 }}>{JSON.stringify(cfg, null, 2)}</div>
            </details>
          </>
        )}

        {tab === "keys" && (
          <>
            <h3>✨ 新建 Key <span className="hint" style={{ margin: 0 }}>明文只显示一次，请立即复制发给用户</span></h3>
            {createdSecret && (
              <div className="pre" style={{ borderColor: "#a855f7", fontFamily: "monospace" }}>
                🎉 新 Key（仅此一次）：{createdSecret}
              </div>
            )}
            <div className="row" style={{ gridTemplateColumns: "1fr 1fr 2fr auto", alignItems: "end" }}>
              <div>
                <label className="lbl" htmlFor="key-name">用户名</label>
                <input id="key-name" className="in" maxLength={32} placeholder="如 zhangsan" value={newName} onChange={(e) => setNewName(e.target.value)} />
              </div>
              <div>
                <label className="lbl" htmlFor="key-quota">可生成首数（0=不限）</label>
                <input id="key-quota" className="in" inputMode="numeric" value={newQuota} onChange={(e) => setNewQuota(e.target.value)} style={{ fontFamily: "monospace" }} />
              </div>
              <div>
                <label className="lbl" htmlFor="key-note">备注</label>
                <input id="key-note" className="in" maxLength={200} placeholder="如 市场部试用" value={newNote} onChange={(e) => setNewNote(e.target.value)} />
              </div>
              <button className="mini primary" onClick={createKey} style={{ padding: "9px 18px" }}>＋ 新建</button>
            </div>

            <h3 style={{ marginTop: 14 }}>🔑 Key 列表 ({keys.length})</h3>
            {keys.length > 0 ? (
              <table className="tbl">
                <thead><tr><th>用户</th><th>前缀</th><th>已用/配额</th><th>状态</th><th>注册时间</th><th>op</th></tr></thead>
                <tbody>
                  {keys.map((k) => (
                    <tr key={k.id} style={selId === k.id ? { background: "rgba(109,40,217,.15)" } : undefined}>
                      <td style={{ fontWeight: 700, color: "#fff" }}>{k.name}</td>
                      <td style={{ fontFamily: "monospace" }}>{k.key_prefix}…</td>
                      <td>{k.used_count}/{k.quota_total <= 0 ? "不限" : k.quota_total}</td>
                      <td>{k.enabled !== 0 ? "✅ 启用" : "⛔ 停用"}</td>
                      <td style={{ fontSize: 11, color: "#8b94a7" }}>{k.created_at}</td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <button className="mini" onClick={() => selectKey(k)}>管理</button>{" "}
                        <button className="mini" onClick={() => resetKey(k.id, k.name)}>换Key</button>{" "}
                        <button className="mini danger" onClick={() => delKey(k.id, k.name)}>删</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <EmptyState emoji="🔑" title="还没有 Key" sub="在上面新建第一个用户 Key" />}

            {selId !== null && (
              <div style={{ marginTop: 12, background: "rgba(2,6,23,.5)", border: "1px solid var(--line2)", borderRadius: 14, padding: 12 }}>
                <h3>⚙️ 编辑配额 / 启停</h3>
                <div className="row" style={{ gridTemplateColumns: "1fr 1fr 2fr auto", alignItems: "end" }}>
                  <div>
                    <label className="lbl" htmlFor="key-edit-quota">配额（0=不限）</label>
                    <input id="key-edit-quota" className="in" inputMode="numeric" value={editQuota} onChange={(e) => setEditQuota(e.target.value)} style={{ fontFamily: "monospace" }} />
                  </div>
                  <div>
                    <label className="lbl" htmlFor="key-edit-enabled" style={{ display: "flex", alignItems: "center", gap: 8, textTransform: "none" }}>
                      <input id="key-edit-enabled" type="checkbox" checked={editEnabled} onChange={(e) => setEditEnabled(e.target.checked)} style={{ width: 16, height: 16, accentColor: "#a855f7" }} />
                      启用
                    </label>
                  </div>
                  <div>
                    <label className="lbl" htmlFor="key-edit-note">备注</label>
                    <input id="key-edit-note" className="in" maxLength={200} value={editNote} onChange={(e) => setEditNote(e.target.value)} />
                  </div>
                  <button className="mini primary" onClick={saveKey} style={{ padding: "9px 18px" }}>保存</button>
                </div>
                <h3 style={{ marginTop: 12 }}>🎵 该用户的歌{keyHist ? ` (${keyHist.total})` : ""}</h3>
                {keyHist ? (
                  keyHist.items.length === 0 ? <div className="hint">还没有作品。</div> : (
                    <table className="tbl">
                      <thead><tr><th>task</th><th>title</th><th>time</th></tr></thead>
                      <tbody>
                        {keyHist.items.map((h) => (
                          <tr key={h.task_id}><td style={{ fontFamily: "monospace" }}>{h.task_id}</td><td>{h.title}</td><td style={{ fontSize: 11, color: "#8b94a7" }}>{h.created_at}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  )
                ) : <div className="hint">加载中…</div>}
              </div>
            )}
          </>
        )}
      </section>
    </div>
  );
}
