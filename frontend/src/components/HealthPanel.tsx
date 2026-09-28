"use client";

import type { HealthState } from "@/hooks/useHealth";
import { API_BASE_URL } from "@/lib/api";

type Tone = "ok" | "warn" | "bad";

const TONE_BY_STATUS: Record<string, Tone> = {
  ok: "ok",
  ready: "ok",
  not_required: "warn",
  degraded: "warn",
  missing: "warn",
  down: "bad",
};

function StatusBadge({ status }: { status: string }) {
  const tone = TONE_BY_STATUS[status] ?? "warn";
  return <span className={`badge badge--${tone}`}>{status}</span>;
}

function Row({
  label,
  status,
  detail,
}: {
  label: string;
  status: string;
  detail?: string | null;
}) {
  return (
    <div className="row">
      <div className="row__label">{label}</div>
      <StatusBadge status={status} />
      <div className="row__detail">{detail ?? "—"}</div>
    </div>
  );
}

export function HealthPanel({ state }: { state: HealthState }) {
  const { data, error, loading, refresh } = state;

  return (
    <section className="panel">
      <header className="panel__header">
        <h2>Состояние системы</h2>
        <button type="button" onClick={refresh} disabled={loading}>
          {loading ? "Обновление…" : "Обновить"}
        </button>
      </header>

      <p className="muted">
        API: <code>{API_BASE_URL}</code>
      </p>

      {error && (
        <p className="error" role="alert">
          API недоступен: {error}
        </p>
      )}

      {!error && !data && loading && <p className="muted">Загрузка…</p>}

      {data && (
        <div className="rows">
          <Row label="Общий статус" status={data.status} detail={`версия ${data.version}`} />
          <Row label="Redis" status={data.redis.status} detail={data.redis.detail} />
          <Row
            label="Worker"
            status={data.workers.status}
            detail={data.workers.detail ?? `${data.workers.count} на очереди «${data.workers.queue}»`}
          />
          <Row
            label="Модели"
            status={data.models.status}
            detail={data.models.detail ?? `${data.models.asr_model} / ${data.models.device}`}
          />
        </div>
      )}
    </section>
  );
}
