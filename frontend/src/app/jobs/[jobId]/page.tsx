import Link from "next/link";

import { JobView } from "@/components/JobView";

interface Props {
  params: Promise<{ jobId: string }>;
}

export default async function JobPage({ params }: Props) {
  const { jobId } = await params;

  return (
    <main className="page">
      <Link href="/" className="back-link">
        ← Все задачи
      </Link>
      <JobView jobId={jobId} />
    </main>
  );
}
