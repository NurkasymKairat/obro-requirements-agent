import { useRef, useState } from "react";
import { startRun } from "../api";
import { HomeFrame } from "./HomeFrame";

const EXT = [".pdf", ".md", ".markdown", ".txt", ".docx"];
const MAX = 10 * 1024 * 1024;

interface Props {
  title: string;
  onStarted: (id: string) => void;
  onDemo: () => void;
}

export function UploadScreen({ title, onStarted, onDemo }: Props) {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const send = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    const name = file.name.toLowerCase();
    if (!EXT.some((e) => name.endsWith(e))) return setError("Нужен файл PDF, Markdown, TXT или DOCX.");
    if (file.size > MAX) return setError("Файл больше 10 МБ.");
    if (!file.size) return setError("Файл пустой.");
    setBusy(true);
    try {
      onStarted(await startRun(file));
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  };

  return (
    <HomeFrame title={title}>
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
