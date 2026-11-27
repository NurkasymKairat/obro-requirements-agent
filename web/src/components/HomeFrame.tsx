import type { ReactNode } from "react";

// Общая рамка первого экрана и экрана прогресса (Figma: «01 · Загрузка», «01 · Проверка документа»).

const FACTS: [string, string][] = [
  ["✓", "Цитаты сверяет код: выдуманных нет"],
  ["◷", "9 вызовов модели, около 5 минут"],
  ["▤", "PDF, Markdown, TXT, DOCX"],
];

export function HomeFrame({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="home">
      <div className="home-inner">
        <div className="home-brand">
          <span className="logo logo-l">✓</span>
          <h1>{title}</h1>
        </div>
        <div className="label home-tagline">ИИ-агент для банковского аналитика</div>
        <p className="lead">
          Находит противоречия, пропуски и непроверяемые формулировки в ТЗ до начала разработки. Каждое замечание — с
          цитатой из документа.
        </p>
        {children}
        <ul className="facts">
          {FACTS.map(([icon, text]) => (
            <li key={text}>
              <span className="fact-icon" aria-hidden>
                {icon}
              </span>
              {text}
            </li>
          ))}
        </ul>
        <p className="standards">
          Критерии: ISO/IEC/IEEE 29148:2018 п. 5.2.5, 5.2.6 · IEEE 830-1998 п. 4.3
        </p>
      </div>
    </div>
  );
}
