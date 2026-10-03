import type { Case } from "../lib/api";

export default function StatusBadge({ status }: { status: Case["status"] }) {
  const kind = status === "done" ? "ok" : status === "failed" ? "flag" : "plain";
  return <span className={`badge ${kind}`}>{status}</span>;
}
