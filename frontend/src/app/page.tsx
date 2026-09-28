import { TranscriberApp } from "@/components/TranscriberApp";

export default function HomePage() {
  return (
    <main className="page">
      <h1>Local Transcriber</h1>
      <p className="muted">
        Локальная расшифровка записей: спикеры, таймкоды, экспорт в JSON, TXT, DOCX и PDF. Аудио
        не покидает этот компьютер.
      </p>
      <TranscriberApp />
    </main>
  );
}
