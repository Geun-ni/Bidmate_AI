import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { noticeEvents } from "../../lib/calendar.js";
import { sameDay, shortDateTime } from "../../lib/format.js";
import "./FavoritesCalendar.css";

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - first.getDay());
  return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
}

export default function FavoritesCalendar({ notices }) {
  const today = new Date();
  const [cursor, setCursor] = useState({ y: today.getFullYear(), m: today.getMonth() });
  const [selected, setSelected] = useState(today);

  const events = useMemo(() => notices.flatMap(noticeEvents).sort((a, b) => a.date - b.date), [notices]);
  const days = monthGrid(cursor.y, cursor.m);
  const eventsOn = (d) => events.filter((e) => sameDay(e.date, d));
  const selectedEvents = eventsOn(selected);
  const upcoming = events.filter((e) => e.date >= today && e.notice.status !== "CANCELLED").slice(0, 3);

  const move = (delta) =>
    setCursor(({ y, m }) => {
      const d = new Date(y, m + delta, 1);
      return { y: d.getFullYear(), m: d.getMonth() };
    });

  return (
    <section className="card card-pad cal" aria-labelledby="cal-title">
      <div className="card-head">
        <h2 id="cal-title" className="card-title">관심공고 일정</h2>
        <div className="cal-nav">
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => move(-1)} aria-label="이전 달">‹</button>
          <span className="cal-month num" aria-live="polite">{cursor.y}.{String(cursor.m + 1).padStart(2, "0")}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => move(1)} aria-label="다음 달">›</button>
        </div>
      </div>

      <div className="cal-grid" role="grid" aria-label="관심공고 달력">
        {WEEK.map((w, i) => (
          <div key={w} role="columnheader" className={`cal-wd${i === 0 ? " sun" : i === 6 ? " sat" : ""}`}>{w}</div>
        ))}
        {days.map((d) => {
          const evs = eventsOn(d);
          const outside = d.getMonth() !== cursor.m;
          const isToday = sameDay(d, today);
          const isSel = sameDay(d, selected);
          return (
            <button
              key={d.toISOString()}
              type="button"
              role="gridcell"
              className={`cal-day${outside ? " out" : ""}${isToday ? " today" : ""}${isSel ? " sel" : ""}`}
              aria-selected={isSel}
              aria-label={`${d.getMonth() + 1}월 ${d.getDate()}일${evs.length ? `, 일정 ${evs.length}건` : ""}`}
              onClick={() => setSelected(d)}
            >
              <span className="num">{d.getDate()}</span>
              {evs.length > 0 && (
                <span className="cal-dots" aria-hidden="true">
                  {evs.slice(0, 3).map((e) => <span key={e.key} className={`cal-dot tone-${e.tone}${e.confirmed ? "" : " unconfirmed"}`} />)}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <ul className="cal-legend xsmall muted" aria-label="범례">
        <li><span className="cal-dot tone-warn" /> 참가신청 마감</li>
        <li><span className="cal-dot tone-danger" /> 투찰 마감</li>
        <li><span className="cal-dot tone-brand" /> 개찰</li>
        <li><span className="cal-dot tone-accent" /> 적격심사 서류</li>
        <li><span className="cal-dot tone-brand unconfirmed" /> 미확정</li>
      </ul>

      <div className="cal-agenda">
        <p className="xsmall muted">{selected.getMonth() + 1}월 {selected.getDate()}일 일정</p>
        {selectedEvents.length ? (
          <ul>
            {selectedEvents.map((e) => <AgendaItem key={e.key} event={e} />)}
          </ul>
        ) : notices.length === 0 ? (
          <p className="small muted">공고 카드의 ☆를 눌러 관심공고로 등록하면 일정이 여기에 표시됩니다.</p>
        ) : (
          <>
            <p className="small muted">선택한 날짜에 일정이 없습니다.</p>
            {upcoming.length > 0 && (
              <>
                <p className="xsmall muted" style={{ marginTop: 10 }}>다가오는 일정</p>
                <ul>{upcoming.map((e) => <AgendaItem key={e.key} event={e} withDate />)}</ul>
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}

function AgendaItem({ event, withDate }) {
  const cancelled = event.notice.status === "CANCELLED";
  return (
    <li>
      <Link to={`/notices/${event.notice.id}`} className="agenda-item">
        <span className={`cal-dot tone-${event.tone}${event.confirmed ? "" : " unconfirmed"}`} aria-hidden="true" />
        <span className="agenda-text">
          <span className="agenda-label">
            {event.label} {withDate ? shortDateTime(event.date.toISOString()) : event.date.toTimeString().slice(0, 5)}
            {!event.confirmed && <span className="badge badge-warn" style={{ marginLeft: 6 }}>미확정</span>}
            {cancelled && <span className="badge badge-muted" style={{ marginLeft: 6 }}>취소됨</span>}
          </span>
          <span className="agenda-title">{event.notice.title}</span>
          <span className="agenda-src">출처: {event.source}</span>
        </span>
      </Link>
    </li>
  );
}
