import { useEffect, useRef, useState } from "react";
import { fetchNotice, fetchNoticeSchedule } from "../api/client.js";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import SummaryTab from "../components/detail/SummaryTab.jsx";
import ScoreTab from "../components/detail/ScoreTab.jsx";
import DocsTab from "../components/detail/DocsTab.jsx";
import { Dday, EmptyState, JudgmentBadge, Money, NoticeBox, NoticeStatusBadge, Star, Term, isInactive } from "../components/ui.jsx";
import { downloadIcs, googleCalendarUrl } from "../lib/calendar.js";
import { fullDateTime, shortDateTime, timeLeft } from "../lib/format.js";
import { STEPS, currentStep, nextDeadline } from "../lib/notice.js";
import { useAppState } from "../state/AppState.jsx";
import { useQna } from "../components/QnaPanel.jsx";
import "./NoticeDetail.css";

const TABS = [
  { id: "summary", label: "자격·심사기준 요약" },
  { id: "score", label: "점수 계산" },
  { id: "docs", label: "필요서류" },
];

export default function NoticeDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { notices, loading, isFavorite, toggleFavorite, withJudgment } = useAppState();
  const qna = useQna();
  const listed = notices.find((n) => n.id === id);
  // 진행 중 목록에 없는 공고(마감된 공고, 주소로 바로 들어온 공고)는 따로 받는다
  const [single, setSingle] = useState({ id: null, notice: null, error: null });
  useEffect(() => {
    if (loading || listed || single.id === id) return undefined;
    let alive = true;
    fetchNotice(id)
      .then((n) => alive && setSingle({ id, notice: n, error: null }))
      .catch((e) => alive && setSingle({ id, notice: null, error: e }));
    return () => {
      alive = false;
    };
  }, [id, loading, listed, single.id]);
  // 적격심사 서류 제출기한: 날짜가 없고 기준서 원문 문구만 있다
  const [docDue, setDocDue] = useState(null);
  // 남은 시간 표시를 1분마다 새로 계산
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 60000);
    return () => clearInterval(t);
  }, []);
  useEffect(() => {
    let alive = true;
    setDocDue(null);
    fetchNoticeSchedule(id)
      .then((r) => alive && setDocDue(r.events.find((e) => e.event_type === "QUALIFICATION_DOC_DUE") ?? null))
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [id]);
  const tabRefs = useRef([]);
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "summary";
  const navigate = useNavigate();
  const location = useLocation();
  // 앱 안에서 들어왔으면 이전 화면(필터·스크롤 유지)으로, 주소로 바로 들어왔으면 목록으로
  const goBack = () => (location.key !== "default" ? navigate(-1) : navigate("/"));

  const notice = listed ?? (single.id === id ? withJudgment(single.notice) : null);
  const pending = !notice && (loading || single.id !== id);
  if (pending) return <div className="card card-pad"><div className="skeleton" style={{ height: 160 }} /></div>;
  if (!notice) {
    return (
      <div className="card">
        <EmptyState title="공고를 찾을 수 없습니다" action={<Link to="/" className="btn btn-primary">추천 공고로 돌아가기</Link>}>
          {single.error?.status === 404 ? "수집 범위(한전 공사·용역 적격심사)에 없는 공고번호입니다." : "공고를 불러오지 못했습니다. 잠시 후 다시 시도해 주세요."}
        </EmptyState>
      </div>
    );
  }

  const inactive = isInactive(notice);
  const step = currentStep(notice, now);
  // 기준서 문구에서 '통보일로부터 N일 이상'의 N
  const dueDays = docDue?.note?.match(/(\d+)\s*일\s*이상/)?.[1] ?? docDue?.note?.match(/(\d+)\s*일/)?.[1] ?? null;
  const deadline = nextDeadline(notice);
  const gcal = googleCalendarUrl(notice);
  const selectTab = (next) => setParams(next === "summary" ? {} : { tab: next }, { replace: true });
  const onTabKey = (e, index) => {
    const delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!delta) return;
    const next = (index + delta + TABS.length) % TABS.length;
    selectTab(TABS[next].id);
    tabRefs.current[next]?.focus();
  };

  return (
    <div className="detail">
      <div className="detail-nav no-print">
        <button type="button" className="btn btn-sm back-btn" onClick={goBack}>
          <span aria-hidden="true">←</span> 목록으로
        </button>
        <nav className="breadcrumb small" aria-label="위치">
          <Link to="/">추천 공고</Link> <span aria-hidden="true">›</span> <span className="num">{notice.id}</span>
        </nav>
      </div>

      <header className="card card-pad detail-head" data-reveal>
        <div className="dh-top">
          <div className="nc-badges">
            <Dday iso={deadline.iso} inactive={inactive} />
            <NoticeStatusBadge status={notice.status} />
            {notice.notStarted && <span className="badge badge-outline">참가신청 {fullDateTime(notice.schedule.applyStart)} 시작</span>}
            {notice.judgment.overall && <JudgmentBadge status={notice.judgment.overall} ended={inactive} />}
          </div>
          <Star active={isFavorite(notice.id)} onToggle={() => toggleFavorite(notice.id)} title={notice.title} />
        </div>
        <h1 className="dh-title">{notice.title}</h1>
        <p className="small muted">{notice.issuer} · <span className="num">{notice.id}</span> · {notice.category} · {notice.regionLabel}</p>

        {notice.correction && <NoticeBox tone="warn" icon="!">{notice.correction}</NoticeBox>}

        <dl className="dh-facts">
          <div><dt><Term>추정가격</Term></dt><dd><Money value={notice.estimatedPrice} /></dd></div>
          <div><dt><Term>기초금액</Term></dt><dd><Money value={notice.basePrice} /></dd></div>
          <div><dt>참가신청 마감</dt><dd className="num">{fullDateTime(notice.schedule.applyEnd)}</dd></div>
          <div><dt>투찰 마감</dt><dd className="num">{fullDateTime(notice.schedule.bidEnd)}</dd></div>
          <div><dt>개찰</dt><dd className="num">{fullDateTime(notice.schedule.opening)}</dd></div>
          {notice.aAmount !== null && (
            <div>
              <dt><Term>A값</Term></dt>
              <dd>{notice.aAmount === 0 ? <span className="muted" title="SRM에 A값이 0으로 등록됨 (주로 용역 공고)">해당 없음</span> : <Money value={notice.aAmount} />}</dd>
            </div>
          )}
          {docDue && (
            <div className="span-all doc-due">
              <dt>적격심사 서류 제출기한 <span className="badge badge-warn" style={{ marginLeft: 4 }}>통보 후 지정</span></dt>
              <dd>
                <p>미리 정해진 날짜가 없습니다. 개찰 후 <strong>적격심사 대상자로 통보</strong>를 받으면 그때 기한이 정해집니다.</p>
                {dueDays && (
                  <p>기준서상 통보일로부터 <strong>{dueDays}일 이상</strong>의 기한이 주어집니다. 정확한 마감일은 통보서에서 확인하세요.</p>
                )}
                <details className="source">
                  <summary>기준서 원문 보기</summary>
                  <blockquote>{docDue.note}</blockquote>
                </details>
              </dd>
            </div>
          )}
        </dl>

        {step >= 0 && (
          <ol className="stepper" aria-label="입찰 진행 단계">
            {STEPS.map((s, i) => {
              const iso = notice.schedule[s.key];
              const left = timeLeft(iso, now);
              return (
                <li key={s.key} className={i < step ? "done" : i === step ? "current" : ""} aria-current={i === step ? "step" : undefined}>
                  <span className="step-dot num">{i < step ? "✓" : i + 1}</span>
                  <span className="step-label"><Term term={s.term}>{s.label}</Term></span>
                  <span className="step-date num">{iso ? shortDateTime(iso) : "미정"}</span>
                  <span className={`step-left num${left && i === step ? " now" : ""}`}>{iso ? (left ?? (s.key === "opening" ? "개찰됨" : "마감됨")) : ""}</span>
                </li>
              );
            })}
          </ol>
        )}

        <div className="dh-actions no-print">
          <DocStatus documents={notice.documents} />
          <span className="spacer" />
          <button type="button" className="btn btn-sm btn-primary" onClick={() => qna.openQna(notice.id)}>💬 이 공고에 대해 묻기</button>
          {!inactive && (
            <>
              <button type="button" className="btn btn-sm" onClick={() => downloadIcs(notice)}>📅 내 캘린더에 추가 (.ics)</button>
              {gcal && <a className="btn btn-sm" href={gcal} target="_blank" rel="noreferrer">구글 캘린더</a>}
            </>
          )}
        </div>
      </header>

      <div className="tabs no-print" role="tablist" aria-label="공고 상세" data-reveal style={{ "--i": 1 }}>
        {TABS.map((t, i) => (
          <button
            key={t.id}
            ref={(el) => (tabRefs.current[i] = el)}
            type="button"
            role="tab"
            id={`tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`panel-${t.id}`}
            tabIndex={tab === t.id ? 0 : -1}
            onClick={() => selectTab(t.id)}
            onKeyDown={(e) => onTabKey(e, i)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div role="tabpanel" id={`panel-${tab}`} aria-labelledby={`tab-${tab}`} className="tab-panel">
        {tab === "summary" && <SummaryTab notice={notice} />}
        {tab === "score" && <ScoreTab key={notice.id} notice={notice} />}
        {tab === "docs" && <DocsTab notice={notice} />}
      </div>
    </div>
  );
}

function DocStatus({ documents }) {
  const items = [
    ["notice", "공고문"],
    ["criteria", "적격심사기준"],
  ];
  return (
    <ul className="doc-status" aria-label="문서 확보 상태">
      {items.map(([key, label]) => {
        const st = documents[key]?.status;
        const ok = st === "OK";
        return (
          <li key={key} className={`badge ${ok ? "badge-ok" : st === "PENDING" ? "badge-warn" : "badge-danger"}`} title={documents[key]?.fileName ?? (st === "PENDING" ? "아직 수집하지 않음" : "첨부에서 찾지 못함")}>
            {ok ? "✓" : st === "PENDING" ? "…" : "✕"} {label} {ok ? "확보" : st === "PENDING" ? "수집 전" : "미확보"}
          </li>
        );
      })}
    </ul>
  );
}
