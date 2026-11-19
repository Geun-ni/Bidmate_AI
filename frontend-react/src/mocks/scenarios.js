import { MOCK_NOTICES } from "./notices.js";

/** 개발 모드에서 화면 상태를 바꿔 보기 위한 목업 시나리오 */
export const SCENARIOS = {
  default: { label: "기본", description: "정상 흐름" },
  missingDocs: { label: "문서 누락", description: "모든 공고에서 적격심사기준을 확보하지 못함" },
  noAValue: { label: "A값 없음", description: "공고문에서 A값을 추출하지 못함" },
  empty: { label: "추천 없음", description: "업체 조건에 맞는 공고가 없음" },
  error: { label: "불러오기 실패", description: "서버 오류" },
  noCompany: { label: "업체 미등록", description: "처음 방문한 사용자" },
};

const clone = (value) => structuredClone(value);

export function noticesFor(scenario) {
  const notices = clone(MOCK_NOTICES);
  switch (scenario) {
    case "missingDocs":
      return notices.map((n) => ({
        ...n,
        documents: { ...n.documents, criteria: { status: "MISSING", fileName: null } },
        criteria: { ...n.criteria, status: "MISSING" },
        attachments: n.attachments.filter((a) => a.kind !== "적격심사기준"),
        schedule: { ...n.schedule, qualifyDue: undefined },
        capability: null,
        priceFormula: null,
        adjustments: [],
        separatePenalty: null,
      }));
    case "noAValue":
      return notices.map((n) => (n.priceFormula ? { ...n, priceFormula: { ...n.priceFormula, aValue: null, aValueSource: null } } : n));
    case "empty":
      return [];
    default:
      return notices;
  }
}
