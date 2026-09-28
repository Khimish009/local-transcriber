import { TranscriberApp } from "@/components/TranscriberApp";

export default function HomePage() {
  return (
    <main className="page">
      <h1>Local Transcriber</h1>
      <p className="muted">
        Phase 1 — загрузка файла и фоновая задача. Распознавание и экспорт появятся на следующих
        этапах.
      </p>
      <TranscriberApp />
    </main>
  );
}
