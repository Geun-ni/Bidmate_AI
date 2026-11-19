import { useState } from "react";
import { calcAdjustment, calcExperienceScore, calcManagementRatio, calcPriceScore, calcTotal } from "../../lib/score.js";
import { won } from "../../lib/format.js";
import MoneyInput from "../MoneyInput.jsx";
import { EmptyState, NoticeBox, Source, Term } from "../ui.jsx";
import "./ScoreTab.css";

const OFFICE_OPTIONS = [
  { value: "own", label: "해당 사업소", points: 4 },
  { value: "adjacent", label: "인접 사업소", points: 3 },
  { value: "region", label: "같은 광역지역의 비인접 사업소", points: 2 },
  { value: "outside", label: "광역지역 밖", points: 0 },
];

/** 반원 계기판: 바늘이 점수까지 움직이고, 기준 점수 위치에 주황 눈금 */
function Gauge({ value, pass, tentative = false }) {
  const angle = (score) => -90 + 180 * Math.min(1, Math.max(0, score / 100));
  const tick = (angle(pass) * Math.PI) / 180;
  const tx = 50 + 40 * Math.sin(tick);
  const ty = 50 - 40 * Math.cos(tick);
  return (
    <svg className="gauge" viewBox="0 0 100 56" aria-hidden="true">
      <path d="M8 50 A42 42 0 0 1 92 50" fill="none" stroke="var(--soft)" strokeWidth="8" strokeLinecap="round" />
      <line x1={tx} y1={ty} x2={50 + 48 * Math.sin(tick)} y2={50 - 48 * Math.cos(tick)} stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />
      <line
        className="gauge-needle"
        x1="50" y1="50" x2="50" y2="14"
        stroke={value === null ? "var(--border-strong)" : tentative ? "var(--text-3)" : "var(--text)"}
        strokeWidth="3" strokeLinecap="round" strokeDasharray={tentative ? "4 3" : undefined}
        style={{ transform: `rotate(${value === null ? -90 : angle(value)}deg)` }}
      />
      <circle cx="50" cy="50" r="4.5" fill="var(--text)" />
    </svg>
  );
}

function ScoreValue({ value, max }) {
  if (value === null || value === undefined) return <span className="badge badge-warn">미산정</span>;
  return (
    <span className="score-val num">
      <strong>{value}</strong>
      {max !== undefined && <span className="muted"> / {max}점</span>}
    </span>
  );
}

function YesNo({ name, value, onChange }) {
  const opts = [
    ["yes", "해당"],
    ["no", "해당 없음"],
    ["unknown", "모름"],
  ];
  return (
    <div className="yn" role="radiogroup" aria-label={name}>
      {opts.map(([v, l]) => (
        <label key={v} className={`yn-opt${value === v ? " on" : ""}`}>
          <input type="radio" name={name} value={v} checked={value === v} onChange={() => onChange(v)} onClick={() => value === v && onChange("")} />
          {l}
        </label>
      ))}
    </div>
  );
}

/** 가감점 부호: 감점 항목이면 음수 */
function adjustmentSign(a) {
  if (a.detail?.kind === "count") return a.detail.perUnit;
  if (a.detail?.kind === "select") return 1;
  return a.points;
}

/** 표시용 점수 범위: "+0.3 ~ +0.5점", "건당 -0.5점 (최대 -1점)" */
function adjustmentRange(a) {
  const d = a.detail;
  if (!d) return `${a.points > 0 ? "+" : ""}${a.points}점`;
  if (d.kind === "select") {
    const pts = d.options.map((o) => o.points);
    return `+${Math.min(...pts)} ~ +${Math.max(...pts)}점`;
  }
  return `건당 ${d.perUnit}점 (최대 ${d.cap}점)`;
}

function RatioField({ label, unit, value, onChange, hint, result }) {
  return (
    <label className="field">
      <span className="field-label small">{label}</span>
      <span className="unit-wrap">
        <input className="input num" type="number" inputMode="decimal" min="0" step="0.01" value={value} onChange={(e) => onChange(e.target.value)} placeholder="0" />
        <span className="money-unit">{unit}</span>
      </span>
      <span className="field-hint">{result ?? hint}</span>
    </label>
  );
}

/** FUNC-022: '해당'일 때 등급 또는 횟수를 추가로 받는다 */
function AdjustmentDetail({ item, value, onChange, points }) {
  const d = item.detail;
  return (
    <div className="adj-detail">
      {d.kind === "select" ? (
        <div className="seg-light" role="group" aria-label={d.label}>
          {d.options.map((o) => (
            <button key={o.label} type="button" aria-pressed={value === o.label} onClick={() => onChange(value === o.label ? "" : o.label)}>
              {o.label} +{o.points}
            </button>
          ))}
        </div>
      ) : (
        <label className="adj-count">
          <span className="xsmall muted">{d.label}</span>
          <input className="input num" type="number" min="1" step="1" value={value} onChange={(e) => onChange(e.target.value)} placeholder="0" />
          <span className="xsmall muted">건</span>
        </label>
      )}
      <span className="xsmall">
        {points === null ? <span className="badge badge-warn">{d.label} 입력 필요</span> : <strong className="num">{points > 0 ? `+${points}` : points}점 적용</strong>}
      </span>
      {d.period && <span className="xsmall muted">{d.period}</span>}
    </div>
  );
}

export default function ScoreTab({ notice }) {
  const { criteria, priceFormula, capability, adjustments, separatePenalty } = notice;
  const [bidPrice, setBidPrice] = useState(null);
  const [plannedPrice, setPlannedPrice] = useState(null);
  const [performance, setPerformance] = useState(null);
  const [office, setOffice] = useState("");
  const [mgmtMethod, setMgmtMethod] = useState("credit");
  const [creditGrade, setCreditGrade] = useState("");
  const [ratio, setRatio] = useState({ debt: "", current: "", years: "" });
  const [answers, setAnswers] = useState({});
  const [details, setDetails] = useState({});
  const [penalty, setPenalty] = useState("");

  if (criteria.status !== "OK" || !priceFormula) {
    return (
      <section className="card">
        <EmptyState title="적격심사기준이 없어 점수를 계산할 수 없습니다">
          공식 API 첨부파일에서 적격심사기준을 찾지 못했습니다. 임의 기준으로 계산하지 않습니다. SRM 첨부파일을 직접 확인해 주세요.
        </EmptyState>
      </section>
    );
  }

  const price = calcPriceScore({ bidPrice, plannedPrice, formula: priceFormula });
  const experience = calcExperienceScore({ performance, assessedAmount: capability.assessedAmount, maxPoints: 14 });
  const officePoints = OFFICE_OPTIONS.find((o) => o.value === office)?.points ?? null;
  const mgmtTable = capability.management;
  const ratioResult = calcManagementRatio(ratio, mgmtTable);
  const mgmt =
    mgmtMethod === "credit"
      ? mgmtTable.credit.find((c) => c.label === creditGrade)?.points ?? null
      : ratioResult?.total ?? null;
  // 등급·횟수를 반영한 가감점. '해당'인데 세부 입력이 없으면 미산정으로 넘긴다.
  const adjList = adjustments.map((a) => {
    const r = calcAdjustment(a, answers[a.id], details[a.id]);
    // 최대 가능 점수 계산용: 아직 답하지 않은 가점 항목은 최고 등급을 받는다고 가정
    const bestPoints = a.detail?.kind === "select" ? Math.max(...a.detail.options.map((o) => o.points)) : a.detail ? 0 : Math.max(0, a.points);
    return { ...a, answer: r.answer, uiAnswer: answers[a.id], points: r.points, missing: r.missing, bestPoints };
  });
  const total = calcTotal({
    parts: [
      { label: "시공경험(실적)", value: experience, max: 14 },
      { label: "지리·지형 숙지도", value: officePoints, max: 4 },
      { label: "경영상태", value: mgmt, max: 12 },
    ],
    adjustments: adjList,
    separatePenalty: { ...separatePenalty, answer: penalty },
    price: { ...price, max: priceFormula.maxPoints },
    capabilityCap: criteria.capabilityCap,
  });

  return (
    <div className="score">
      {/* 결과 요약: 화면 위쪽 고정 */}
      <section className="card card-pad score-summary" aria-live="polite" aria-labelledby="score-sum-title">
        <div className="sum-main">
          <Gauge value={total.state === "done" ? total.total : total.partial} tentative={total.state !== "done"} pass={criteria.passScore} />
          <div>
            <h2 id="score-sum-title" className="xsmall muted">
              {total.state === "done" ? "예상 총점" : "현재까지 산정된 점수"}
            </h2>
            {total.state === "done" ? (
              <p className="total num"><strong>{total.total}</strong>점</p>
            ) : (
              <p className="total num tentative">
                <strong>{total.partial}</strong>점 <span className="badge badge-warn">산정 보류</span>
              </p>
            )}
            {total.state !== "done" &&
              (total.maxPossible < criteria.passScore ? (
                // 한 줄 안에서 문구만 바꿔 카드 높이가 흔들리지 않게 한다
                <p className="xsmall num max-short" role="status">
                  최대 {total.maxPossible}점 · 남은 항목을 모두 최고로 받아도 기준 점수보다{" "}
                  {Math.round((criteria.passScore - total.maxPossible) * 100) / 100}점 낮습니다
                </p>
              ) : (
                <p className="xsmall muted num">남은 항목을 모두 최고로 받으면 최대 {total.maxPossible}점</p>
              ))}
          </div>
        </div>
        <div className="pass">
          <p className="xsmall muted"><Term>낙찰자 결정 기준 점수</Term></p>
          <p className="num"><strong>{criteria.passScore}</strong>점</p>
        </div>
        <div className="verdict">
          {total.state === "done" ? (
            total.total >= criteria.passScore ? (
              <span className="badge badge-ok">기준보다 {Math.round((total.total - criteria.passScore) * 100) / 100}점 높음</span>
            ) : (
              <span className="badge badge-danger">기준보다 {Math.round((criteria.passScore - total.total) * 100) / 100}점 부족</span>
            )
          ) : (
            <p className="small muted pending-line" title={total.pending.join(", ")}>
              <strong className="num">남은 항목 {total.pending.length}개</strong> · {total.pending.join(", ")}
            </p>
          )}
        </div>
        <p className="xsmall muted span-all">업체가 입력한 정보와 추출된 기준으로 계산한 <strong>참고용 시뮬레이션</strong>이며, 공식 심사 결과나 낙찰을 보장하지 않습니다.</p>
      </section>

      {/* 1. 입찰가격 점수 (회의 1안) */}
      <section className="card card-pad" aria-labelledby="price-title" data-reveal>
        <div className="card-head">
          <h2 id="price-title" className="section-title">① 입찰가격 점수</h2>
          <ScoreValue value={price.state === "done" ? price.score : null} max={priceFormula.maxPoints} />
        </div>
        <p className="section-desc">입찰가격과 예정가격만 입력하면 공고의 산식으로 자동 계산합니다. 투찰가격을 추천하는 기능은 아닙니다.</p>
        <p className="xsmall muted">예시 산식: (입찰가격 − A값) ÷ (예정가격 − A값)이 {priceFormula.baseRate * 100}%에 가까울수록 높은 점수, 1%p 벗어날 때마다 {priceFormula.k}점 감점, 최저 {priceFormula.minPoints}점.</p>

        <div className="price-grid">
          <MoneyInput label="입찰가격 (투찰 예정 금액)" required value={bidPrice} onChange={setBidPrice} hint="써낼 금액을 입력하세요" />
          <MoneyInput label={<Term>예정가격</Term>} required value={plannedPrice} onChange={setPlannedPrice} hint="개찰 때 정해지는 금액 (기초금액으로 대체하지 않음)" />
          <div className="field">
            <span className="field-label"><Term>A값</Term> <span className="badge badge-brand">공고문 자동 추출</span></span>
            {priceFormula.aValue !== null ? (
              <>
                <p className="auto-val num">{won(priceFormula.aValue)}</p>
                <Source text={priceFormula.aValueSource} />
              </>
            ) : (
              <p className="auto-val missing">추출 실패</p>
            )}
          </div>
        </div>

        {price.state === "blocked" && (
          <NoticeBox tone="danger" icon="!"><strong>계산 보류 — 원문 확인 필요.</strong> {price.reason}</NoticeBox>
        )}
        {price.state === "error" && <NoticeBox tone="warn" icon="!">{price.reason}</NoticeBox>}
        {price.state === "done" && (
          <>
            <p className="small muted">
              산식 비율 <strong className="num">{(price.ratio * 100).toFixed(3)}%</strong> (기준 {priceFormula.baseRate * 100}%와 {Math.abs(priceFormula.baseRate * 100 - price.ratio * 100).toFixed(3)}%p 차이) · 투찰률 <span className="num">{(price.bidRate * 100).toFixed(3)}%</span>
            </p>
            {price.atMin && (
              <NoticeBox tone="warn" icon="!">
                <strong>최저점({priceFormula.minPoints}점)이 적용됐습니다.</strong> 이 산식은 산식 비율이 기준 {priceFormula.baseRate * 100}%에서 1%p 멀어질 때마다 {priceFormula.k}점씩 깎이고, 최저 {priceFormula.minPoints}점까지 내려갑니다.
              </NoticeBox>
            )}
            {(price.bidRate < 0.7 || price.bidRate > 1.1) && (
              <NoticeBox tone="warn" icon="?">
                입찰가격이 예정가격의 {(price.bidRate * 100).toFixed(1)}%입니다. 보통 예정가격 근처에서 투찰하므로 <strong>자릿수(0 개수)를 잘못 입력했는지</strong> 확인해 보세요.
              </NoticeBox>
            )}
          </>
        )}

        <details className="formula">
          <summary>적용 산식 보기</summary>
          <p className="num">{priceFormula.text}</p>
          <p className="xsmall muted">{priceFormula.rounding} · 최저 {priceFormula.minPoints}점 · 상한 {priceFormula.maxPoints}점</p>
          <Source text={priceFormula.source} />
        </details>
      </section>

      {/* 2. 수행능력 */}
      <section className="card card-pad" aria-labelledby="cap-title" data-reveal>
        <div className="card-head">
          <h2 id="cap-title" className="section-title">② 당해공사 수행능력</h2>
          {[experience, officePoints, mgmt].every((v) => v === null) ? (
            <ScoreValue value={null} />
          ) : (
            <span>
              {[experience, officePoints, mgmt].some((v) => v === null) && <span className="badge badge-warn" style={{ marginRight: 8 }}>일부 미산정</span>}
              <ScoreValue value={total.capability} max={criteria.capabilityCap} />
            </span>
          )}
        </div>
        <p className="section-desc">입력하지 않은 항목은 0점이 아니라 &lsquo;미산정&rsquo;으로 둡니다. 가점·감점을 합쳐 {criteria.capabilityCap}점을 넘지 않습니다.</p>

        <div className="cap-item">
          <div className="cap-head">
            <h3>시공경험 — 최근 5년 실적 <span className="muted small">(14점)</span></h3>
            <ScoreValue value={experience} max={14} />
          </div>
          <div className="price-grid two">
            <div className="field">
              <span className="field-label"><Term>사정액</Term> <span className="badge badge-brand">기준서 자동 추출</span></span>
              <p className="auto-val num">{won(capability.assessedAmount)}</p>
              <Source text={capability.assessedSource} />
            </div>
            <MoneyInput label="최근 5년 해당 업종 실적 누계액" value={performance} onChange={setPerformance} hint="전기공사협회 실적 기준" />
          </div>
          <p className="xsmall muted">계산: (실적 누계액 ÷ 사정액) × 14, 상한 14점</p>
        </div>

        <div className="cap-item">
          <div className="cap-head">
            <h3>지리·지형·배전계통 숙지도 <span className="muted small">(4점)</span></h3>
            <ScoreValue value={officePoints} max={4} />
          </div>
          <p className="small muted">본점이 어느 사업소 관할에 있나요? 해당: {capability.offices.own} · 인접: {capability.offices.adjacent.join(", ")}</p>
          <div className="option-list">
            {OFFICE_OPTIONS.map((o) => (
              <label key={o.value} className={`option${office === o.value ? " on" : ""}`}>
                <input
                  type="radio"
                  name="office"
                  value={o.value}
                  checked={office === o.value}
                  onChange={() => setOffice(o.value)}
                  onClick={() => office === o.value && setOffice("")} // 선택된 항목을 다시 누르면 해제
                />
                <span>{o.label}</span>
                <span className="num muted">{o.points}점</span>
              </label>
            ))}
          </div>
        </div>

        <div className="cap-item">
          <div className="cap-head">
            <h3>경영상태 <span className="muted small">(12점)</span></h3>
            <ScoreValue value={mgmt} max={12} />
          </div>
          <div className="seg-light" role="group" aria-label="경영상태 평가 방식">
            <button type="button" aria-pressed={mgmtMethod === "credit"} onClick={() => setMgmtMethod("credit")}>신용평가등급</button>
            <button type="button" aria-pressed={mgmtMethod === "ratio"} onClick={() => setMgmtMethod("ratio")}>재무비율</button>
          </div>
          {mgmtMethod === "credit" ? (
            <div className="option-list">
              {mgmtTable.credit.map((c) => (
                <label key={c.label} className={`option${creditGrade === c.label ? " on" : ""}`}>
                  <input type="radio" name="credit" checked={creditGrade === c.label} onChange={() => setCreditGrade(c.label)} onClick={() => creditGrade === c.label && setCreditGrade("")} />
                  <span>{c.label}</span>
                  <span className="num muted">{c.points}점</span>
                </label>
              ))}
            </div>
          ) : (
            <>
              <div className="ratio-grid">
                <RatioField label="부채비율" unit="%" value={ratio.debt} onChange={(v) => setRatio((r) => ({ ...r, debt: v }))}
                  hint={`업종 평균 ${mgmtTable.industryAvg.debtRatio}%`} result={ratioResult && `${ratioResult.debtPts} / ${mgmtTable.ratio.max.debt}점 · 평균의 ${(ratioResult.debtRel * 100).toFixed(1)}%`} />
                <RatioField label="유동비율" unit="%" value={ratio.current} onChange={(v) => setRatio((r) => ({ ...r, current: v }))}
                  hint={`업종 평균 ${mgmtTable.industryAvg.currentRatio}%`} result={ratioResult && `${ratioResult.currentPts} / ${mgmtTable.ratio.max.current}점 · 평균의 ${(ratioResult.currentRel * 100).toFixed(1)}%`} />
                <RatioField label="영업기간" unit="년" value={ratio.years} onChange={(v) => setRatio((r) => ({ ...r, years: v }))}
                  hint="공고일 기준" result={ratioResult && `${ratioResult.periodPts} / ${mgmtTable.ratio.max.period}점`} />
              </div>
              <Source text={mgmtTable.industryAvg.source} label="업종 평균 근거" />
            </>
          )}
          {!mgmtTable.verified && (
            <p className="xsmall muted">구간별 점수는 시연용 예시표입니다. 실제로는 공고 기준서에서 추출한 등급표를 씁니다.</p>
          )}
        </div>
      </section>

      {/* 3. 가점·감점 */}
      <section className="card card-pad" aria-labelledby="adj-title" data-reveal>
        <h2 id="adj-title" className="section-title">③ 가점·감점</h2>
        <p className="section-desc">기준서에 적힌 항목만 표시합니다. &lsquo;모름&rsquo;이 하나라도 있으면 총점을 확정하지 않습니다.</p>
        <ul className="adj-list">
          {adjList.map((a) => (
            <li key={a.id}>
              <div>
                <p className="adj-label">
                  {a.label} <span className={`num ${adjustmentSign(a) < 0 ? "minus" : "plus"}`}>{adjustmentRange(a)}</span>
                </p>
                <Source text={a.source} />
              </div>
              <div className="adj-right">
                <YesNo name={a.label} value={a.uiAnswer} onChange={(v) => setAnswers((s) => ({ ...s, [a.id]: v }))} />
                {a.uiAnswer === "yes" && a.detail && (
                  <AdjustmentDetail item={a} value={details[a.id] ?? ""} onChange={(v) => setDetails((s) => ({ ...s, [a.id]: v }))} points={a.missing ? null : a.points} />
                )}
              </div>
            </li>
          ))}
          <li className="separate">
            <div>
              <p className="adj-label">
                {separatePenalty.label} <span className="num minus">{separatePenalty.points}점</span>
                <span className="badge badge-danger" style={{ marginLeft: 6 }}>별도 감점</span>
              </p>
              <Source text={separatePenalty.source} />
            </div>
            <YesNo name={separatePenalty.label} value={penalty} onChange={setPenalty} />
          </li>
        </ul>
      </section>
    </div>
  );
}
