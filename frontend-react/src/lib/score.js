import { round2 } from "./format.js";

/**
 * 입찰가격 점수 (회의 1안: 공고 산식 자동 계산).
 * 사용자 입력: 입찰가격, 예정가격 / 공고문 자동 추출: A값.
 *
 * 초안의 산식은 목업 공고에 들어 있는 예시 산식이다.
 *   점수 = 배점 - k × | (기준율 - (입찰가격 - A) / (예정가격 - A)) × 100 |
 * 실제 서비스에서는 백엔드·AI가 공고 적격심사기준에서 추출한 산식을 그대로 적용해야 한다.
 */
export function calcPriceScore({ bidPrice, plannedPrice, formula }) {
  if (!formula) return { state: "blocked", reason: "적격심사기준에서 입찰가격 산식을 찾지 못했습니다." };
  if (formula.aValue === null || formula.aValue === undefined) {
    return { state: "blocked", reason: "공고문에서 A값을 추출하지 못했습니다. 임의 값으로 계산하지 않습니다." };
  }
  if (!bidPrice || !plannedPrice) return { state: "input", reason: "입찰가격과 예정가격을 입력하세요." };
  const A = formula.aValue;
  if (plannedPrice <= A) return { state: "error", reason: "예정가격이 A값보다 커야 계산할 수 있습니다." };
  if (bidPrice <= A) return { state: "error", reason: "입찰가격이 A값보다 커야 계산할 수 있습니다." };

  const ratio = (bidPrice - A) / (plannedPrice - A);
  const raw = formula.maxPoints - formula.k * Math.abs((formula.baseRate - ratio) * 100);
  const min = formula.minPoints ?? 0;
  const score = round2(Math.min(formula.maxPoints, Math.max(min, raw)));
  return { state: "done", score, ratio, bidRate: bidPrice / plannedPrice, atMin: raw <= min };
}

/** 시공경험(실적) 점수: (실적 누계액 / 사정액) × 배점, 상한 = 배점 */
export function calcExperienceScore({ performance, assessedAmount, maxPoints }) {
  if (performance === null || performance === undefined) return null;
  if (!assessedAmount) return null;
  return round2(Math.min(maxPoints, (performance / assessedAmount) * maxPoints));
}

/**
 * 예상 총점. 하나라도 미산정·확인 필요면 총점을 확정하지 않는다.
 * 수행능력(시공경험 + 경영상태 + 가점/감점)은 capabilityCap(30점)을 넘지 않는다.
 * 기술자 보유 미달 등 별도 감점(separatePenalty)은 상한 적용 후 뺀다.
 */
export function calcTotal({ parts, adjustments, separatePenalty, price, capabilityCap }) {
  const pending = [];
  for (const p of parts) if (p.value === null || p.value === undefined) pending.push(p.label);
  for (const a of adjustments) if (a.answer === "unknown" || !a.answer) pending.push(a.label);
  if (separatePenalty.answer !== "yes" && separatePenalty.answer !== "no") pending.push(separatePenalty.label);
  if (price.state !== "done") pending.push("입찰가격 점수");

  const capabilityRaw =
    parts.reduce((sum, p) => sum + (p.value ?? 0), 0) +
    adjustments.reduce((sum, a) => sum + (a.answer === "yes" ? a.points : 0), 0);
  const capability = round2(Math.max(0, Math.min(capabilityCap, capabilityRaw)));

  const penalty = separatePenalty.answer === "yes" ? separatePenalty.points : 0;
  const priceNow = price.state === "done" ? price.score : 0;

  if (pending.length) {
    // 산정 보류여도 "지금까지 산정된 점수"와 "남은 항목을 모두 최고로 받았을 때의 최대 점수"는 보여준다.
    // 통과·미달은 단정하지 않는다 (FUNC-023).
    const bestCapabilityRaw =
      parts.reduce((sum, p) => sum + (p.value ?? p.max ?? 0), 0) +
      adjustments.reduce((sum, a) => {
        if (a.answer === "yes") return sum + a.points;
        if (a.answer === "unknown" || !a.answer) return sum + Math.max(0, a.bestPoints ?? 0); // 가점은 받는다고, 감점은 없다고 가정
        return sum;
      }, 0);
    const bestCapability = Math.max(0, Math.min(capabilityCap, bestCapabilityRaw));
    const bestPrice = price.state === "done" ? price.score : price.max ?? 0;
    return {
      state: "pending",
      pending,
      capability,
      partial: round2(capability + priceNow + penalty),
      maxPossible: round2(bestCapability + bestPrice + penalty),
    };
  }
  const total = round2(capability + priceNow + penalty);
  return { state: "done", total, capability, penalty };
}

/**
 * 경영상태 — 재무비율 방식 (FUNC-019).
 * 부채비율은 업종 평균 대비 낮을수록, 유동비율은 높을수록 점수가 높다. 영업기간은 연수 구간.
 * 세 값 중 하나라도 비어 있으면 미산정(null).
 */
export function calcManagementRatio({ debt, current, years }, mgmt) {
  if ([debt, current, years].some((v) => v === null || v === undefined || v === "" || Number.isNaN(Number(v)))) return null;
  const debtRel = Number(debt) / mgmt.industryAvg.debtRatio;
  const currentRel = Number(current) / mgmt.industryAvg.currentRatio;
  const debtPts = mgmt.ratio.debt.find(([limit]) => debtRel <= limit)[1];
  const currentPts = mgmt.ratio.current.find(([limit]) => currentRel >= limit)[1];
  const periodPts = mgmt.ratio.period.find(([limit]) => Number(years) >= limit)[1];
  return { debtPts, currentPts, periodPts, debtRel, currentRel, total: round2(debtPts + currentPts + periodPts) };
}

/**
 * 가점·감점 한 항목의 점수 (FUNC-022).
 * '해당'인데 등급·횟수를 입력하지 않았으면 미산정으로 본다.
 */
export function calcAdjustment(item, answer, detailValue) {
  if (answer !== "yes") return { answer, points: 0 };
  const d = item.detail;
  if (!d) return { answer, points: item.points };
  if (d.kind === "select") {
    const opt = d.options.find((o) => o.label === detailValue);
    return opt ? { answer, points: opt.points } : { answer: "unknown", points: 0, missing: d.label };
  }
  const count = Number(detailValue);
  if (detailValue === "" || detailValue === undefined || !Number.isFinite(count) || count < 1) return { answer: "unknown", points: 0, missing: d.label };
  return { answer, points: round2(Math.max(d.cap, d.perUnit * count)) };
}
