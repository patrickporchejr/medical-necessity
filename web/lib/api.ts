// The API the dashboard reads. Cases live in the API's memory, so a restart empties the queue.
export const API = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Patient = { id: string; name: string; birth_date?: string | null; gender?: string | null };

export type PipelineEvent = {
  event: "node_started" | "node_finished" | "node_failed";
  node: string;
  data: Record<string, unknown>;
};

export type Ref = { resource_type: string; id: string };
export type Check = { ref: Ref; exists: boolean; supports: boolean; reason: string | null };

export type Assertion = {
  criterion_id: string;
  kind: "evidence" | "gap";
  text: string;
  supported: boolean;
  reasons: string[];
  citations: Check[];
};

export type ChartRecord = {
  resource_type: string;
  id: string;
  label: string | null;
  date: string | null;
  status: string | null;
  value: string | null;
  text: string | null;
};

export type Result = {
  route: "assemble" | "gap_packet";
  assembled_by: string | null;
  repairs: number;
  repair_failed: string | null;
  first_pass: { flagged: string[]; unaddressed: string[] } | null;
  usage: { input_tokens: number | null; output_tokens: number | null; schema_retries: number | null };
  seconds: Record<string, number>;
  as_of: string;
  criteria: { id: string; description: string }[];
  assertions: Assertion[];
  unaddressed: string[];
  records: Record<string, ChartRecord>;
};

export type Case = {
  id: string;
  patient_id: string;
  patient_name: string;
  service: string;
  status: "queued" | "running" | "done" | "failed";
  created_at: string;
  events: PipelineEvent[];
  error: string | null;
  result: Result | null;
};

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const settled = (c: Case) => c.status === "done" || c.status === "failed";
