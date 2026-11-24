/**
 * SRM 투찰제한 1차 판정 (기능명세서 FUNC-010~014).
 * 백엔드 공고 상세의 조건(license·qualification·region·license_main)과 업체정보를 코드로 비교한다.
 *
 * 원칙
 * - 수동심사(Manual), 조회 실패, 결합 조건(AND/OR)을 알 수 없는 경우는 추측하지 않고 REVIEW.
 *   요구 면허가 여러 개일 때: API가 auto_eval_type(And/Or)을 주면 그대로 적용하고,
 *   주지 않으면(현재) '전부 보유'와 '하나도 없음'만 확정한다.
 *   (DB에는 license_auto_eval_type 등이 저장되어 있으나 API 응답에는 아직 없다)
 * - 지역 제한이 여러 개면(예: 대전·충남·충북·세종) 본점은 한 곳뿐이므로 그중 하나에 있으면 충족.
 * - 정상 조회로 제한이 없을 때만 NOT_APPLICABLE.
 * - 종합: 불일치가 하나라도 있으면 MISMATCH, 없고 불확실한 게 있으면 REVIEW, 나머지는 MATCH.
 */

const LABEL = { license: "일반 면허", qualification: "제한경쟁입찰자격", region: "지역 제한", licenseMain: "주력분야" };
const OLD_AREA_CODES = ["U", "W"]; // 과거 공고 지역코드. 전남광주(I)로 바꾸지 않고 확인 필요로 둔다

const named = (items) => items.map((i) => `${i.name} (${i.code})`).join(", ");

function base(key, cond) {
  return {
    key,
    label: LABEL[key],
    mode: cond?.auto_eval === "Manual" ? "MANUAL" : "AUTO",
    source: cond?.auto_eval ? `SRM 투찰제한 · ${cond.auto_eval === "Manual" ? "수동심사" : "자동심사"} (${cond.auto_eval})` : "SRM 투찰제한",
  };
}

/** 조회 전·실패·제한 없음처럼 비교 전에 결론이 나는 경우 */
function precheck(key, cond) {
  if (!cond) return { ...base(key, cond), required: "확인 필요", company: "-", status: "REVIEW", reason: "공고 상세를 불러오지 못했습니다." };
  if (cond.status === "확인 필요")
    return { ...base(key, cond), required: "확인 필요", company: "-", status: "REVIEW", reason: `SRM 조건을 확인하지 못했습니다 (${cond.reason ?? "사유 없음"}).` };
  if (cond.status === "제한 없음")
    return { ...base(key, cond), required: "제한 없음", company: "-", status: "NOT_APPLICABLE", reason: "정상 조회 결과 제한이 없습니다." };
  return null;
}

/** 면허·자격: 요구 코드 목록과 업체 보유 코드 비교 */
function codeItem(key, cond, held, { partialIsMismatch }) {
  const pre = precheck(key, cond);
  if (pre) return pre;
  const it = base(key, cond);
  const required = cond.items;
  const have = required.filter((r) => held.some((h) => h.code === r.code));
  const company = have.length ? named(have) : held.length ? "해당 없음" : "등록 없음";
  const out = { ...it, required: named(required), company };
  if (it.mode === "MANUAL")
    return { ...out, status: "REVIEW", reason: `수동심사 항목이라 결합 조건(AND/OR)을 추측하지 않습니다. 요구 ${required.length}개 중 ${have.length}개 보유.` };
  if (have.length === required.length)
    return { ...out, status: "MATCH", reason: required.length > 1 ? "요구 조건을 모두 보유하고 있습니다." : "요구 조건을 보유하고 있습니다." };
  const type = cond.auto_eval_type; // "And" | "Or" | undefined
  if (type === "Or" && have.length > 0)
    return { ...out, status: "MATCH", reason: `요구 ${required.length}개 중 하나 이상 보유 (SRM 결합 조건: OR).` };
  if (type === "And" && partialIsMismatch)
    return { ...out, status: "MISMATCH", reason: `요구 ${required.length}개를 모두 보유해야 합니다 (SRM 결합 조건: AND). ${have.length}개 보유.` };
  if (have.length === 0 && partialIsMismatch)
    return { ...out, status: "MISMATCH", reason: required.length > 1 ? "요구 면허를 하나도 보유하지 않았습니다." : "요구 면허를 보유하지 않았습니다." };
  if (have.length === 0)
    return { ...out, status: "REVIEW", reason: "요구 자격을 등록하지 않았습니다. 상위 자격으로 인정되는지 확인하세요." };
  return { ...out, status: "REVIEW", reason: `요구 ${required.length}개 중 ${have.length}개 보유 — 모두 필요한지(AND) 하나만 있으면 되는지(OR) 원문에서 확인하세요.` };
}

function regionItem(cond, company) {
  const pre = precheck("region", cond);
  if (pre) return pre;
  const it = base("region", cond);
  const rows = cond.items;
  const required = rows.map((r) => (r.sub_area_name ? `${r.name} (${r.area_code}) · ${r.sub_area_name}` : `${r.name} (${r.area_code}) 전체`)).join(", ");
  const out = { ...it, required, company: `${company.areaName} · ${company.subAreaName}` };
  if (rows.some((r) => OLD_AREA_CODES.includes(r.area_code)))
    return { ...out, status: "REVIEW", reason: "옛 지역코드가 쓰인 공고입니다. 공고 담당자에게 문의하세요." };
  const hits = rows.filter((r) => r.area_code === company.areaLimit && (!r.sub_area_code || r.sub_area_code === company.subAreaCode));
  if (hits.length === 0)
    return { ...out, status: "MISMATCH", reason: `${rows.map((r) => r.sub_area_name ?? r.name).join(", ")} 소재 업체로 제한된 공고입니다.` };
  if (rows.length > 1) return { ...out, status: "MATCH", reason: `허용 지역(${rows.map((r) => r.sub_area_name ?? r.name).join("·")}) 중 한 곳에 본점이 있습니다.` };
  return { ...out, status: "MATCH", reason: "본점 소재지가 지역 제한을 충족합니다." };
}

function licenseMainItem(cond) {
  const pre = precheck("licenseMain", cond);
  if (pre) return pre;
  return {
    ...base("licenseMain", cond),
    required: named(cond.items),
    company: "미수집",
    status: "REVIEW",
    reason: "입찰메이트는 업체 주력분야를 받지 않아 확인이 필요합니다.",
  };
}

/** 업체가 없으면 공고 조건만 보여 주고 판정(overall)은 null */
export function judge(notice, company) {
  const c = notice.conditions;
  if (!company) {
    const items = ["license", "qualification", "region", "licenseMain"].map((key) => {
      const cond = c[key];
      const pre = precheck(key, cond);
      if (pre) return pre;
      const required = key === "region" ? notice.regionLabel : named(cond.items);
      return { ...base(key, cond), required, company: "-", status: null, reason: "업체정보를 등록하면 비교합니다." };
    });
    return { overall: null, items, reviewReasons: [] };
  }

  const items = [
    codeItem("license", c.license, company.licenses, { partialIsMismatch: true }),
    codeItem("qualification", c.qualification, company.qualifications, { partialIsMismatch: false }),
    regionItem(c.region, company),
    licenseMainItem(c.licenseMain),
  ];
  const overall = items.some((i) => i.status === "MISMATCH") ? "MISMATCH" : items.some((i) => i.status === "REVIEW") ? "REVIEW" : "MATCH";
  const reviewReasons = items
    .filter((i) => i.status === "REVIEW")
    .map((i) => (i.mode === "MANUAL" ? `${i.label} 조건이 수동심사입니다` : i.key === "licenseMain" ? "주력분야 제한이 있습니다" : `${i.label} 조건을 확인해야 합니다`));
  return { overall, items, reviewReasons };
}
