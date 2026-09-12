import { useCallback, useEffect, useState } from "react";
import Studio from "./pages/Studio";
import Admin from "./pages/Admin";
import { ModalProvider } from "./components/Modal";
import "./styles.css";

function route(): string {
  return window.location.hash.replace(/^#/, "") || "/";
}

export default function App() {
  const [path, setPath] = useState(route());
  const [serverState, setServerState] = useState("连接中…");
  const [serverOk, setServerOk] = useState(false);

  useEffect(() => {
    const fn = () => setPath(route());
    window.addEventListener("hashchange", fn);
    return () => window.removeEventListener("hashchange", fn);
  }, []);

  const refreshServer = useCallback(async () => {
    try {
      const h = await fetch("/healthz").then((r) => r.json());
      setServerOk(!!h.model_loaded);
      setServerState(h.model_loaded ? `在线${h.queue_pending ? ` · 排队 ${h.queue_pending}` : ""}` : "模型加载中…");
    } catch {
      setServerOk(false);
      setServerState("连接失败");
    }
  }, []);

  useEffect(() => {
    refreshServer();
    const t = setInterval(refreshServer, 15000);
    return () => clearInterval(t);
  }, [refreshServer]);

  const isAdmin = path.startsWith("/admin");

  return (
    <ModalProvider>
      <header className="topbar">
        <div className="brand">YuE2 Studio <span className="badge">Studio + Admin</span></div>
        <nav className="nav">
          <a href="#/" className={!isAdmin ? "active" : ""}>创作</a>
          <a href="#/admin" className={isAdmin ? "active" : ""}>管理</a>
        </nav>
        <div className="server-pill">
          <span className={`dot ${serverOk ? "ok" : "warn"}`} />
          <span>{serverState}</span>
        </div>
      </header>
      {isAdmin ? <Admin /> : <Studio serverState={serverState} refreshServer={refreshServer} />}
    </ModalProvider>
  );
}
