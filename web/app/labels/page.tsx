"use client";

import { useEffect, useState } from "react";

type LabelRecord = { ref: string; date: string | null; summary: string; text: string | null };
type Label = {
  criterion: string;
  status: "evidence" | "gap" | "unlabeled";
  reason: string;
  expected: string | null;
  records: LabelRecord[];
};
type LabelCase = { key: string; split: string; labels: Label[] };
type LabelsFile = {
  labeler: string;
  date: string;
  scripted?: boolean;
  criteria: { id: string; description: string }[];
  cases: LabelCase[];
};
type Verdict = "agree" | "disagree" | "unsure";
type Decision = { verdict?: Verdict; note?: string };
type Decisions = Record<string, Decision>; // key: `${case}|${criterion}`

const STORE = "labels-review-v1";
const dkey = (c: string, k: string) => `${c}|${k}`;

function loadDecisions(): Decisions {
  try {
    const raw = window.localStorage.getItem(STORE);
    return raw ? (JSON.parse(raw) as Decisions) : {};
  } catch {
    return {};
  }
}

function saveDecisions(d: Decisions) {
  try {
    window.localStorage.setItem(STORE, JSON.stringify(d));
  } catch {
    // storage is blocked or full; decisions stay on screen and can still be downloaded
  }
}

async function fetchJson(path: string): Promise<LabelsFile | null> {
  try {
    const res = await fetch(path, { cache: "no-store" });
    if (!res.ok) return null;
    return (await res.json()) as LabelsFile;
  } catch {
    return null;
  }
}

const badgeKind = { evidence: "ok", gap: "gap", unlabeled: "plain" } as const;
const VERDICTS: Verdict[] = ["agree", "disagree", "unsure"];

export default function LabelsPage() {
  const [data, setData] = useState<LabelsFile | null>(null);
  const [source, setSource] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [decisions, setDecisions] = useState<Decisions>({});
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    setDecisions(loadDecisions());
    (async () => {
      let file = await fetchJson("/labels.json");
      let used = "/labels.json";
      if (!file) {
        file = await fetchJson("/labels.sample.json");
        used = "/labels.sample.json";
      }
      if (!file) {
        setError("Neither /labels.json nor /labels.sample.json could be loaded.");
        return;
      }
      setData(file);
      setSource(used);
      setOpen(file.cases[0]?.key ?? null);
    })();
  }, []);

  function update(c: string, k: string, patch: Decision) {
    setDecisions((prev) => {
      const next = { ...prev, [dkey(c, k)]: { ...prev[dkey(c, k)], ...patch } };
      saveDecisions(next);
      return next;
    });
  }

  const reviewedIn = (c: LabelCase) => c.labels.filter((l) => decisions[dkey(c.key, l.criterion)]?.verdict).length;

  function download() {
    if (!data) return;
    const rows = data.cases.flatMap((c) =>
      c.labels.flatMap((l) => {
        const d = decisions[dkey(c.key, l.criterion)];
        return d?.verdict || d?.note
          ? [{ case: c.key, criterion: l.criterion, verdict: d.verdict ?? null, note: d.note ?? "" }]
          : [];
      }),
    );
    const blob = new Blob([JSON.stringify(rows, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "labels-review.json";
    a.click();
    URL.revokeObjectURL(url);
  }

  if (error) return <p className="error">{error}</p>;
  if (!data) return <p className="muted">Loading labels…</p>;

  const totalLabels = data.cases.reduce((n, c) => n + c.labels.length, 0);
  const totalReviewed = data.cases.reduce((n, c) => n + reviewedIn(c), 0);
  const desc = (id: string) => data.criteria.find((c) => c.id === id)?.description;

  return (
    <>
      <h1>Reference labels</h1>
      {data.scripted && (
        <p className="banner" role="note">
          <b>Sample.</b> These labels are scripted, not written by a model. They only show how the page works.
        </p>
      )}
      <p className="muted">
        Each label was written by the model <code>{data.labeler}</code> on {data.date}. No human has reviewed
        them: every label shows &ldquo;not reviewed&rdquo; until you choose agree, disagree or unsure. Your
        decisions stay in this browser and download as JSON.
      </p>
      <p className="muted small">Loaded from {source}.</p>

      <div className="row labels-actions" style={{ margin: "12px 0" }}>
        <button onClick={download}>Download review</button>
        <span className="muted small">
          {totalReviewed} of {totalLabels} labels reviewed
        </span>
      </div>

      <h2>Cases</h2>
      <div className="panel" style={{ padding: 0, overflowX: "auto" }}>
        <table>
          <thead>
            <tr>
              <th>Case</th>
              <th>Split</th>
              <th>Evidence</th>
              <th>Gap</th>
              <th>Unlabeled</th>
              <th>Reviewed</th>
            </tr>
          </thead>
          <tbody>
            {data.cases.map((c) => (
              <tr key={c.key}>
                <td>
                  <a href={`#case-${c.key}`} onClick={() => setOpen(c.key)}>
                    <code>{c.key}</code>
                  </a>
                </td>
                <td>{c.split}</td>
                <td>{c.labels.filter((l) => l.status === "evidence").length}</td>
                <td>{c.labels.filter((l) => l.status === "gap").length}</td>
                <td>{c.labels.filter((l) => l.status === "unlabeled").length}</td>
                <td>
                  {reviewedIn(c)} of {c.labels.length}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {data.cases.map((c) => (
        <section key={c.key} id={`case-${c.key}`} className="panel" style={{ marginTop: 16 }}>
          <div className="row">
            <h2 style={{ margin: 0 }}>
              <code>{c.key}</code>
            </h2>
            <span className="badge plain">{c.split}</span>
            <span className="muted small">
              {reviewedIn(c)} of {c.labels.length} reviewed
            </span>
            <button className="ghost" aria-expanded={open === c.key} onClick={() => setOpen(open === c.key ? null : c.key)}>
              {open === c.key ? "Hide" : "Show"}
            </button>
          </div>
          {open === c.key &&
            c.labels.map((l) => {
              const d = decisions[dkey(c.key, l.criterion)] ?? {};
              const mismatch = l.expected !== null && l.expected !== l.status;
              return (
                <div key={l.criterion} className="criterion">
                  <div className="head">
                    <code>{l.criterion}</code>
                    <span className={`badge ${badgeKind[l.status]}`}>{l.status}</span>
                    {l.expected !== null && (
                      <span className={`badge ${mismatch ? "flag" : "plain"}`}>
                        expected: {l.expected}
                        {mismatch ? " · mismatch" : ""}
                      </span>
                    )}
                    <span className="badge plain">{d.verdict ? `you: ${d.verdict}` : "not reviewed"}</span>
                  </div>
                  {desc(l.criterion) && <div className="muted small">{desc(l.criterion)}</div>}
                  <p style={{ margin: "6px 0" }}>{l.reason}</p>
                  {l.records.length === 0 && <div className="muted small">No records cited.</div>}
                  {l.records.map((r) => (
                    <div key={r.ref} className="cite">
                      <div className="row">
                        <code>{r.ref}</code>
                        <span className="muted">{r.date ?? "no date"}</span>
                      </div>
                      <div style={{ marginTop: 4 }}>{r.summary}</div>
                      {r.text && <pre>{r.text}</pre>}
                    </div>
                  ))}
                  <div className="row" style={{ marginTop: 10 }} role="group" aria-label={`Your verdict on ${l.criterion}`}>
                    {VERDICTS.map((v) => (
                      <button
                        key={v}
                        className={d.verdict === v ? "" : "ghost"}
                        aria-pressed={d.verdict === v}
                        onClick={() => update(c.key, l.criterion, { verdict: d.verdict === v ? undefined : v })}
                      >
                        {v}
                      </button>
                    ))}
                  </div>
                  <textarea
                    aria-label={`Note on ${l.criterion}`}
                    placeholder="Optional note"
                    value={d.note ?? ""}
                    onChange={(e) => update(c.key, l.criterion, { note: e.target.value })}
                    rows={2}
                  />
                </div>
              );
            })}
        </section>
      ))}
    </>
  );
}
