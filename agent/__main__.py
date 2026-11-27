"""CLI: python -m agent <документ> <отчёт.md>"""
from __future__ import annotations

import argparse
import os
import sys
import time

from .config import ConfigError, load_settings
from .llm import AllModelsUnavailable, BalanceExhausted, FreeTierUnavailable, make_client
from .pipeline import Pipeline
from .report import write_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agent",
                                 description="Статическое ревью документа требований (ISO/IEC/IEEE 29148, IEEE 830).")
    ap.add_argument("document", help="путь к документу: .pdf, .md, .txt, .docx")
    ap.add_argument("report", nargs="?", default="reports/report.md",
                    help="путь к отчёту Markdown (по умолчанию reports/report.md); рядом пишется .json")
    args = ap.parse_args(argv)
    started = time.monotonic()   # время прогона — от старта до записи отчёта

    try:
        settings = load_settings()
        llm = make_client(settings)
        pipeline = Pipeline(llm)
        result = pipeline.run(args.document)
        md, js = write_report(result, args.report, started)
        pipeline.log_totals(result)
    except (ConfigError, FileNotFoundError, ValueError) as e:
        print(f"Ошибка: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Прервано. Повторный запуск продолжит с последнего успешного вызова (кеш).", file=sys.stderr)
        return 130
    except Exception as e:  # noqa: BLE001 — пользователь видит сообщение, а не стектрейс
        if os.getenv("AGENT_DEBUG"):
            raise
        print(f"Внутренняя ошибка агента: {type(e).__name__}: {e}. "
              "Подробности: запустите с AGENT_DEBUG=1.", file=sys.stderr)
        return 1

    print(f"Отчёт: {md}\nДанные: {js}\nТребований: {len(result.requirements)}, "
          f"замечаний: {len(result.findings)}, вердикт: {result.verdict}", file=sys.stderr)
    return exit_code(result, pipeline.stop)


def exit_code(result, stop) -> int:
    """0 — полный прогон; 6 — проекту недоступен бесплатный тариф; 5 — закончился баланс;
    3 — все модели недоступны; 4 — прочий сбой прохода."""
    if result.incomplete_reason is None:
        return 0
    if isinstance(stop, FreeTierUnavailable):
        print(f"Остановлено: {stop}", file=sys.stderr)
        return 6
    if isinstance(stop, BalanceExhausted):
        print(f"Остановлено: {stop}", file=sys.stderr)
        return 5
    if isinstance(stop, AllModelsUnavailable):
        print(f"Остановлено: {stop}", file=sys.stderr)
        return 3
    print("Отчёт неполный: часть проходов не выполнена, вердикт не вынесен.", file=sys.stderr)
    return 4


if __name__ == "__main__":
    sys.exit(main())
