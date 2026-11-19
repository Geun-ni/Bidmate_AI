const wonFormatter = new Intl.NumberFormat("ko-KR");

/** 120000000 → "120,000,000원" */
export function won(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "-";
  return `${wonFormatter.format(value)}원`;
}

/** 463855784 → "4억 6,385만 5,784원" (금액 한글 병기) */
export function koreanWon(value) {
  if (!value && value !== 0) return "";
  const n = Math.floor(Math.abs(value));
  if (n === 0) return "0원";
  const eok = Math.floor(n / 1e8);
  const man = Math.floor((n % 1e8) / 1e4);
  const rest = n % 1e4;
  const parts = [];
  if (eok) parts.push(`${wonFormatter.format(eok)}억`);
  if (man) parts.push(`${wonFormatter.format(man)}만`);
  if (rest) parts.push(wonFormatter.format(rest));
  return `${parts.join(" ")}원`;
}

/** "463,855,784" 같은 입력 문자열 → 숫자 (빈 값은 null) */
export function parseMoney(text) {
  const digits = String(text ?? "").replace(/[^\d]/g, "");
  return digits ? Number(digits) : null;
}

export function formatMoneyInput(value) {
  return value === null || value === undefined ? "" : wonFormatter.format(value);
}

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

export function toDate(iso) {
  return iso ? new Date(iso) : null;
}

/** "2026-11-24T18:00" → "11.24(화) 18:00" */
export function shortDateTime(iso) {
  const d = toDate(iso);
  if (!d) return "미정";
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${mm}.${dd}(${WEEKDAYS[d.getDay()]}) ${hh}:${mi}`;
}

export function fullDateTime(iso) {
  const d = toDate(iso);
  if (!d) return "미정";
  return `${d.getFullYear()}.${shortDateTime(iso)}`;
}

function startOfDay(d) {
  return new Date(d.getFullYear(), d.getMonth(), d.getDate());
}

/**
 * 마감까지 남은 날짜.
 * level: normal | soon(3일 이내) | today | over
 */
export function dday(iso, now = new Date()) {
  const d = toDate(iso);
  if (!d) return null;
  if (d.getTime() < now.getTime()) return { label: "마감", level: "over", days: -1 };
  const days = Math.round((startOfDay(d) - startOfDay(now)) / 86400000);
  if (days === 0) return { label: "D-DAY", level: "today", days };
  return { label: `D-${days}`, level: days <= 3 ? "soon" : "normal", days };
}

export function sameDay(a, b) {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

export function round2(n) {
  return Math.round(n * 100) / 100;
}
