import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

/** Self-check used by the container healthcheck; does not depend on the backend. */
export function GET() {
  return NextResponse.json({ status: "ok" });
}
