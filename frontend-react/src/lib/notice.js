/** 지금 기준으로 가장 가까운 다음 마감 (참가신청 마감 → 투찰 마감 순) */
export function nextDeadline(notice, now = new Date()) {
  const { applyEnd, bidEnd } = notice.schedule ?? {};
  if (applyEnd && new Date(applyEnd) > now) return { label: "참가신청 마감", iso: applyEnd };
  if (bidEnd && new Date(bidEnd) > now) return { label: "투찰 마감", iso: bidEnd };
  return { label: "투찰 마감", iso: bidEnd ?? applyEnd };
}

/** 입찰 진행 단계: 0 참가신청, 1 투찰, 2 개찰, 3 적격심사, 4 계약 */
export const STEPS = ["입찰참가신청", "투찰", "개찰", "적격심사", "계약"];

export function currentStep(notice, now = new Date()) {
  const s = notice.schedule ?? {};
  if (notice.status === "CANCELLED") return -1;
  if (s.applyEnd && now <= new Date(s.applyEnd)) return 0;
  if (s.bidEnd && now <= new Date(s.bidEnd)) return 1;
  if (s.opening && now <= new Date(s.opening)) return 2;
  return 3;
}
