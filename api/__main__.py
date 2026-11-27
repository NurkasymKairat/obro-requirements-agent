"""python -m api [--host 127.0.0.1] [--port 8000] [--reload] — API и собранный фронт одной командой."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser(prog="python -m api", description="Веб-интерфейс агента проверки требований.")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--reload", action="store_true", help="перезапуск при правке api/ (режим разработки)")
    args = ap.parse_args()
    os.chdir(ROOT)                 # .env, кеш .cache/llm и относительные пути — как у CLI
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    import uvicorn
    print(f"Веб-интерфейс: http://{args.host}:{args.port}  (прогон: {os.getenv('WEB_PIPELINE') or 'real'})")
    uvicorn.run("api.main:app", host=args.host, port=args.port, reload=args.reload,
                reload_dirs=[str(ROOT / "api")] if args.reload else None, log_level="info")


if __name__ == "__main__":
    main()
