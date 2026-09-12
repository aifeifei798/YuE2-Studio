import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch, formatBytes, getAdminToken, setAdminToken } from "../lib/api";
import { useModal } from "../components/Modal";

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
  const [health, setHealth] = useState<{ model_loaded: boolean; model_error?: string; worker_alive?: boolean } | null>(null);
  const [disk, setDisk] = useState<Record<string, number | string> | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [cfg, setCfg] = useState<Record<string, unknown> | null>(null);
  const [maxQueue, setMaxQueue] = useState("10");
  const [submitPerHour, setSubmitPerHour] = useState("20");
  const [maxPendingPerIp, setMaxPendingPerIp] = useState("2");
  const [err, setErr] = useState("");
  const modal = useModal();
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
        }
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
    } catch (e) {
      setErr((e as Error).message);
    }
  }

  async function cancel(id: string) {
    if (!(await modal.confirm(`取消排队任务 ${id}？`))) return;
    try {
      await apiFetch(`/api/admin/tasks/${id}/cancel`, { method: "POST" }, true);
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
    }
  }

  async function saveConfig() {
    const body: Record<string, number> = {};
    const mq = Number(maxQueue);
    const sph = Number(submitPerHour);
    const ppi = Number(maxPendingPerIp);
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
    try {
      await apiFetch("/api/admin/config", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }, true);
      await modal.alert("已更新（重启后以环境变量为准）");
      load();
    } catch (e) {
      await modal.alert((e as Error).message);
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
            {health && (
              <div className="hint">
                模型：{health.model_loaded ? "已加载" : "未加载"} · worker：{health.worker_alive ? "存活" : "异常"}
                {!health.model_loaded && health.model_error ? ` · ${health.model_error.slice(0, 200)}` : ""}
              </div>
            )}
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
            <label className="lbl" htmlFor="cfg-max-queue">max_queue：全局排队上限（1~100）</label>
            <input id="cfg-max-queue" className="in" inputMode="numeric" value={maxQueue} onChange={(e) => setMaxQueue(e.target.value)} />
            <label className="lbl" htmlFor="cfg-submit">submit_per_hour：每 IP 每小时提交次数（0=不限）</label>
            <input id="cfg-submit" className="in" inputMode="numeric" value={submitPerHour} onChange={(e) => setSubmitPerHour(e.target.value)} />
            <label className="lbl" htmlFor="cfg-per-ip">max_pending_per_ip：每 IP 最大并存任务数（0=不限）</label>
            <input id="cfg-per-ip" className="in" inputMode="numeric" value={maxPendingPerIp} onChange={(e) => setMaxPendingPerIp(e.target.value)} />
            <div className="hint">热更新立即生效；重启后以环境变量为准。无账号体系下 IP 即用户。</div>
            <div style={{ marginTop: 8 }}><button className="btn ghost" onClick={saveConfig}>保存</button></div>
          </>
        )}
      </section>
    </div>
  );
}
