/**
 * 목업 공고 (가상 데이터).
 * 필드 구조는 docs/design/frontend-requirements.md 7장의 "백엔드 요청 데이터 형식" 초안이다.
 * 금액·배점 일부는 기능명세서의 검증 예시를 빌려왔지만, 공고 자체는 가상이다.
 */

const STANDARD_CRITERIA = {
  status: "OK",
  passScore: 95,
  capabilityCap: 30,
  items: [
    { name: "시공경험", points: 18, detail: "최근 5년 실적 14점 + 사업소 지리·지형·배전계통 숙지도 4점" },
    { name: "경영상태", points: 12, detail: "재무비율 또는 신용평가등급 중 선택" },
    { name: "입찰가격", points: 70, detail: "공고별 입찰가격 평가산식 적용" },
    { name: "가점·감점", points: null, detail: "일반신인도, 건설안전신인도, 부정당 시공경험 등" },
  ],
  source: "적격심사기준 제3조 · 수행능력 30점(시공경험 18, 경영상태 12), 입찰가격 70점, 종합평점 95점 이상인 자를 낙찰자로 결정한다.",
};

/**
 * 가점·감점 (FUNC-022). 등급·횟수가 있는 항목은 '해당'을 고르면 추가 입력을 받는다.
 * - select: 등급별 점수 / count: 건당 점수 × 횟수 (cap까지)
 * 등급·건당 점수는 예시값이며 실제로는 공고 기준서 추출값을 쓴다.
 */
const STANDARD_ADJUSTMENTS = [
  {
    id: "job", label: "일자리 창출 우수기업", points: 0.5, source: "적격심사기준 별표2 · 일반신인도 가점",
    detail: { kind: "select", label: "인증 등급", options: [{ label: "최우수", points: 0.5 }, { label: "우수", points: 0.3 }] },
  },
  {
    id: "safety", label: "건설재해·안전 관련 제재", points: -1, source: "적격심사기준 별표2 · 건설안전신인도 감점",
    detail: { kind: "count", label: "최근 1년 제재 건수", perUnit: -0.5, cap: -1, period: "공고일 기준 최근 1년" },
  },
  {
    id: "bribe", label: "직원 금품 제공 (부정당 시공경험)", points: -0.8, source: "적격심사기준 별표2 · 부정당 시공경험 감점",
    detail: { kind: "count", label: "최근 2년 적발 건수", perUnit: -0.8, cap: -1.6, period: "공고일 기준 최근 2년" },
  },
  {
    id: "outage", label: "일시정전 유발 (부정당 시공경험)", points: -0.4, source: "적격심사기준 별표2 · 부정당 시공경험 감점",
    detail: { kind: "count", label: "최근 1년 유발 횟수", perUnit: -0.4, cap: -1.2, period: "공고일 기준 최근 1년" },
  },
];

/**
 * 경영상태 평가표 (FUNC-019). 업종 평균은 기능명세서의 검증 예시값,
 * 구간별 점수는 화면 시연용 예시표다(실제 기준서 추출 후 교체).
 */
const MANAGEMENT = {
  verified: false,
  industryAvg: { debtRatio: 120.77, currentRatio: 142.57, source: "적격심사기준 별표1 · 2025년 전기공사업 평균 부채비율 120.77%, 유동비율 142.57%" },
  ratio: {
    max: { debt: 5.5, current: 5.5, period: 1 },
    // 업체 비율 ÷ 업종 평균 기준 구간
    debt: [[0.5, 5.5], [0.75, 5.0], [1.0, 4.5], [1.25, 4.0], [1.5, 3.5], [Infinity, 3.0]], // 이하일 때
    current: [[1.5, 5.5], [1.2, 5.0], [1.0, 4.5], [0.7, 4.0], [0.5, 3.5], [0, 3.0]], // 이상일 때
    period: [[5, 1.0], [3, 0.8], [0, 0.6]], // 영업기간(년) 이상일 때
  },
  credit: [
    { label: "AAA ~ AA-", points: 12 },
    { label: "A+ ~ A-", points: 11.5 },
    { label: "BBB+ ~ BBB-", points: 11 },
    { label: "BB+ ~ BB-", points: 10.5 },
    { label: "B+ 이하", points: 10 },
  ],
  source: "적격심사기준 별표1 · 경영상태 평가 (재무비율 또는 신용평가등급 중 택1)",
};

/**
 * 필요서류 (FUNC-024): 제출 목적, 필수/조건부, 제출 시점·방법까지 표시한다.
 */
const STANDARD_DOCS = [
  { name: "입찰참가신청서", stage: "APPLY", requirement: "REQUIRED", purpose: "입찰 참가 의사 표시", timing: "참가신청 마감 전", method: "SRM 전자 작성", source: "공고문 6항 · 입찰참가신청서 1부(SRM 전자 제출)" },
  { name: "입찰보증금 지급각서", stage: "APPLY", requirement: "REQUIRED", purpose: "입찰보증금(입찰금액의 5% 이상) 납부 약속", timing: "참가신청 마감 전", method: "SRM 전자 제출", source: "공고문 7항 · 입찰보증금은 지급각서로 갈음한다." },
  { name: "전기공사업 등록증 사본", stage: "APPLY", requirement: "REQUIRED", purpose: "면허 보유 확인", timing: "참가신청 마감 전", method: "SRM 첨부", source: "공고문 6항 · 전기공사업 등록증 사본 1부" },
  { name: "사업자등록증 사본", stage: "APPLY", requirement: "REQUIRED", purpose: "업체 기본정보 확인", timing: "참가신청 마감 전", method: "SRM 첨부", source: "공고문 6항 · 사업자등록증 사본 1부" },
  { name: "적격심사 신청서", stage: "QUALIFY", requirement: "REQUIRED", purpose: "적격심사 신청", timing: "대상자 통보일부터 5일 이내", method: "SRM 전자 제출", source: "적격심사기준 제5조 · 적격심사 대상자로 통보받은 날부터 5일 이내 제출" },
  { name: "시공실적 증명서 (최근 5년)", stage: "QUALIFY", requirement: "REQUIRED", purpose: "시공경험 점수 산정", timing: "대상자 통보일부터 5일 이내", method: "전기공사협회 발급본 제출", source: "적격심사기준 별표1 · 전기공사협회 발급 실적증명서" },
  { name: "신용평가등급 확인서 또는 재무제표", stage: "QUALIFY", requirement: "REQUIRED", purpose: "경영상태 점수 산정", timing: "대상자 통보일부터 5일 이내", method: "평가기관 발급본 또는 재무제표 제출", source: "적격심사기준 별표1 · 경영상태 평가 서류" },
  { name: "기술자 보유 현황", stage: "QUALIFY", requirement: "REQUIRED", purpose: "기술자 보유 기준 충족 확인 (미달 시 10점 감점)", timing: "대상자 통보일부터 5일 이내", method: "협회 경력증명 첨부", source: "적격심사기준 별표1 · 기술자 보유 미달 시 10점 감점" },
  { name: "공동수급 협정서", stage: "CONDITIONAL", requirement: "CONDITIONAL", condition: "공동도급으로 참가하는 경우", purpose: "공동수급체 구성 확인", timing: "참가신청 마감 전", method: "SRM 첨부", source: "공고문 8항 · 공동수급 시 협정서 제출" },
  { name: "일자리 창출 우수기업 확인서", stage: "CONDITIONAL", requirement: "CONDITIONAL", condition: "가점을 신청하는 경우", purpose: "일반신인도 가점 증빙", timing: "대상자 통보일부터 5일 이내", method: null, source: null },
];

const LICENSE_A06 = {
  key: "license",
  label: "일반 면허",
  required: "전기공사업 (A06)",
  company: "전기공사업 (A06)",
  mode: "AUTO",
  status: "MATCH",
  reason: "요구 면허를 보유하고 있습니다.",
  source: "SRM 투찰제한 · licenseAutoEvalMethod=Auto, licenseQualificationCode=A06",
};

const QUALIFICATION_NONE = {
  key: "qualification",
  label: "제한경쟁입찰자격",
  required: "제한 없음",
  company: "-",
  mode: "AUTO",
  status: "NOT_APPLICABLE",
  reason: "정상 조회 결과 자격 제한이 없습니다.",
  source: "SRM 투찰제한 · findQualifiedCodeData 빈 응답",
};

const MAIN_NONE = {
  key: "licenseMain",
  label: "주력분야",
  required: "제한 없음",
  company: "-",
  mode: "AUTO",
  status: "NOT_APPLICABLE",
  reason: "정상 조회 결과 주력분야 제한이 없습니다.",
  source: "SRM 투찰제한 · findLicenseMainCodeData 빈 응답",
};

function region(required, status = "MATCH", reason = "본점 소재지가 지역 제한을 충족합니다.") {
  return {
    key: "region",
    label: "지역 제한",
    required,
    company: "강원 · 춘천시",
    mode: "AUTO",
    status,
    reason,
    source: `SRM 투찰제한 · regionLimitAutoEvalMethod=Auto, areaCode=F`,
  };
}

const PRICE_FORMULA = {
  maxPoints: 70,
  minPoints: 2,
  k: 4,
  baseRate: 0.88,
  text: "70 − 4 × | (88/100 − (입찰가격 − A값) / (예정가격 − A값)) × 100 |",
  rounding: "소수점 셋째 자리에서 반올림",
  source: "적격심사기준 별표3 · 입찰가격 평점 산식 (예시)",
};

const RAW_NOTICES = [
  {
    id: "E012600812",
    title: "춘천지사 관내 배전선로 개량공사 (1차)",
    issuer: "한국전력공사 강원본부 춘천지사",
    category: "공사",
    status: "OPEN",
    estimatedPrice: 421687077,
    basePrice: 463855784,
    regionLabel: "강원 전체",
    schedule: {
      applyStart: "2026-11-17T09:00",
      applyEnd: "2026-11-26T18:00",
      bidStart: "2026-11-24T10:00",
      bidEnd: "2026-11-27T14:00",
      opening: "2026-11-27T15:00",
    },
    judgment: {
      overall: "MATCH",
      items: [LICENSE_A06, QUALIFICATION_NONE, region("강원 (F) 전체"), MAIN_NONE],
    },
    reviewReasons: [],
    documents: {
      notice: { status: "OK", fileName: "입찰공고문_E012600812.hwpx" },
      criteria: { status: "OK", fileName: "배전공사_적격심사기준(3억~10억).hwp" },
    },
    extraConditions: [
      {
        text: "배전활선·무정전 동종실적: 최근 10년 이내 1건, VAT 제외 금액이 추정가격의 1/3 이상",
        verify: "AFTER_OPENING",
        source: "공고문 3항 2호 · 공고일 전일 기준 최근 10년 이내 협회 공시 동종실적 1건 이상",
      },
      { text: "대기업 전기공사업자 참여 제한", verify: "SELF", source: "공고문 3항 4호 · 대기업 및 계열사 참여 제한" },
      { text: "공동도급 해당 없음", verify: "SELF", source: "공고문 4항 · 공동수급 불허" },
      { text: "부정당업자 제재 기간 중인 업체 참가 불가", verify: "SOURCE", source: null },
    ],
    criteria: STANDARD_CRITERIA,
    capability: {
      assessedAmount: 463855784,
      assessedSource: "적격심사기준 별표1 · 해당공사 공사원가 예정가격 산출 기초조서상의 사정액 463,855,784원(VAT 포함)",
      offices: { own: "춘천지사", adjacent: ["홍천지사", "인제지사", "화천지사", "양구지사"] },
    },
    priceFormula: { ...PRICE_FORMULA, aValue: 31250000, aValueSource: "공고문 7항 · A값(국민연금·건강보험료 등) 31,250,000원" },
    adjustments: STANDARD_ADJUSTMENTS,
    separatePenalty: { id: "engineer", label: "기술자 보유 기준 미달", points: -10, source: "적격심사기준 별표1 · 기술자 보유 미달 시 10점 감점" },
    requiredDocs: STANDARD_DOCS,
  },
  {
    id: "E012600845",
    title: "홍천지사 무정전 공법 배전공사",
    issuer: "한국전력공사 강원본부 홍천지사",
    category: "공사",
    status: "OPEN",
    estimatedPrice: 286400000,
    basePrice: 315040000,
    regionLabel: "강원 전체",
    schedule: {
      applyStart: "2026-11-16T09:00",
      applyEnd: "2026-11-21T18:00",
      bidStart: "2026-11-20T10:00",
      bidEnd: "2026-11-22T14:00",
      opening: "2026-11-22T15:00",
    },
    judgment: {
      overall: "REVIEW",
      items: [
        {
          ...LICENSE_A06,
          required: "전기공사업 (A06), 전기공사업(무정전시공) (F50)",
          mode: "MANUAL",
          status: "REVIEW",
          reason: "수동심사 항목이라 두 면허의 결합 조건(AND/OR)을 추측하지 않습니다.",
          source: "SRM 투찰제한 · licenseAutoEvalMethod=Manual",
        },
        QUALIFICATION_NONE,
        region("강원 (F) 전체"),
        {
          key: "licenseMain",
          label: "주력분야",
          required: "배전활선",
          company: "미수집",
          mode: "AUTO",
          status: "REVIEW",
          reason: "입찰메이트는 업체 주력분야를 받지 않아 확인이 필요합니다.",
          source: "SRM 투찰제한 · findLicenseMainCodeData 1건",
        },
      ],
    },
    reviewReasons: ["면허 조건이 수동심사입니다", "주력분야 제한이 있습니다"],
    documents: {
      notice: { status: "OK", fileName: "입찰공고문_E012600845.hwpx" },
      criteria: { status: "OK", fileName: "배전공사_적격심사기준(1억~3억).hwp" },
    },
    extraConditions: [
      { text: "무정전 공법 시공 가능 장비 보유", verify: "SELF", source: "공고문 3항 3호 · 무정전 공법 장비 보유 업체" },
    ],
    criteria: STANDARD_CRITERIA,
    capability: {
      assessedAmount: 315040000,
      assessedSource: "적격심사기준 별표1 · 사정액 315,040,000원(VAT 포함)",
      offices: { own: "홍천지사", adjacent: ["춘천지사", "횡성지사", "인제지사"] },
    },
    priceFormula: { ...PRICE_FORMULA, aValue: 21800000, aValueSource: "공고문 7항 · A값 21,800,000원" },
    adjustments: STANDARD_ADJUSTMENTS,
    separatePenalty: { id: "engineer", label: "기술자 보유 기준 미달", points: -10, source: "적격심사기준 별표1" },
    requiredDocs: STANDARD_DOCS,
  },
  {
    id: "E012600860",
    title: "영월지사 지중배전 신설공사",
    issuer: "한국전력공사 강원본부 영월지사",
    category: "공사",
    status: "OPEN",
    estimatedPrice: 612300000,
    basePrice: 673530000,
    regionLabel: "강원 · 영월군",
    schedule: {
      applyStart: "2026-11-19T09:00",
      applyEnd: "2026-12-03T18:00",
      bidStart: "2026-12-02T10:00",
      bidEnd: "2026-12-04T14:00",
      opening: "2026-12-04T15:00",
    },
    judgment: {
      overall: "MISMATCH",
      items: [
        LICENSE_A06,
        QUALIFICATION_NONE,
        region("강원 (F) · 영월군", "MISMATCH", "영월군 소재 업체로 제한된 공고입니다."),
        MAIN_NONE,
      ],
    },
    reviewReasons: [],
    documents: {
      notice: { status: "OK", fileName: "입찰공고문_E012600860.hwpx" },
      criteria: { status: "OK", fileName: "지중배전_적격심사기준.hwp" },
    },
    extraConditions: [],
    criteria: STANDARD_CRITERIA,
    capability: { assessedAmount: 673530000, assessedSource: "적격심사기준 별표1 · 사정액", offices: { own: "영월지사", adjacent: ["정선지사", "평창지사"] } },
    priceFormula: { ...PRICE_FORMULA, aValue: 40100000, aValueSource: "공고문 7항 · A값" },
    adjustments: STANDARD_ADJUSTMENTS,
    separatePenalty: { id: "engineer", label: "기술자 보유 기준 미달", points: -10, source: "적격심사기준 별표1" },
    requiredDocs: STANDARD_DOCS,
  },
  {
    id: "E012600871",
    title: "원주지사 주상변압기 교체공사",
    issuer: "한국전력공사 강원본부 원주지사",
    category: "공사",
    status: "OPEN",
    estimatedPrice: 198500000,
    basePrice: 218350000,
    regionLabel: "강원 전체",
    schedule: {
      applyStart: "2026-11-18T09:00",
      applyEnd: "2026-12-01T18:00",
      bidStart: "2026-11-30T10:00",
      bidEnd: "2026-12-02T14:00",
      opening: "2026-12-02T15:00",
    },
    judgment: { overall: "MATCH", items: [LICENSE_A06, QUALIFICATION_NONE, region("강원 (F) 전체"), MAIN_NONE] },
    reviewReasons: [],
    documents: {
      notice: { status: "OK", fileName: "입찰공고문_E012600871.pdf" },
      criteria: { status: "OK", fileName: "배전공사_적격심사기준(1억~3억).hwp" },
    },
    extraConditions: [
      { text: "변압기 교체 동종실적 1건 이상", verify: "AFTER_OPENING", source: "공고문 3항 2호 · 최근 5년 이내 동종실적" },
    ],
    criteria: STANDARD_CRITERIA,
    capability: { assessedAmount: 218350000, assessedSource: "적격심사기준 별표1 · 사정액 218,350,000원", offices: { own: "원주지사", adjacent: ["횡성지사", "영월지사"] } },
    // A값을 공고문에서 찾지 못한 사례 → 점수 계산 보류
    priceFormula: { ...PRICE_FORMULA, aValue: null, aValueSource: null },
    adjustments: STANDARD_ADJUSTMENTS,
    separatePenalty: { id: "engineer", label: "기술자 보유 기준 미달", points: -10, source: "적격심사기준 별표1" },
    requiredDocs: STANDARD_DOCS.slice(0, 7),
  },
  {
    id: "E012600833",
    title: "강릉지사 배전선로 보강공사 (정정)",
    issuer: "한국전력공사 강원본부 강릉지사",
    category: "공사",
    status: "CORRECTED",
    correction: "투찰 마감이 11.25 → 11.28로 변경되었습니다.",
    estimatedPrice: 354000000,
    basePrice: 389400000,
    regionLabel: "강원 전체",
    schedule: {
      applyStart: "2026-11-12T09:00",
      applyEnd: "2026-11-27T18:00",
      bidStart: "2026-11-26T10:00",
      bidEnd: "2026-11-28T14:00",
      opening: "2026-11-28T15:00",
    },
    judgment: { overall: "MATCH", items: [LICENSE_A06, QUALIFICATION_NONE, region("강원 (F) 전체"), MAIN_NONE] },
    reviewReasons: [],
    documents: {
      notice: { status: "OK", fileName: "정정공고문_E012600833.hwpx" },
      // 공식 API 첨부 5개 제한으로 적격심사기준이 누락된 사례
      criteria: { status: "MISSING", fileName: null },
    },
    extraConditions: [],
    criteria: { ...STANDARD_CRITERIA, status: "MISSING" },
    capability: null,
    priceFormula: null,
    adjustments: [],
    separatePenalty: null,
    requiredDocs: STANDARD_DOCS.slice(0, 3),
  },
  {
    id: "E012600731",
    title: "당림리 배전선로 무정전 공사",
    issuer: "한국전력공사 강원본부 춘천지사",
    category: "공사",
    status: "CLOSED",
    estimatedPrice: 421687077,
    basePrice: 463855784,
    regionLabel: "강원 전체",
    schedule: {
      applyStart: "2026-11-03T09:00",
      applyEnd: "2026-11-11T18:00",
      bidStart: "2026-11-10T10:00",
      bidEnd: "2026-11-12T14:00",
      opening: "2026-11-12T15:00",
    },
    judgment: { overall: "MATCH", items: [LICENSE_A06, QUALIFICATION_NONE, region("강원 (F) 전체"), MAIN_NONE] },
    reviewReasons: [],
    documents: {
      notice: { status: "OK", fileName: "입찰공고문_E012600731.hwpx" },
      criteria: { status: "OK", fileName: "배전공사_적격심사기준(3억~10억).hwp" },
    },
    extraConditions: [],
    criteria: STANDARD_CRITERIA,
    capability: { assessedAmount: 463855784, assessedSource: "적격심사기준 별표1 · 사정액", offices: { own: "춘천지사", adjacent: ["홍천지사", "인제지사", "화천지사", "양구지사"] } },
    priceFormula: { ...PRICE_FORMULA, aValue: 31250000, aValueSource: "공고문 7항 · A값" },
    adjustments: STANDARD_ADJUSTMENTS,
    separatePenalty: { id: "engineer", label: "기술자 보유 기준 미달", points: -10, source: "적격심사기준 별표1" },
    requiredDocs: STANDARD_DOCS,
  },
  {
    id: "E012600808",
    title: "태백지사 배전설비 정비공사",
    issuer: "한국전력공사 강원본부 태백지사",
    category: "공사",
    status: "CANCELLED",
    correction: "발주처 사정으로 공고가 취소되었습니다.",
    estimatedPrice: 152000000,
    basePrice: 167200000,
    regionLabel: "강원 전체",
    schedule: { applyStart: "2026-11-14T09:00", applyEnd: "2026-11-25T18:00", bidStart: null, bidEnd: "2026-11-26T14:00", opening: null },
    judgment: { overall: "MATCH", items: [LICENSE_A06, QUALIFICATION_NONE, region("강원 (F) 전체"), MAIN_NONE] },
    reviewReasons: [],
    documents: { notice: { status: "OK", fileName: "취소공고문_E012600808.hwpx" }, criteria: { status: "MISSING", fileName: null } },
    extraConditions: [],
    criteria: { ...STANDARD_CRITERIA, status: "MISSING" },
    capability: null,
    priceFormula: null,
    adjustments: [],
    separatePenalty: null,
    requiredDocs: [],
  },
];

/* ───────── 공통 보강: 일정 확정 여부, 적격심사 서류 기한, 첨부파일, 경영상태 평가표 ───────── */
const addDays = (iso, days) => {
  const d = new Date(iso);
  d.setDate(d.getDate() + days);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T18:00`;
};

function enrich(n) {
  const criteriaOk = n.documents.criteria.status === "OK";
  const schedule = { ...n.schedule };
  // FUNC-016: 적격심사 서류 제출기한은 "대상자 통보일 + 5일"이라 개찰 전에는 확정할 수 없다 → 개찰일 기준 추정, 미확정
  if (criteriaOk && schedule.opening) schedule.qualifyDue = addDays(schedule.opening, 5);
  const corrected = n.status === "CORRECTED";
  const scheduleMeta = {
    applyEnd: { confirmed: true, source: corrected ? "정정공고 일정" : "SRM 공고 일정" },
    bidEnd: { confirmed: true, source: corrected ? "정정공고 일정 (11.25 → 11.28 변경)" : "SRM 공고 일정" },
    opening: { confirmed: true, source: "SRM 공고 일정" },
    qualifyDue: { confirmed: false, source: "적격심사기준 제5조 · 대상자 통보일부터 5일 이내 (통보일 미정 → 개찰일 기준 추정)" },
  };
  // FUNC-018: 원본 첨부파일 (목업 파일은 public/mock-docs/)
  const attachments = [];
  if (n.documents.notice.status === "OK")
    attachments.push({ kind: "공고문", name: n.documents.notice.fileName, url: "/mock-docs/notice-sample.txt", size: "84 KB", origin: "공식 Open API" });
  if (criteriaOk)
    attachments.push({ kind: "적격심사기준", name: n.documents.criteria.fileName, url: "/mock-docs/criteria-sample.txt", size: "212 KB", origin: n.id === "E012600812" ? "SRM 추가 조회" : "공식 Open API" });
  attachments.push({ kind: "기타", name: "설계도면_및_내역서.zip", url: null, size: "3.1 MB", origin: "SRM (미리보기 미지원)" });

  return {
    ...n,
    schedule,
    scheduleMeta,
    attachments,
    capability: n.capability ? { ...n.capability, management: MANAGEMENT } : null,
  };
}

export const MOCK_NOTICES = RAW_NOTICES.map(enrich);
