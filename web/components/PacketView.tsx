import type { Result } from "../lib/api";
import CitationTrace from "./CitationTrace";

// The packet, criterion by criterion, each with verify's verdict. Flagged assertions are shown,
// never hidden: the reviewer decides what to do with them.
export default function PacketView({ result }: { result: Result }) {
  const byCriterion = new Map(result.criteria.map((c) => [c.id, c.description]));
  return (
    <div className="panel">
      {result.assertions.map((a, i) => (
        <div className="criterion" key={`${a.criterion_id}-${i}`}>
          <div className="head">
            <code>{a.criterion_id}</code>
            <span className={`badge ${a.kind === "evidence" ? "ok" : "gap"}`}>
              {a.kind === "evidence" ? "evidence" : "gap"}
            </span>
            <span className={`badge ${a.supported ? "ok" : "flag"}`}>{a.supported ? "verified" : "flagged"}</span>
          </div>
          <div className="small muted" style={{ marginTop: 4 }}>{byCriterion.get(a.criterion_id)}</div>
          <p style={{ margin: "8px 0 0" }}>{a.text}</p>
          {!a.supported && a.reasons.length > 0 && (
            <ul className="reasons">
              {a.reasons.map((r) => (
                <li key={r}>{r}</li>
              ))}
            </ul>
          )}
          {a.citations.map((c) => (
            <CitationTrace
              key={`${c.ref.resource_type}/${c.ref.id}`}
              check={c}
              record={result.records[`${c.ref.resource_type}/${c.ref.id}`]}
            />
          ))}
        </div>
      ))}
      {result.unaddressed.map((id) => (
        <div className="criterion" key={id}>
          <div className="head">
            <code>{id}</code>
            <span className="badge flag">not addressed</span>
          </div>
          <div className="small muted">{byCriterion.get(id)}</div>
        </div>
      ))}
    </div>
  );
}
