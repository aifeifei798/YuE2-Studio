import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";
import { CheckCircle2, XCircle } from "lucide-react";

interface ToastItem { id: number; kind: "ok" | "err"; text: string }

const Ctx = createContext<{ toast: (text: string, kind?: "ok" | "err") => void }>({ toast: () => {} });

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const idRef = useRef(1);

  const toast = useCallback((text: string, kind: "ok" | "err" = "ok") => {
    const id = idRef.current++;
    setItems((prev) => [...prev.slice(-2), { id, kind, text }]);
    setTimeout(() => setItems((prev) => prev.filter((t) => t.id !== id)), 3400);
  }, []);

  return (
    <Ctx.Provider value={{ toast }}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.kind === "err" ? "err" : "ok"}`}>
            {t.kind === "err" ? <XCircle /> : <CheckCircle2 />}
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{t.text}</span>
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}
