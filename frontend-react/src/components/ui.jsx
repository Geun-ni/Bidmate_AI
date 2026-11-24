import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { GLOSSARY } from "../lib/glossary.js";
import { dday, koreanWon, won } from "../lib/format.js";

/* ───────── 판정 상태 ───────── */
export const JUDGMENT = {
  MATCH: { label: "참가 가능(1차)", tone: "ok" },
  REVIEW: { label: "입찰참가자격 확인 필요", tone: "warn" },
  MISMATCH: { label: "조건 불일치", tone: "danger" },
  NOT_APPLICABLE: { label: "제한 없음", tone: "muted" },
};

const ITEM_STATUS = {
  MATCH: { label: "충족", tone: "ok" },
  REVIEW: { label: "확인 필요", tone: "warn" },
  MISMATCH: { label: "불일치", tone: "danger" },
  NOT_APPLICABLE: { label: "해당 없음", tone: "muted" },
};

/** 마감·취소 공고에서는 "참가 가능"처럼 보이지 않도록 회색으로 낮춰 보여준다 */
const ENDED_LABEL = { MATCH: "조건 충족 · 종료된 공고", REVIEW: "확인 필요 · 종료된 공고", MISMATCH: "조건 불일치 · 종료된 공고" };

export function JudgmentBadge({ status, compact = false, ended = false }) {
  if (ended && !compact) {
    return (
      <span className="badge badge-muted" title="마감되었거나 취소된 공고라 지금은 참가할 수 없습니다.">
        <span className="dot" aria-hidden="true" />
        {ENDED_LABEL[status] ?? "종료된 공고"}
      </span>
    );
  }
  const meta = (compact ? ITEM_STATUS : JUDGMENT)[status] ?? { label: "확인 필요", tone: "warn" };
  return (
    <span className={`badge badge-${meta.tone}`}>
      <span className="dot" aria-hidden="true" />
      {meta.label}
    </span>
  );
}

/* ───────── 공고 상태 ───────── */
export const NOTICE_STATUS = {
  OPEN: { label: "진행 중", tone: "brand" },
  CORRECTED: { label: "정정 공고", tone: "warn" },
  CLOSED: { label: "마감", tone: "muted" },
  CANCELLED: { label: "취소", tone: "muted" },
};

export function NoticeStatusBadge({ status }) {
  if (status === "OPEN") return null;
  const meta = NOTICE_STATUS[status];
  return <span className={`badge badge-${meta.tone}`}>{meta.label}</span>;
}

export const isInactive = (notice) => notice.status === "CLOSED" || notice.status === "CANCELLED";

/* ───────── D-day ───────── */
export function Dday({ iso, inactive }) {
  const d = dday(iso);
  if (!d) return null;
  const level = inactive ? "over" : d.level;
  const label = inactive ? "종료" : d.label;
  return <span className={`dday dday-${level}`}>{label}</span>;
}

/* ───────── 금액 (한글 병기) ───────── */
export function Money({ value, showKorean = true }) {
  if (value === null || value === undefined) return <span className="muted">-</span>;
  return (
    <span className="num">
      {won(value)}
      {showKorean && <span className="muted small"> ({koreanWon(value)})</span>}
    </span>
  );
}

/* ───────── 용어 툴팁 ───────── */
export function Term({ children, term, tabbable = true }) {
  const key = term ?? children;
  const text = GLOSSARY[key];
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState(null);
  const id = useId();
  const triggerRef = useRef(null);
  const popRef = useRef(null);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    const onDown = (e) => !triggerRef.current?.contains(e.target) && setOpen(false);
    const onMove = () => setOpen(false); // 스크롤하면 닫아서 위치가 어긋나지 않게
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    window.addEventListener("scroll", onMove, true);
    window.addEventListener("resize", onMove);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
      window.removeEventListener("scroll", onMove, true);
      window.removeEventListener("resize", onMove);
    };
  }, [open]);

  // 화면(viewport) 기준으로 위치를 잡아, 표·카드 밖으로 나가도 잘리지 않고 가로 스크롤도 생기지 않게 한다.
  useLayoutEffect(() => {
    if (!open) {
      setPos(null);
      return;
    }
    if (!triggerRef.current || !popRef.current) return;
    const t =triggerRef.current.getBoundingClientRect();
    const p = popRef.current.getBoundingClientRect();
    const margin = 8;
    // 스크롤바를 뺀 실제 보이는 영역 기준
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    const left = Math.min(Math.max(margin, t.left), vw - p.width - margin);
    const below = t.bottom + 6;
    const top = below + p.height > vh - margin ? t.top - p.height - 6 : below;
    setPos({ left, top: Math.max(margin, top) });
  }, [open]);

  if (!text) return children;
  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className="term"
        tabIndex={tabbable ? undefined : -1}
        aria-describedby={open ? id : undefined}
        onMouseEnter={() => setOpen(true)}
        onMouseLeave={() => setOpen(false)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onClick={() => setOpen(true)}
      >
        {children}
      </button>
      {open &&
        createPortal(
          <span
            ref={popRef}
            role="tooltip"
            id={id}
            className="term-pop"
            style={pos ? { left: pos.left, top: pos.top } : { left: 0, top: 0, visibility: "hidden" }}
          >
            <strong>{key}</strong>
            {text}
          </span>,
          document.body,
        )}
    </>
  );
}

/* ───────── 원문 근거 ───────── */
export function Source({ text, label = "원문 근거" }) {
  if (!text) return <p className="source-missing">⚠ 원문 근거 확인 필요</p>;
  return (
    <details className="source">
      <summary>{label}</summary>
      <blockquote>{text}</blockquote>
    </details>
  );
}

/* ───────── 안내 박스 ───────── */
export function NoticeBox({ tone = "info", icon = "i", children }) {
  return (
    <div className={`notice-box notice-box-${tone}`} role={tone === "danger" ? "alert" : undefined}>
      <span className="icon" aria-hidden="true">{icon}</span>
      <div>{children}</div>
    </div>
  );
}

export function Disclaimer({ children }) {
  return (
    <NoticeBox tone="info" icon="i">
      {children ?? "추천 결과는 SRM 조건을 비교한 1차 검토입니다. 최종 참가 자격은 공고 원문과 담당자 확인이 우선합니다."}
    </NoticeBox>
  );
}

/* ───────── 빈 상태 ───────── */
export function EmptyState({ title, children, action }) {
  return (
    <div className="empty">
      <p className="empty-title">{title}</p>
      {children && <p className="small">{children}</p>}
      {action}
    </div>
  );
}

/* ───────── 관심공고 별표 ───────── */
export function Star({ active, onToggle, title }) {
  const [pop, setPop] = useState(false);
  return (
    <button
      type="button"
      className={`star${pop ? " pop" : ""}`}
      onAnimationEnd={() => setPop(false)}
      aria-pressed={active}
      aria-label={active ? `${title} 관심공고 해제` : `${title} 관심공고 추가`}
      title={active ? "관심공고 해제" : "관심공고 추가"}
      onClick={(e) => {
        e.preventDefault();
        e.stopPropagation();
        if (!active) setPop(true);
        onToggle();
      }}
    >
      {active ? "★" : "☆"}
    </button>
  );
}

/* ───────── 검색어 강조 ───────── */
export function Highlight({ text, query }) {
  const q = (query ?? "").trim();
  if (!q || !text) return text ?? null;
  const lower = text.toLowerCase();
  const key = q.toLowerCase();
  const out = [];
  let i = 0;
  for (let at = lower.indexOf(key); at !== -1; at = lower.indexOf(key, i)) {
    if (at > i) out.push(text.slice(i, at));
    out.push(<mark key={at} className="hl">{text.slice(at, at + q.length)}</mark>);
    i = at + q.length;
  }
  out.push(text.slice(i));
  return out;
}
