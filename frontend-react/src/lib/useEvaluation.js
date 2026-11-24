import { useCallback, useEffect, useState } from "react";
import { fetchEvaluation, fetchUploadedCriteria } from "../api/client.js";

/**
 * 공고의 적격심사 배점·산식 (GET /api/notices/<번호>/evaluation).
 * 공용 배점이 없고 업체가 기준서를 직접 올렸으면 그 배점을 쓴다 (uploaded: 올린 파일 정보).
 * → { state: loading|done|error, evaluation, uploaded, error, reload }
 */
export function useEvaluation(noticeId, companyId) {
  const [result, setResult] = useState({ state: "loading", evaluation: null, uploaded: null, error: null });
  const [tick, setTick] = useState(0);
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    let alive = true;
    setResult((r) => ({ ...r, state: "loading" }));
    (async () => {
      const evaluation = await fetchEvaluation(noticeId);
      let uploaded = null;
      if (!evaluation.rules.length && companyId) {
        uploaded = await fetchUploadedCriteria(companyId, noticeId).catch(() => null);
      }
      const effective = uploaded?.evaluation?.rules?.length ? uploaded.evaluation : evaluation;
      if (alive) setResult({ state: "done", evaluation: effective, uploaded, error: null });
    })().catch((error) => alive && setResult({ state: "error", evaluation: null, uploaded: null, error }));
    return () => {
      alive = false;
    };
  }, [noticeId, companyId, tick]);

  return { ...result, reload };
}

/** 배점 표에 보여 줄 한 줄 설명 (산식 → 구간표 → 심사항목 순) */
export function ruleDetail(n) {
  const p = n.parameters ?? {};
  if (n.formula) return n.formula;
  if (p.rows) return p.rows.map((r) => `${r.label ?? r.grade ?? r.level ?? r["회사채"] ?? ""} ${r.score ?? ""}`.trim()).join(" · ");
  if (p.items) return p.items.map((i) => `${i.name} ${i.score}점`).join(" · ");
  return p["심사항목"] ?? p.condition ?? (p.pass_score != null ? `${p.pass_score}점 이상` : "");
}

export const CATEGORY_LABEL = {
  PRICE: "입찰가격",
  PERFORMANCE: "수행능력",
  MANAGEMENT: "경영상태",
  OFFICE: "숙지도",
  BONUS: "가점·감점",
  PENALTY: "결격·감점",
};
