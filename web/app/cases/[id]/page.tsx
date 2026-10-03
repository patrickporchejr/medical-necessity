"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import PacketView from "../../../components/PacketView";
import StatusBadge from "../../../components/StatusBadge";
import { api, settled, type Case, type PipelineEvent } from "../../../lib/api";

export default function CasePage() {
  const { id } = useParams<{ id: string }>();
  const [kase, setCase] = useState<Case | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let stop = false;
    const tick = async () => {
      try {
        const c = await api<Case>(`/cases/${id}`);
        if (stop) return;
        setCase(c);
        if (!settled(c)) setTimeout(tick, 700);
      } catch (e) {
        if (!stop) setError((e as Error).message);
      }
    };
    tick();
    return () => {
      stop = true;
    };
  }, [id]);

  if (error) return <p className="error">{error}</p>;
  if (!kase) return <p className="muted">Loading…</p>;
  const r = kase.result;

  return (
    <>
      <p className="small">
        <a href="/">← Case queue</a>
      </p>
      <div className="row">
        <h1>{kase.patient_name}</h1>
        <StatusBadge status={kase.status} />
      </div>
      <p className="muted" style={{ marginTop: 0 }}>
        {kase.service}
        {r ? ` · as of ${r.as_of}` : ""}
      </p>

      <Timeline events={kase.events} />
      {kase.error && <p className="error">{kase.error}</p>}

      {r && (
        <>
          <h2>Summary</h2>
          <Summary kase={kase} />
          <h2>Packet</h2>
          <PacketView result={r} />
        </>
      )}
    </>
  );
}

// The nodes as they ran, from the run's metadata-only events.
function Timeline({ events }: { events: PipelineEvent[] }) {
  const steps: { node: string; state: string; seconds?: number; note?: string }[] = [];
  for (const e of events) {
    if (e.event === "node_started") steps.push({ node: e.node, state: "running" });
    const last = [...steps].reverse().find((s) => s.node === e.node);
    if (!last) continue;
    if (e.event === "node_finished") {
      last.state = "done";
      last.seconds = e.data.seconds as number;
      if (e.node === "assemble" && Number(e.data.repairs) > 0) last.note = "repair";
      if (e.node === "verify") last.note = `${e.data.flagged} flagged`;
    }
    if (e.event === "node_failed") last.state = "failed";
  }
  if (steps.length === 0) return <p className="muted">Waiting to start…</p>;
  return (
    <div className="timeline">
      {steps.map((s, i) => (
        <div key={i} className={`step ${s.state === "failed" ? "failed" : ""}`}>
          {s.node}
          {s.note ? ` (${s.note})` : ""}
          <span className="muted">
            {" "}
            · {s.state === "done" ? `${s.seconds}s` : s.state}
          </span>
        </div>
      ))}
    </div>
  );
}

function Summary({ kase }: { kase: Case }) {
  const r = kase.result!;
  const flagged = r.assertions.filter((a) => !a.supported).length + r.unaddressed.length;
  const tokens = r.usage.input_tokens == null ? "–" : `${r.usage.input_tokens} in / ${r.usage.output_tokens} out`;
  return (
    <>
      <div className="stats">
        <div>
          <span className="small muted">Verdict</span>
          <b className={flagged ? "error" : ""}>{flagged ? `${flagged} for review` : "all verified"}</b>
        </div>
        <div>
          <span className="small muted">Drafted by</span>
          <b>{r.route === "gap_packet" ? "code (no model call)" : r.assembled_by}</b>
        </div>
        <div>
          <span className="small muted">Repairs</span>
          <b>{r.repairs}</b>
        </div>
        <div>
          <span className="small muted">Tokens</span>
          <b>{tokens}</b>
        </div>
      </div>
      {r.route === "gap_packet" && (
        <p className="small muted">
          The RA diagnosis is not established in this chart, so no packet can be approved. Code wrote it from the
          evidence and no model was called.
        </p>
      )}
      {r.first_pass && !r.repair_failed && (
        <p className="small muted">
          Verify flagged the model&apos;s first draft ({[...r.first_pass.flagged, ...r.first_pass.unaddressed].join(", ")})
          and the flagged criteria went back to the model once with verify&apos;s reasons.
        </p>
      )}
      {r.repair_failed && (
        <p className="small error">
          The repair call failed ({r.repair_failed}); the first draft is shown with its flags.
        </p>
      )}
    </>
  );
}
