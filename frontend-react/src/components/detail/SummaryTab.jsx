import { Link } from "react-router-dom";
import { CATEGORY_LABEL, ruleDetail, useEvaluation } from "../../lib/useEvaluation.js";
import { useAppState } from "../../state/AppState.jsx";
import { Disclaimer, EmptyState, JudgmentBadge, NoticeBox, Source, Term } from "../ui.jsx";
import Attachments from "./Attachments.jsx";

export default function SummaryTab({ notice }) {
  const { company, testMode } = useAppState();
  const { judgment } = notice;
  const ev = useEvaluation(notice.id, testMode ? undefined : company?.id);

  return (
    <>
      <section className="card card-pad" aria-labelledby="sum-judge" data-reveal>
        <h2 id="sum-judge" className="section-title">
          SRM 투찰제한 <Term>1차 판정</Term>
          {judgment.overall && <JudgmentBadge status={judgment.overall} />}
        </h2>
        <p className="section-desc">
          {company ? "SRM에 등록된 조건과 우리 업체 정보를 코드로 비교했습니다." : "SRM에 등록된 조건입니다. 업체정보를 등록하면 우리 업체와 비교해 판정합니다."}
        </p>
        {!company && (
          <p className="small" style={{ marginBottom: 10 }}>
            <Link to="/company" className="btn btn-sm btn-primary">업체정보 등록하고 비교하기</Link>
          </p>
        )}
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">항목</th>
                <th scope="col">공고 조건</th>
                <th scope="col">우리 업체</th>
                <th scope="col">판정</th>
                <th scope="col">심사 방식</th>
              </tr>
            </thead>
            <tbody>
              {judgment.items.map((item) => (
                <tr key={item.key}>
                  <th scope="row" style={{ background: "transparent", color: "var(--text)", fontSize: ".88rem" }}>
                    {item.key === "qualification" ? <Term>제한경쟁입찰자격</Term> : item.key === "licenseMain" ? <Term>주력분야</Term> : item.label}
                  </th>
                  <td>{item.required}</td>
                  <td>{item.company}</td>
                  <td>
                    {item.status ? <JudgmentBadge status={item.status} compact /> : <span className="muted small">-</span>}
                    <p className="xsmall muted" style={{ marginTop: 4 }}>{item.reason}</p>
                    <Source text={item.source} label="근거" />
                  </td>
                  <td>
                    <span className={`badge ${item.mode === "AUTO" ? "badge-outline" : "badge-warn"}`}>
                      <Term term={item.mode === "AUTO" ? "자동심사" : "수동심사"}>{item.mode === "AUTO" ? "자동" : "수동"}</Term>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div style={{ marginTop: 12 }}>
          <Disclaimer>SRM 자동심사를 충족해도 최종 참가 자격을 보장하지 않습니다. 공고문의 추가 조건(실적·대기업 제한·공동수급 등)과 담당자 확인이 우선합니다.</Disclaimer>
        </div>
      </section>

      <section className="card card-pad" aria-labelledby="sum-extra" data-reveal>
        <h2 id="sum-extra" className="section-title">공고문 추가 참가 조건</h2>
        <p className="section-desc">SRM 코드로 확인할 수 없는 실적·대기업 제한·공동수급 같은 조건입니다.</p>
        <p className="small muted" style={{ marginTop: 8 }}>
          공고문 분석(AI) 결과가 아직 연결되지 않았습니다. 아래 원본 문서의 공고문에서 참가 자격 항목을 직접 확인하세요.
        </p>
      </section>

      <section className="card card-pad" aria-labelledby="sum-criteria" data-reveal>
        <h2 id="sum-criteria" className="section-title"><Term>적격심사</Term> 기준</h2>
        <Criteria ev={ev} />
      </section>

      <Attachments notice={notice} />
    </>
  );
}

function Criteria({ ev }) {
  if (ev.state === "loading") return <div className="skeleton" style={{ height: 120, marginTop: 12 }} />;
  if (ev.state === "error") return <EmptyState title="배점을 불러오지 못했습니다">{ev.error.message}</EmptyState>;
  const { evaluation } = ev;
  if (!evaluation.rules.length) {
    return (
      <div style={{ marginTop: 12 }}>
        <NoticeBox tone="danger" icon="!">
          <strong>배점을 확인하지 못했습니다 — {evaluation.reason}.</strong> 점수 계산 탭에서 적격심사기준 파일을 직접 올리거나, SRM 첨부파일을 확인하세요.
        </NoticeBox>
      </div>
    );
  }
  const { summary } = evaluation;
  const rows = evaluation.rules.filter((r) => r.category !== "PASS_SCORE");
  return (
    <>
      {summary.pass_score != null && (
        <p className="pass-score">
          <Term>낙찰자 결정 기준 점수</Term> <strong className="num">{summary.pass_score}점</strong> 이상
        </p>
      )}
      {evaluation.status === "확인 필요" && (
        <NoticeBox tone="warn" icon="!">{evaluation.reason}. 표시된 항목은 원문과 대조해 주세요.</NoticeBox>
      )}
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th scope="col">평가 항목</th><th scope="col">배점</th><th scope="col">내용</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>
                  <strong>{r.name}</strong>
                  {CATEGORY_LABEL[r.category] && r.name !== CATEGORY_LABEL[r.category] && <span className="xsmall muted"> · {CATEGORY_LABEL[r.category]}</span>}
                  {r.verification_status === "REVIEW" && <span className="badge badge-warn" style={{ marginLeft: 6 }}>원문 확인</span>}
                </td>
                <td className="num">{r.max_score == null ? "가감" : `${r.max_score}점`}</td>
                <td className="small">
                  {r.children.length ? r.children.map((c) => `${c.name}${c.max_score != null ? ` ${c.max_score}` : ""}`).join(" · ") : ruleDetail(r)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="xsmall muted" style={{ marginTop: 6 }}>
        {summary.source === "업체 업로드" ? "우리 업체가 올린 기준서" : "수집한 기준서"}에서 자동 추출 · 파일: {summary.source_file} · 추출 {summary.extracted_at?.slice(0, 10)}
      </p>
    </>
  );
}
