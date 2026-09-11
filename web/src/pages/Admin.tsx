import { useCallback, useEffect, useState } from "react";
import { apiFetch, formatBytes, getAdminToken, setAdminToken } from "../lib/api";

interface QueueResp {
  pending_count: number;
  running: { task_id: string; title?: string; status: string } | null;
  pending: { task_id: string; title?: string; status: string; queue_position?: number }[];
  max_queue: number;
}

export default function Admin() {
  const [token, setToken] = useState(getAdminToken());
  const [authed, setAuthed] = useState(!!getAdminToken());
  const [tab, setTab] = useState<"queue" | "disk" | "logs" | "config">("queue");
  const [queue, setQueue] = useState<QueueResp | null>(null);
  const [disk, setDisk] = useState<Record<string, number | string> | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [maxQueue, setMaxQueue] = useState("10");
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      if (tab === "queue") setQueue(await apiFetch<QueueResp>("/api/admin/queue", {}, true));
      if (tab === "disk") setDisk(await apiFetch<Record<string, number | string>>("/api/admin/disk", {}, true));
      if (tab === "logs") {
        const d = await apiFetch<{ items: string[] }>("/api/admin/logs?tail=200", {}, true);
        setLogs(d.items || []);
      }
      if (tab === "config") {
        const d = await apiFetch<{ config: Record<string, unknown> }>("/api/admin/config", {}, true);
        setCfg(d.config);
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

  function login() {
    setAdminToken(token.trim());
    setAuthed(!!token.trim());
    setErr("");
  }

  async function cancel(id: string) {
    if (!confirm(`取消排队任务 ${id}？`)) return;
    try {
      await apiFetch(`/api/admin/tasks/${id}/cancel`, { method: "POST" }, true);
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  async function saveConfig() {
    try {
      await apiFetch("/api/admin/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_queue: Number(maxQueue) }),
      }, true);
      alert("已更新");
      load();
    } catch (e) {
      alert((e as Error).message);
    }
  }

  if (!authed) {
    return (
      <div className="layout" style={{ maxWidth: 480 }}>
        <section className="panel">
          <h3>管理登录</h3>
          <div className="hint">输入后端 ADMIN_TOKEN（请求头 Bearer）。未设置则管理接口禁用，属正常安全默认。</div>
          <input className="in" type="password" placeholder="ADMIN_TOKEN" value={token} onChange={(e) => setToken(e.target.value)} />
          <div style={{ marginTop: 10 }}>
            <button className="btn" onClick={login}>进入管理</button>
          </div>
          {err && <div className="hint">{err}</div>}
        </section>
      </div>
    );
  }

  return (
    <div className="layout">
      <section className="panel">
        <div className="tabs">
          {(["queue", "disk", "logs", "config"] as const).map((t) => (
            <button key={t} className={tab === t ? "active" : ""} onClick={() => setTab(t)}>{t}</button>
          ))}
          <button onClick={() => { setAdminToken(""); setAuthed(false); }}>退出</button>
          <button className="mini" onClick={load}>刷新</button>
        </div>
        {err && <div className="hint">{err}</div>}

        {tab === "queue" && queue && (
          <>
            <div className="hint">排队 {queue.pending_count} / 上限 {queue.max_queue}</div>
            <h3>Running</h3>
            {queue.running ? <div className="pre">{queue.running.task_id} · {queue.running.title}</div> : <div className="hint">空闲</div>}
            <h3>Pending</h3>
            <table className="tbl">
              <thead><tr><th>task</th><th>title</th><th>pos</th><th>op</th></tr></thead>
              <tbody>
                {queue.pending.map((p) => (
                  <tr key={p.task_id}>
                    <td>{p.task_id}</td><td>{p.title}</td><td>{p.queue_position ?? "-"}</td>
                    <td><button className="mini danger" onClick={() => cancel(p.task_id)}>取消</button></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}

        {tab === "disk" && disk && (
          <table className="tbl">
            <tbody>
              {Object.entries(disk).map(([k, v]) => (
                <tr key={k}><td>{k}</td><td>{typeof v === "number" && (k.includes("bytes") || k.startsWith("disk")) ? formatBytes(v) : String(v)}</td></tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === "logs" && <div className="logbox" style={{ height: 400 }}>{logs.map((l, i) => <div key={i}>{l}</div>)}</div>}

        {tab === "config" && cfg && (
          <>
            <div className="pre">{JSON.stringify(cfg, null, 2)}</div>
            <label className="lbl">max_queue（1~100，重启后以环境变量为准）</label>
            <input className="in" value={maxQueue} onChange={(e) => setMaxQueue(e.target.value)} />
            <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={saveConfig}>保存</button></div>
          </>
        )}
      </section>
    </div>
  );
}
