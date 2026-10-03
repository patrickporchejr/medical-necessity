import type { ChartRecord, Check } from "../lib/api";

// One citation: did it resolve to a record in this patient's chart, does that record bear on the
// criterion, and what the record actually says.
export default function CitationTrace({ check, record }: { check: Check; record?: ChartRecord }) {
  const ok = check.exists && check.supports;
  return (
    <div className="cite">
      <div className="row">
        <span className={`badge ${ok ? "ok" : "flag"}`}>
          {!check.exists ? "not in chart" : check.supports ? "resolves" : "does not support"}
        </span>
        <code>
          {check.ref.resource_type} {check.ref.id}
        </code>
      </div>
      {record && (
        <div style={{ marginTop: 6 }}>
          <b>{record.label ?? record.resource_type}</b>
          <span className="muted">
            {record.date ? ` · ${record.date.slice(0, 10)}` : ""}
            {record.status ? ` · ${record.status}` : ""}
            {record.value ? ` · ${record.value}` : ""}
          </span>
          {record.text && (
            <details>
              <summary className="small muted">Note text</summary>
              <pre>{record.text}</pre>
            </details>
          )}
        </div>
      )}
      {!ok && check.reason && <div className="error small" style={{ marginTop: 4 }}>{check.reason}</div>}
    </div>
  );
}
