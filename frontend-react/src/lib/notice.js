/** 지금 기준으로 가장 가까운 다음 마감 (참가신청 마감 → 투찰 마감 순) */
export function nextDeadline(notice, now = new Date()) {
  const { applyEnd, bidEnd } = notice.schedule ?? {};
  if (applyEnd && new Date(applyEnd) > now) return { label: "참가신청 마감", iso: applyEnd };
  if (bidEnd && new Date(bidEnd) > now) return { label: "투찰 마감", iso: bidEnd };
  return { label: "투찰 마감", iso: bidEnd ?? applyEnd };
}

/**
 * 입찰 진행 단계 (날짜가 정해진 3단계만): 0 참가신청 마감, 1 투찰 마감, 2 개찰.
 * 적격심사 서류 기한은 통보일부터 'N일 이상'으로 정해져 날짜를 알 수 없어 단계에 넣지 않는다.
 */
export const STEPS = [
  { key: "applyEnd", label: "참가신청 마감", term: "입찰참가신청" },
  { key: "bidEnd", label: "투찰 마감", term: "투찰" },
  { key: "opening", label: "개찰", term: "개찰" },
];

/** 지금 단계 번호 (3이면 개찰까지 끝남, -1이면 취소) */
export function currentStep(notice, now = new Date()) {
  const s = notice.schedule ?? {};
  if (notice.status === "CANCELLED") return -1;
  const i = STEPS.findIndex((st) => s[st.key] && now <= new Date(s[st.key]));
  return i === -1 ? STEPS.length : i;
}
