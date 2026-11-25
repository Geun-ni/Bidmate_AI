import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { noticeEvents } from "../../lib/calendar.js";
import { sameDay, shortDateTime } from "../../lib/format.js";
import { load, save } from "../../lib/storage.js";
import { useAppState } from "../../state/AppState.jsx";
import Select from "../Select.jsx";
import "./FavoritesCalendar.css";

const pad = (n) => String(n).padStart(2, "0");
const ymd = (d) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;

/**
 * 직접 등록한 일정 (예: 11/30 ○○공고 적격심사 서류 제출).
 * 백엔드에 저장 API가 없어 이 브라우저에 사용자별로 저장한다 → 다른 PC에서는 보이지 않는다.
 */
function useCustomEvents(userKey) {
  const key = `customEvents:${userKey}`;
  const [list, setList] = useState(() => load(key, []));
  useEffect(() => setList(load(key, [])), [key]);
  const update = (fn) =>
    setList((prev) => {
      const next = fn(prev);
      save(key, next);
      return next;
    });
  return [list, update];
}

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

function monthGrid(year, month) {
  const first = new Date(year, month, 1);
  const start = new Date(year, month, 1 - first.getDay());
  return Array.from({ length: 42 }, (_, i) => new Date(start.getFullYear(), start.getMonth(), start.getDate() + i));
}

export default function FavoritesCalendar({ notices }) {
  const { user } = useAppState();
  const today = new Date();
  const [cursor, setCursor] = useState({ y: today.getFullYear(), m: today.getMonth() });
  const [selected, setSelected] = useState(today);
  const [custom, setCustom] = useCustomEvents(user?.user_id ?? "guest");
  const [adding, setAdding] = useState(false);

  const events = useMemo(() => {
    const own = custom.map((c) => ({
      key: `custom-${c.id}`,
      type: "custom",
      label: "내 일정",
      tone: "custom",
      date: new Date(`${c.date}T${c.time || "00:00"}`),
      allDay: !c.time,
      confirmed: true,
      source: "직접 등록",
      notice: notices.find((n) => n.id === c.noticeId) ?? null,
      custom: c,
    }));
    return [...notices.flatMap(noticeEvents), ...own].sort((a, b) => a.date - b.date);
  }, [notices, custom]);
  const days = monthGrid(cursor.y, cursor.m);
  const eventsOn = (d) => events.filter((e) => sameDay(e.date, d));
  const selectedEvents = eventsOn(selected);
  const startOfToday = new Date(today.getFullYear(), today.getMonth(), today.getDate());
  const upcoming = events.filter((e) => (e.allDay ? e.date >= startOfToday : e.date >= today) && e.notice?.status !== "CANCELLED");
  const removeCustom = (id) => setCustom((list) => list.filter((c) => c.id !== id));
  const addCustom = (c) => {
    setCustom((list) => [...list, { ...c, id: Date.now() }]);
    const d = new Date(`${c.date}T00:00`);
    setSelected(d);
    setCursor({ y: d.getFullYear(), m: d.getMonth() });
    setAdding(false);
  };
  const dayKey = `${selected.getFullYear()}-${selected.getMonth()}-${selected.getDate()}`;

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
        <li><span className="cal-dot tone-custom" /> 내 일정</li>
      </ul>

      {adding ? (
        <CustomEventForm date={ymd(selected)} notices={notices} onAdd={addCustom} onCancel={() => setAdding(false)} />
      ) : (
        <button type="button" className="btn btn-sm cal-add" onClick={() => setAdding(true)}>+ 일정 직접 추가</button>
      )}

      <div className="cal-agenda">
        <p className="xsmall muted">{selected.getMonth() + 1}월 {selected.getDate()}일 일정</p>
        {selectedEvents.length ? (
          <AgendaList key={`day-${dayKey}`} events={selectedEvents} label={`${selected.getMonth() + 1}월 ${selected.getDate()}일 일정`} onRemove={removeCustom} />
        ) : notices.length === 0 && custom.length === 0 ? (
          <p className="small muted">공고 카드의 ☆를 눌러 관심공고로 등록하면 일정이 여기에 표시됩니다.</p>
        ) : (
          <>
            <p className="small muted">선택한 날짜에 일정이 없습니다.</p>
            {upcoming.length > 0 && (
              <>
                <p className="xsmall muted" style={{ marginTop: 10 }}>다가오는 일정</p>
                <AgendaList key={`up-${dayKey}`} events={upcoming} withDate label="다가오는 일정" onRemove={removeCustom} />
              </>
            )}
          </>
        )}
      </div>
    </section>
  );
}

const PAGE_SIZE = 5;

/** 일정 목록: 5개씩 보여 주고 6개부터 ‹ › 로 넘긴다 (옆 칸이 화면에 고정돼 있어 길어지면 아래가 잘리므로) */
function AgendaList({ events, withDate, label, onRemove }) {
  const [page, setPage] = useState(0);
  const pages = Math.max(1, Math.ceil(events.length / PAGE_SIZE));
  const current = Math.min(page, pages - 1);
  const shown = events.slice(current * PAGE_SIZE, current * PAGE_SIZE + PAGE_SIZE);
  return (
    <>
      <ul className="agenda-list">{shown.map((e) => <AgendaItem key={e.key} event={e} withDate={withDate} onRemove={onRemove} />)}</ul>
      {pages > 1 && (
        <div className="agenda-pager" role="group" aria-label={`${label} 페이지`}>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPage(current - 1)} disabled={current === 0} aria-label="이전 일정">‹</button>
          <span className="xsmall muted num" aria-live="polite">{current + 1} / {pages}</span>
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => setPage(current + 1)} disabled={current === pages - 1} aria-label="다음 일정">›</button>
          <span className="xsmall muted">총 {events.length}건</span>
        </div>
      )}
    </>
  );
}

function AgendaItem({ event, withDate, onRemove }) {
  const cancelled = event.notice?.status === "CANCELLED";
  const full = shortDateTime(event.date.toISOString());
  const when = withDate ? (event.allDay ? full.replace(/ \d\d:\d\d$/, "") : full) : event.allDay ? "종일" : event.date.toTimeString().slice(0, 5);
  const title = event.custom ? event.custom.title : event.notice.title;
  const sub = event.custom && event.notice ? event.notice.title : null;
  const body = (
    <>
      <span className={`cal-dot tone-${event.tone}${event.confirmed ? "" : " unconfirmed"}`} aria-hidden="true" />
      <span className="agenda-text">
        <span className="agenda-label">
          {event.label} {when}
          {!event.confirmed && <span className="badge badge-warn" style={{ marginLeft: 6 }}>미확정</span>}
          {cancelled && <span className="badge badge-muted" style={{ marginLeft: 6 }}>취소됨</span>}
        </span>
        <span className="agenda-title">
          {title}
          {sub && <span className="muted"> · {sub}</span>}
        </span>
      </span>
    </>
  );
  return (
    <li className={event.custom ? "agenda-custom" : undefined}>
      {event.notice ? (
        <Link to={`/notices/${event.notice.id}`} className="agenda-item" title={`출처: ${event.source}`}>{body}</Link>
      ) : (
        <div className="agenda-item" title="직접 등록한 일정">{body}</div>
      )}
      {event.custom && onRemove && (
        <button type="button" className="agenda-remove" onClick={() => onRemove(event.custom.id)} aria-label={`${title} 일정 삭제`} title="삭제">✕</button>
      )}
    </li>
  );
}

/** 일정 직접 추가: 날짜(필수), 시간(선택, 비우면 종일), 내용(필수), 관련 관심공고(선택) */
function CustomEventForm({ date, notices, onAdd, onCancel }) {
  const [form, setForm] = useState({ date, time: "", title: "", noticeId: "" });
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));
  const ok = Boolean(form.date && form.title.trim());
  return (
    <form
      className="cal-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) onAdd({ ...form, title: form.title.trim(), noticeId: form.noticeId || null });
      }}
    >
      <div className="cal-form-row">
        <input className="input" type="date" value={form.date} onChange={(e) => set({ date: e.target.value })} aria-label="날짜" required />
        <input className="input" type="time" value={form.time} onChange={(e) => set({ time: e.target.value })} aria-label="시간 (선택)" title="비워 두면 종일 일정" />
      </div>
      <input
        className="input"
        value={form.title}
        onChange={(e) => set({ title: e.target.value })}
        placeholder="내용 (예: 적격심사 서류 제출)"
        maxLength={60}
        aria-label="일정 내용"
        autoFocus
        required
      />
      {notices.length > 0 && (
        <Select
          size="sm"
          ariaLabel="관련 관심공고"
          value={form.noticeId}
          onChange={(v) => set({ noticeId: v })}
          placeholder="관련 공고 없음"
          options={[{ value: "", label: "관련 공고 없음" }, ...notices.map((n) => ({ value: n.id, label: n.title }))]}
        />
      )}
      <div className="cal-form-actions">
        <span className="xsmall muted">이 브라우저에만 저장됩니다.</span>
        <button type="button" className="btn btn-ghost btn-sm" onClick={onCancel}>취소</button>
        <button type="submit" className="btn btn-primary btn-sm" disabled={!ok}>추가</button>
      </div>
    </form>
  );
}
