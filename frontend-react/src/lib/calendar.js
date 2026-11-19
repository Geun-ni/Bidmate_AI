/** 공고 일정 → 달력 이벤트 목록 */
export const EVENT_TYPES = {
  applyEnd: { label: "참가신청 마감", short: "신청마감", tone: "warn" },
  bidEnd: { label: "투찰 마감", short: "투찰마감", tone: "danger" },
  opening: { label: "개찰", short: "개찰", tone: "brand" },
  qualifyDue: { label: "적격심사 서류 제출기한", short: "심사서류", tone: "accent" },
};

/** FUNC-016: 일정마다 확정 여부와 출처를 함께 넘긴다 (정보가 없으면 미확정으로 본다). */
export function noticeEvents(notice) {
  return Object.entries(EVENT_TYPES)
    .filter(([key]) => notice.schedule?.[key])
    .map(([key, meta]) => ({
      key: `${notice.id}-${key}`,
      type: key,
      ...meta,
      date: new Date(notice.schedule[key]),
      confirmed: notice.scheduleMeta?.[key]?.confirmed ?? false,
      source: notice.scheduleMeta?.[key]?.source ?? "출처 확인 필요",
      notice,
    }));
}

function icsDate(d) {
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}T${pad(d.getHours())}${pad(d.getMinutes())}00`;
}

function icsEscape(text) {
  return String(text).replace(/[\\;,]/g, (m) => `\\${m}`).replace(/\n/g, "\\n");
}

/** 공고 일정을 .ics 파일로 내려받는다 (휴대폰·아웃룩 캘린더에 추가). */
export function downloadIcs(notice) {
  const lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Bidmate//KO", "CALSCALE:GREGORIAN"];
  for (const ev of noticeEvents(notice)) {
    const end = new Date(ev.date.getTime() + 30 * 60000);
    lines.push(
      "BEGIN:VEVENT",
      `UID:${ev.key}@bidmate`,
      `DTSTART;TZID=Asia/Seoul:${icsDate(ev.date)}`,
      `DTEND;TZID=Asia/Seoul:${icsDate(end)}`,
      `SUMMARY:${icsEscape(`[${ev.label}${ev.confirmed ? "" : " · 미확정"}] ${notice.title}`)}`,
      `DESCRIPTION:${icsEscape(`공고번호 ${notice.id}\n${notice.issuer}\n일정은 공고 원문과 SRM에서 다시 확인하세요.`)}`,
      "BEGIN:VALARM",
      "TRIGGER:-P1D",
      "ACTION:DISPLAY",
      `DESCRIPTION:${icsEscape(`내일 ${ev.label}`)}`,
      "END:VALARM",
      "END:VEVENT",
    );
  }
  lines.push("END:VCALENDAR");
  const blob = new Blob([lines.join("\r\n")], { type: "text/calendar;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${notice.id}.ics`;
  a.click();
  URL.revokeObjectURL(url);
}

/** 구글 캘린더 일정 추가 링크 (투찰 마감 기준) */
export function googleCalendarUrl(notice) {
  const date = notice.schedule?.bidEnd;
  if (!date) return null;
  const d = new Date(date);
  const end = new Date(d.getTime() + 30 * 60000);
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: `[투찰 마감] ${notice.title}`,
    dates: `${icsDate(d)}/${icsDate(end)}`,
    ctz: "Asia/Seoul",
    details: `공고번호 ${notice.id}`,
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}
