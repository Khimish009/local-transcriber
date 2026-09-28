import { HealthPanel } from "@/components/HealthPanel";

export default function HomePage() {
  return (
    <main className="page">
      <h1>Local Transcriber</h1>
      <p className="muted">
        Phase 0 — bootstrap. Загрузка файлов и расшифровка появятся на следующих этапах.
      </p>
      <HealthPanel />
    </main>
  );
}
