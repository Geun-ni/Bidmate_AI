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

/**
 * 짧은 한글 금액 (화면 표시용): 1억 이상은 억 단위 소수 둘째 자리까지(셋째 자리에서 반올림),
 * 1억 미만은 만 원 단위로 반올림. 598,287,000 → "5.98억", 1,261,490,000 → "12.61억", 29,929,234 → "2,993만"
 */
export function shortKoreanWon(value) {
  if (value === null || value === undefined || Number.isNaN(value)) return "";
  const n = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (n >= 1e8 || Math.round(n / 1e4) >= 1e4) {
    const eok = Math.round((n / 1e8) * 100) / 100;
    return `${sign}${eok.toLocaleString("ko-KR", { maximumFractionDigits: 2 })}억`;
  }
  if (n >= 1e4) return `${sign}${wonFormatter.format(Math.round(n / 1e4))}만`;
  return `${sign}${wonFormatter.format(n)}원`;
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

/**
 * 마감까지 남은 시간 (분 단위까지): "1일 5시간 남음", "3시간 20분 남음", "12분 남음". 지났으면 null.
 * 공고 카드의 D-day는 달력 날짜 기준이라, 헷갈리지 않게 'D-'를 붙이지 않고 실제 남은 시간으로 쓴다.
 */
export function timeLeft(iso, now = new Date()) {
  const d = toDate(iso);
  if (!d) return null;
  const ms = d.getTime() - now.getTime();
  if (ms <= 0) return null;
  const min = Math.floor(ms / 60000);
  const days = Math.floor(min / 1440);
  const hours = Math.floor((min % 1440) / 60);
  const mins = min % 60;
  if (days > 0) return `${days}일${hours ? ` ${hours}시간` : ""} 남음`;
  if (hours > 0) return `${hours}시간${mins ? ` ${mins}분` : ""} 남음`;
  return `${Math.max(1, mins)}분 남음`;
}
