"use client";

import { useEffect, useState } from "react";
import StatusBadge from "../components/StatusBadge";
import { api, settled, type Case, type Patient } from "../lib/api";

export default function QueuePage() {
  const [patients, setPatients] = useState<Patient[]>([]);
  const [patientId, setPatientId] = useState("");
  const [cases, setCases] = useState<Case[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    api<Patient[]>("/patients")
      .then((list) => {
        setPatients(list);
        setPatientId((id) => id || list[0]?.id || "");
      })
      .catch((e) => setError(`Cannot reach the API: ${e.message}`));
  }, []);

  // Keep the queue current, including cases started elsewhere (another tab, or the API itself):
  // quickly while anything is queued or running, slowly otherwise.
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stop = false;
    const tick = async () => {
      let delay = 5000;
      try {
        const list = await api<Case[]>("/cases");
        if (stop) return;
        setCases(list);
        if (list.some((c) => !settled(c))) delay = 1500;
      } catch {
        // the API is down or restarting; try again at the slow pace
      }
      if (!stop) timer = setTimeout(tick, delay);
    };
    tick();
    return () => {
      stop = true;
      clearTimeout(timer);
    };
  }, []);

  async function start() {
    setBusy(true);
    setError(null);
    try {
      const created = await api<Case>("/cases", { method: "POST", body: JSON.stringify({ patient_id: patientId }) });
      window.location.href = `/cases/${created.id}`;
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Case queue</h1>
      <p className="muted">
        Pick a patient to draft a prior-authorization packet. The agent reads the chart through MCP tools, behind a
        de-identifying gateway; a model drafts each criterion; every citation is then checked against the chart.
      </p>

      <div className="panel row">
        <select value={patientId} onChange={(e) => setPatientId(e.target.value)} aria-label="Patient">
          {patients.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
              {p.birth_date ? ` · born ${p.birth_date}` : ""}
            </option>
          ))}
        </select>
        <button onClick={start} disabled={!patientId || busy}>
          {busy ? "Starting…" : "Start case"}
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {!error && patients.length === 0 && (
        <p className="muted small">
          No patients found. Generate the synthetic cohort first: <code>data/generate.sh</code> (see the README).
        </p>
      )}

      <h2>Cases</h2>
      {cases.length === 0 ? (
        <p className="muted">No cases yet. Cases are kept in memory, so restarting the API clears them.</p>
      ) : (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Patient</th>
                <th>Status</th>
                <th className="hide-sm">Service</th>
                <th className="hide-sm">Created</th>
              </tr>
            </thead>
            <tbody>
              {cases.map((c) => (
                <tr key={c.id}>
                  <td>
                    <a href={`/cases/${c.id}`}>{c.patient_name}</a>
                  </td>
                  <td>
                    <StatusBadge status={c.status} />
                  </td>
                  <td className="hide-sm muted">{c.service}</td>
                  <td className="hide-sm muted small">{new Date(c.created_at).toLocaleTimeString()}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
