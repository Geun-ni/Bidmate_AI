import { useAppState } from "../../state/AppState.jsx";
import { EmptyState, NoticeBox, Source, Term } from "../ui.jsx";
import "./DocsTab.css";

const STAGES = [
  { id: "APPLY", title: "입찰참가신청 서류", term: "입찰참가신청" },
  { id: "QUALIFY", title: "적격심사 서류", term: "적격심사" },
  { id: "CONDITIONAL", title: "해당하는 경우에만 내는 서류", term: null },
];

/** FUNC-025: 서류별 상태 4가지 */
const STATUSES = [
  { id: "ready", label: "준비", tone: "ok" },
  { id: "todo", label: "미준비", tone: "muted" },
  { id: "na", label: "해당 없음", tone: "muted" },
  { id: "check", label: "확인 필요", tone: "warn" },
];

function StatusPicker({ name, value, onChange }) {
  return (
    <div className="doc-status-pick" role="radiogroup" aria-label={`${name} 준비 상태`}>
      {STATUSES.map((s) => (
        <label key={s.id} className={`dsp dsp-${s.tone}${value === s.id ? " on" : ""}`}>
          <input type="radio" name={`st-${name}`} checked={value === s.id} onChange={() => onChange(s.id)} />
          {s.label}
        </label>
      ))}
    </div>
  );
}

export default function DocsTab({ notice }) {
  const { docStatuses, setDocStatus } = useAppState();
  const statuses = docStatuses(notice.id);
  const statusOf = (d) => statuses[d.name] ?? "todo";
  const docs = notice.requiredDocs;
  const criteriaMissing = notice.documents.criteria.status !== "OK";

  // 진행률은 필수 서류 기준. '해당 없음'으로 고른 필수 서류는 분모에서 뺀다.
  const required = docs.filter((d) => d.requirement === "REQUIRED" && statusOf(d) !== "na");
  const readyCount = required.filter((d) => statusOf(d) === "ready").length;
  const checkCount = docs.filter((d) => statusOf(d) === "check").length;
  const pct = required.length ? Math.round((readyCount / required.length) * 100) : 0;

  if (docs.length === 0) {
    return (
      <section className="card">
        {notice.status === "CANCELLED" ? (
          <EmptyState title="취소된 공고입니다">취소된 공고는 서류를 준비할 필요가 없습니다. 재공고가 올라오면 새 공고에서 확인하세요.</EmptyState>
        ) : (
          <EmptyState title="필요서류를 찾지 못했습니다">공고문과 적격심사기준 원문에서 제출서류를 확인해 주세요.</EmptyState>
        )}
      </section>
    );
  }

  return (
    <>
      <section className="card card-pad docs-progress" aria-labelledby="docs-title" data-reveal>
        <div className="card-head" style={{ marginBottom: 6 }}>
          <h2 id="docs-title" className="section-title">제출서류 체크리스트</h2>
          <button type="button" className="btn btn-sm no-print" onClick={() => window.print()}>🖨 인쇄</button>
        </div>
        <p className="small" aria-live="polite">
          필수 서류 <strong className="num">{readyCount}</strong> / <span className="num">{required.length}</span>개 준비 <span className="muted">({pct}%)</span>
          {checkCount > 0 && <span className="badge badge-warn" style={{ marginLeft: 8 }}>확인 필요 {checkCount}건</span>}
        </p>
        <div className="progress" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="필수 서류 준비율">
          <span style={{ width: `${pct}%` }} />
        </div>
        <p className="xsmall muted" style={{ marginTop: 8 }}>
          진행률은 필수 서류만 셉니다(조건부 서류와 &lsquo;해당 없음&rsquo;은 제외). 상태는 이 브라우저에만 저장되는 개인 메모이며, SRM 제출 완료나 기관 승인을 뜻하지 않습니다.
        </p>
      </section>

      {criteriaMissing && (
        <NoticeBox tone="warn" icon="!">
          적격심사기준을 확보하지 못해 <strong>적격심사 서류가 빠져 있을 수 있습니다.</strong> 원문에서 꼭 확인하세요.
        </NoticeBox>
      )}

      {STAGES.map((stage) => {
        const list = docs.filter((d) => d.stage === stage.id);
        if (!list.length) return null;
        return (
          <section key={stage.id} data-reveal className="card card-pad" aria-labelledby={`stage-${stage.id}`}>
            <h3 id={`stage-${stage.id}`} className="section-title">
              {stage.term ? <Term term={stage.term}>{stage.title}</Term> : stage.title}
              <span className="muted small num">{list.filter((d) => statusOf(d) === "ready").length}/{list.length} 준비</span>
            </h3>
            <ul className="doc-list">
              {list.map((d) => {
                const st = statusOf(d);
                return (
                  <li key={d.name} className={`doc-${st}`}>
                    <div className="doc-row">
                      <div className="doc-title">
                        <span className="doc-name">{d.name}</span>
                        <span className={`badge ${d.requirement === "REQUIRED" ? "badge-brand" : "badge-outline"}`}>
                          {d.requirement === "REQUIRED" ? "필수" : "조건부"}
                        </span>
                      </div>
                      <StatusPicker name={d.name} value={st} onChange={(v) => setDocStatus(notice.id, d.name, v)} />
                    </div>
                    <dl className="doc-info">
                      {d.condition && <div><dt>조건</dt><dd>{d.condition}</dd></div>}
                      <div><dt>제출 목적</dt><dd>{d.purpose ?? <span className="source-missing">원문 확인 필요</span>}</dd></div>
                      <div><dt>제출 시점</dt><dd>{d.timing ?? <span className="source-missing">원문 확인 필요</span>}</dd></div>
                      <div><dt>제출 방법</dt><dd>{d.method ?? <span className="source-missing">원문 확인 필요</span>}</dd></div>
                    </dl>
                    <Source text={d.source} />
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </>
  );
}
