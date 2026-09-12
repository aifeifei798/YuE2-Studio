import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

interface ModalRequest {
  kind: "alert" | "confirm";
  message: string;
  resolve: (v: boolean) => void;
}

interface ModalApi {
  alert: (msg: string) => Promise<void>;
  confirm: (msg: string) => Promise<boolean>;
}

const Ctx = createContext<ModalApi>({
  alert: async () => {},
  confirm: async () => false,
});

export function useModal(): ModalApi {
  return useContext(Ctx);
}

/** 全局非阻塞弹窗：替代原生 alert/confirm（后者会冻结整个页面线程）。 */
export function ModalProvider({ children }: { children: ReactNode }) {
  const [req, setReq] = useState<ModalRequest | null>(null);
  const reqRef = useRef<ModalRequest | null>(null);
  reqRef.current = req;

  const close = (v: boolean) => {
    const r = reqRef.current;
    setReq(null);
    r?.resolve(v);
  };

  const api: ModalApi = {
    alert: (message) =>
      new Promise<void>((resolve) => setReq({ kind: "alert", message, resolve: () => resolve() })),
    confirm: (message) =>
      new Promise<boolean>((resolve) => setReq({ kind: "confirm", message, resolve })),
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && reqRef.current) close(reqRef.current.kind === "alert");
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <Ctx.Provider value={api}>
      {children}
      {req && (
        <div className="overlay" onClick={() => close(req.kind === "alert")}>
          <div className="dialog panel" role="alertdialog" onClick={(e) => e.stopPropagation()}>
            <div style={{ fontSize: 26, marginBottom: 8 }}>{req.kind === "confirm" ? "🗑️" : "💡"}</div>
            <div style={{ fontSize: 13.5, lineHeight: 1.7, color: "#e2e8f0", whiteSpace: "pre-wrap" }}>{req.message}</div>
            <div style={{ display: "flex", gap: 8, marginTop: 14, justifyContent: "flex-end" }}>
              {req.kind === "confirm" && (
                <button className="mini" onClick={() => close(false)}>取消</button>
              )}
              <button className={`mini${req.kind === "confirm" ? " primary" : ""}`} onClick={() => close(true)}>
                {req.kind === "confirm" ? "确定删除" : "知道了"}
              </button>
            </div>
          </div>
        </div>
      )}
    </Ctx.Provider>
  );
}
