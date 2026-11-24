import { Link } from "react-router-dom";
import { shortDateTime } from "../../lib/format.js";
import { nextDeadline } from "../../lib/notice.js";
import { useAppState } from "../../state/AppState.jsx";
import { Dday, JudgmentBadge, Money, NoticeStatusBadge, Star, isInactive } from "../ui.jsx";
import "./NoticeCard.css";

export default function NoticeCard({ notice }) {
  const { isFavorite, toggleFavorite } = useAppState();
  const inactive = isInactive(notice);
  const deadline = nextDeadline(notice);
  const missing = Object.entries(notice.documents).filter(([, d]) => d.status === "MISSING");
  const reviewReasons = notice.judgment?.reviewReasons ?? [];
  const overall = notice.judgment?.overall;

  return (
    <article className={`notice-card card j-${overall ?? "NONE"}${inactive ? " is-inactive" : ""}`}>
      <div className="nc-top">
        <div className="nc-badges">
          <Dday iso={deadline.iso} inactive={inactive} />
          <NoticeStatusBadge status={notice.status} />
          {notice.notStarted && <span className="badge badge-outline" title="참가신청 기간이 아직 시작되지 않았습니다">신청 {shortDateTime(notice.schedule.applyStart)} 시작</span>}
          {overall && <JudgmentBadge status={overall} ended={inactive} />}
        </div>
        <Star active={isFavorite(notice.id)} onToggle={() => toggleFavorite(notice.id)} title={notice.title} />
      </div>

      <h3 className="nc-title">
        <Link to={`/notices/${notice.id}`} className="nc-link">{notice.title}</Link>
      </h3>
      <p className="nc-meta small muted">
        <span>{notice.issuer}</span>
        <span aria-hidden="true">·</span>
        <span className="num">{notice.id}</span>
        <span aria-hidden="true">·</span>
        <span>{notice.regionLabel}</span>
      </p>

      <dl className="nc-facts">
        <div>
          <dt>추정가격</dt>
          <dd><Money value={notice.estimatedPrice} /></dd>
        </div>
        <div>
          <dt>참가신청 마감</dt>
          <dd className="num">{shortDateTime(notice.schedule.applyEnd)}</dd>
        </div>
        <div>
          <dt>투찰 마감</dt>
          <dd className="num">{shortDateTime(notice.schedule.bidEnd)}</dd>
        </div>
      </dl>

      {(reviewReasons.length > 0 || notice.correction || missing.length > 0) && (
        <ul className="nc-notes">
          {notice.correction && <li className="note-warn">{notice.correction}</li>}
          {reviewReasons.map((r) => (
            <li key={r} className="note-warn">확인 필요: {r}</li>
          ))}
          {missing.length > 0 && (
            <li className="note-danger">
              {missing.map(([k]) => (k === "criteria" ? "적격심사기준" : "공고문")).join("·")} 미확보 — 일부 분석이 제한됩니다
            </li>
          )}
        </ul>
      )}
    </article>
  );
}
