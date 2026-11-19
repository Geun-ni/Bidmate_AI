import { Disclaimer, EmptyState, JudgmentBadge, NoticeBox, Source, Term } from "../ui.jsx";
import Attachments from "./Attachments.jsx";

const VERIFY = {
  AFTER_OPENING: { label: "개찰 후 확인", tone: "brand" },
  SELF: { label: "업체 직접 확인", tone: "warn" },
  SOURCE: { label: "원문 확인 필요", tone: "danger" },
};

export default function SummaryTab({ notice }) {
  const { judgment, extraConditions, criteria, documents } = notice;
  const noticeMissing = documents.notice.status !== "OK";
  const criteriaMissing = documents.criteria.status !== "OK";

  return (
    <>
      <section className="card card-pad" aria-labelledby="sum-judge" data-reveal>
        <h2 id="sum-judge" className="section-title">
          SRM 투찰제한 <Term>1차 판정</Term>
          <JudgmentBadge status={judgment.overall} />
        </h2>
        <p className="section-desc">SRM에 등록된 조건과 우리 업체 정보를 코드로 비교했습니다.</p>
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
                    <JudgmentBadge status={item.status} compact />
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
          <Disclaimer>SRM 자동심사를 충족해도 최종 참가 자격을 보장하지 않습니다. 아래 공고문 추가 조건과 담당자 확인이 우선합니다.</Disclaimer>
        </div>
      </section>

      <section className="card card-pad" aria-labelledby="sum-extra" data-reveal>
        <h2 id="sum-extra" className="section-title">공고문 추가 참가 조건</h2>
        <p className="section-desc">SRM 코드로는 확인할 수 없어 공고문에서 따로 찾은 조건입니다. AI가 추출했으므로 원문과 함께 확인하세요.</p>
        {noticeMissing ? (
          <EmptyState title="공고문을 확보하지 못했습니다">SRM에서 공고문 원문을 직접 확인해 주세요.</EmptyState>
        ) : extraConditions.length === 0 ? (
          <p className="small muted" style={{ marginTop: 12 }}>공고문에서 추가 조건을 찾지 못했습니다. 원문에서 한 번 더 확인하세요.</p>
        ) : (
          <ul className="cond-list">
            {extraConditions.map((c) => (
              <li key={c.text}>
                <span className={`badge badge-${VERIFY[c.verify].tone}`}>{VERIFY[c.verify].label}</span>
                <div>
                  <p>{c.text}</p>
                  <Source text={c.source} />
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card card-pad" aria-labelledby="sum-criteria" data-reveal>
        <h2 id="sum-criteria" className="section-title"><Term>적격심사</Term> 기준</h2>
        {criteriaMissing ? (
          <div style={{ marginTop: 12 }}>
            <NoticeBox tone="danger" icon="!">
              <strong>적격심사기준 파일을 확보하지 못했습니다.</strong> 공식 API가 첨부파일을 최대 5개까지만 제공해 누락됐을 수 있습니다. 배점·점수 계산·적격심사 서류 안내가 제한되니 SRM 첨부파일을 직접 확인하세요.
            </NoticeBox>
          </div>
        ) : (
          <>
            <p className="pass-score">
              <Term>낙찰자 결정 기준 점수</Term> <strong className="num">{criteria.passScore}점</strong> 이상
            </p>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr><th scope="col">평가 항목</th><th scope="col">배점</th><th scope="col">내용</th></tr>
                </thead>
                <tbody>
                  {criteria.items.map((i) => (
                    <tr key={i.name}>
                      <td><strong>{i.name}</strong></td>
                      <td className="num">{i.points === null ? "가감" : `${i.points}점`}</td>
                      <td>{i.detail}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <Source text={criteria.source} />
            <p className="xsmall muted" style={{ marginTop: 6 }}>표준 적격심사기준은 공고마다 달라질 수 있습니다. 파일: {documents.criteria.fileName}</p>
          </>
        )}
      </section>

      <Attachments notice={notice} />
    </>
  );
}
