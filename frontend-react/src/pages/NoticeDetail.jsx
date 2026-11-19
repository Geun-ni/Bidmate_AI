import { useRef } from "react";
import { Link, useLocation, useNavigate, useParams, useSearchParams } from "react-router-dom";
import SummaryTab from "../components/detail/SummaryTab.jsx";
import ScoreTab from "../components/detail/ScoreTab.jsx";
import DocsTab from "../components/detail/DocsTab.jsx";
import { Dday, EmptyState, JudgmentBadge, Money, NoticeBox, NoticeStatusBadge, Star, Term, isInactive } from "../components/ui.jsx";
import { downloadIcs, googleCalendarUrl } from "../lib/calendar.js";
import { fullDateTime } from "../lib/format.js";
import { STEPS, currentStep, nextDeadline } from "../lib/notice.js";
import { useAppState } from "../state/AppState.jsx";
import "./NoticeDetail.css";

const TABS = [
  { id: "summary", label: "자격·심사기준 요약" },
  { id: "score", label: "점수 계산" },
  { id: "docs", label: "필요서류" },
];

export default function NoticeDetail() {
  const { id } = useParams();
  const [params, setParams] = useSearchParams();
  const { notices, loading, error, isFavorite, toggleFavorite } = useAppState();
  const tabRefs = useRef([]);
  const tab = TABS.some((t) => t.id === params.get("tab")) ? params.get("tab") : "summary";
  const navigate = useNavigate();
  const location = useLocation();
  // 앱 안에서 들어왔으면 이전 화면(필터·스크롤 유지)으로, 주소로 바로 들어왔으면 목록으로
  const goBack = () => (location.key !== "default" ? navigate(-1) : navigate("/"));

  if (loading) return <div className="card card-pad"><div className="skeleton" style={{ height: 160 }} /></div>;
  const notice = notices.find((n) => n.id === id);
  if (error || !notice) {
    return (
      <div className="card">
        <EmptyState title="공고를 찾을 수 없습니다" action={<Link to="/" className="btn btn-primary">추천 공고로 돌아가기</Link>}>
          공고가 삭제되었거나 목록을 불러오지 못했습니다.
        </EmptyState>
      </div>
    );
  }

  const inactive = isInactive(notice);
  const step = currentStep(notice);
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
            <JudgmentBadge status={notice.judgment.overall} ended={inactive} />
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
          {notice.schedule.qualifyDue && (
            <div>
              <dt>적격심사 서류 기한</dt>
              <dd className="num">
                {fullDateTime(notice.schedule.qualifyDue)}
                {!notice.scheduleMeta?.qualifyDue?.confirmed && (
                  <span className="badge badge-warn" title={notice.scheduleMeta?.qualifyDue?.source} style={{ marginLeft: 6 }}>미확정·추정</span>
                )}
              </dd>
            </div>
          )}
        </dl>

        {step >= 0 && (
          <ol className="stepper" aria-label="입찰 진행 단계">
            {STEPS.map((s, i) => (
              <li key={s} className={i < step ? "done" : i === step ? "current" : ""} aria-current={i === step ? "step" : undefined}>
                <span className="step-dot num">{i < step ? "✓" : i + 1}</span>
                <span className="step-label"><Term>{s}</Term></span>
              </li>
            ))}
          </ol>
        )}

        <div className="dh-actions no-print">
          <DocStatus documents={notice.documents} />
          <span className="spacer" />
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
        const ok = documents[key]?.status === "OK";
        return (
          <li key={key} className={`badge ${ok ? "badge-ok" : "badge-danger"}`} title={documents[key]?.fileName ?? "공식 API 첨부에서 찾지 못함"}>
            {ok ? "✓" : "✕"} {label} {ok ? "확보" : "미확보"}
          </li>
        );
      })}
    </ul>
  );
}
