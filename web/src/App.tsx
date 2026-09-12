import { useCallback, useEffect, useState } from "react";
import { LayoutDashboard, Wand2 } from "lucide-react";
import Studio from "./pages/Studio";
import Admin from "./pages/Admin";
import { ModalProvider } from "./components/Modal";
import { ToastProvider } from "./components/Toast";
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
      <ToastProvider>
        <header className="topbar">
          <div className="brand">
            <span className="brand-mark">🎵</span>
            <span>YuE2 Studio</span>
            <span className="badge">AI 作曲工坊</span>
          </div>
          <nav className="nav">
            <a href="#/" className={!isAdmin ? "active" : ""}>
              <Wand2 size={13} /> 创作
            </a>
            <a href="#/admin" className={isAdmin ? "active" : ""}>
              <LayoutDashboard size={13} /> 管理
            </a>
          </nav>
          <div className="server-pill" title="GPU 服务状态">
            <span className={`dot ${serverOk ? "ok" : "warn"}`} />
            <span>{serverState}</span>
          </div>
        </header>
        {isAdmin ? <Admin /> : <Studio serverState={serverState} refreshServer={refreshServer} />}
      </ToastProvider>
    </ModalProvider>
  );
}
