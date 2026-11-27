import { useRef, useState } from "react";
import { AuthError, startRun, type Credentials } from "../api";
import { HomeFrame } from "./HomeFrame";

const EXT = [".pdf", ".md", ".markdown", ".txt", ".docx"];
const MAX = 10 * 1024 * 1024;

interface Props {
  title: string;
  authRequired: boolean;
  onStarted: (id: string) => void;
  onDemo: () => void;
}

const CREDS_KEY = "tzcontrol.jury";

function savedCreds(): Credentials {
  try {
    const c = JSON.parse(sessionStorage.getItem(CREDS_KEY) ?? "");
    if (typeof c?.user === "string" && typeof c?.password === "string") return c;
  } catch {
    /* нет сохранённых */
  }
  return { user: "jury", password: "" };
}

export function UploadScreen({ title, authRequired, onStarted, onDemo }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creds, setCreds] = useState<Credentials>(savedCreds);
  const [askAuth, setAskAuth] = useState(false); // сервер ответил 401, хотя заранее пароль не требовал
  const showAuth = authRequired || askAuth;

  const send = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    const name = file.name.toLowerCase();
    if (!EXT.some((e) => name.endsWith(e))) return setError("Нужен файл PDF, Markdown, TXT или DOCX.");
    if (file.size > MAX) return setError("Файл больше 10 МБ.");
    if (!file.size) return setError("Файл пустой.");
    setBusy(true);
    try {
      const id = await startRun(file, showAuth ? creds : null);
      if (showAuth) sessionStorage.setItem(CREDS_KEY, JSON.stringify(creds));
      onStarted(id);
    } catch (e) {
      if (e instanceof AuthError) setAskAuth(true);
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <HomeFrame title={title}>
      {showAuth && (
        <form className="auth" onSubmit={(e) => e.preventDefault()} autoComplete="on">
          <span className="label">Доступ жюри</span>
          <input
            aria-label="Логин"
            placeholder="Логин"
            autoComplete="username"
            value={creds.user}
            onChange={(e) => setCreds({ ...creds, user: e.target.value })}
          />
          <input
            aria-label="Пароль"
            placeholder="Пароль"
            type="password"
            autoComplete="current-password"
            value={creds.password}
            onChange={(e) => setCreds({ ...creds, password: e.target.value })}
          />
          <span className="auth-note">нужен только для загрузки; пример открыт всем</span>
        </form>
      )}
      <div
        className={`dropzone${over ? " over" : ""}${busy ? " busy" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          if (!busy) send(e.dataTransfer.files[0]);
        }}
        onClick={() => !busy && input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
      >
        <div className="dz-icon" aria-hidden>
          ⤒
        </div>
        <div className="dz-title">{busy ? "Загружаю…" : "Перетащите документ сюда"}</div>
        <div className="dz-sub">PDF, Markdown, TXT, DOCX · до 10 МБ</div>
        <div className="dz-actions">
          <button
            className="btn primary"
            disabled={busy}
            onClick={(e) => {
              e.stopPropagation();
              input.current?.click();
            }}
          >
            Выбрать документ
          </button>
          <button
            className="btn"
            onClick={(e) => {
              e.stopPropagation();
              onDemo();
            }}
          >
            Открыть пример
          </button>
        </div>
        <input
          ref={input}
          type="file"
          accept={EXT.join(",")}
          hidden
          onChange={(e) => {
            send(e.target.files?.[0]);
            e.target.value = "";
          }}
        />
      </div>
      {error && <p className="error">{error}</p>}
    </HomeFrame>
  );
}
