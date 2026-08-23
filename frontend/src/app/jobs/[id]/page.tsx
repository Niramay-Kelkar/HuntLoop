import { JobDetailClient } from "./JobDetailClient";

export default async function JobDetailPage({ params }: PageProps<"/jobs/[id]">) {
  const { id } = await params;
  return <JobDetailClient jobId={Number(id)} />;
}
