/**
 * 백엔드 응답 → 화면에서 쓰는 모양.
 * 화면 컴포넌트는 이 파일이 만든 필드만 쓰므로, API 형식이 바뀌면 여기만 고치면 된다.
 */

/** "2026-11-25 18:00:00" → "2026-11-25T18:00:00" (Safari는 공백 형식을 못 읽는다). ISO는 그대로 */
export function isoOf(value) {
  if (!value) return null;
  const s = String(value);
  return /^\d{4}-\d{2}-\d{2} \d/.test(s) ? s.replace(" ", "T") : s;
}

const KIND = { NOTICE: "공고문", CRITERIA: "적격심사기준" };
const ORIGIN = { OPEN_API: "공식 Open API", SRM_FALLBACK: "SRM 추가 조회" };

function fileSize(bytes) {
  if (!bytes && bytes !== 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

/** 공고문·적격심사기준 확보 상태: 받은 파일이 있으면 OK, 수집 전이면 PENDING, 못 찾았으면 MISSING */
function docState(found, attachments, kind) {
  const file = attachments.find((a) => a.doc_kind === kind && a.download_status === "SUCCESS");
  if (file) return { status: "OK", fileName: file.filename };
  return { status: found === null || found === undefined ? "PENDING" : "MISSING", fileName: null };
}

/** 지역 제한 조건 → "강원 전체", "경기 · 수원시", 제한이 없으면 "지역 제한 없음" */
export function regionLabel(region) {
  if (!region) return "지역 확인 필요";
  if (region.status === "제한 없음") return "지역 제한 없음";
  if (region.status !== "수집됨") return "지역 확인 필요";
  return region.items.map((i) => (i.sub_area_name ? `${i.name} · ${i.sub_area_name}` : `${i.name} 전체`)).join(", ");
}

/* ───────── 공사 / 용역 ─────────
 * DB(bid_notice.item_type)에는 Construction / Service 가 있지만 API 응답에 아직 없다.
 * API가 item_type 을 주면 그 값을 쓰고, 없으면 공고명으로 추정한다
 * (공고명에 용역·감리·설계·측량 등이 있으면 용역. 진행 중 141건에서 면허 종류 기준 추정과 140건 일치).
 */
const SERVICE_WORDS = /용역|감리|설계|측량|조사|진단|점검|컨설팅|평가/;
export const NOTICE_TYPE = { CONSTRUCTION: "공사", SERVICE: "용역" };

function noticeType(row, d) {
  const raw = row.item_type ?? d.item_type;
  if (raw === "Construction") return { type: "CONSTRUCTION", typeEstimated: false };
  if (raw === "Service") return { type: "SERVICE", typeEstimated: false };
  return { type: SERVICE_WORDS.test(row.title ?? "") ? "SERVICE" : "CONSTRUCTION", typeEstimated: true };
}

/* ───────── 공고 지역 (광역) ─────────
 * API에 공사 현장 위치가 없어 이렇게 정한다.
 * 1) 지역 제한이 있으면 그 광역 (정확)  2) 없으면 발주 지역본부 이름으로 추정 (예: 경기본부 → 경기)
 * 건설본부·상생조달처처럼 여러 지역을 맡는 부서나 발주부서가 비어 있으면 '미분류'.
 */
const HQ_AREAS = [
  [/^남서울본부|^서울본부/, ["S"]],
  [/^인천본부/, ["H"]],
  [/^경기북부본부|^경기본부/, ["G"]],
  [/^강원본부/, ["F"]],
  [/^충북본부/, ["C"]],
  [/^대전세종충남본부/, ["D", "L", "R"]],
  [/^전북본부/, ["J"]],
  [/^광주전남본부/, ["I"]],
  [/^대구본부/, ["T"]],
  [/^경북본부/, ["K"]],
  [/^부산울산본부/, ["B", "Z"]],
  [/^경남본부/, ["M"]],
  [/^제주본부/, ["E"]],
];

function noticeAreas(region, placeName) {
  if (region?.status === "수집됨" && region.items.length) return { areas: [...new Set(region.items.map((i) => i.area_code))], areaSource: "지역 제한" };
  const hit = placeName ? HQ_AREAS.find(([re]) => re.test(placeName)) : null;
  return hit ? { areas: hit[1], areaSource: "발주 본부" } : { areas: [], areaSource: null };
}

/**
 * row: 목록 행(/api/notices) 또는 상세, detail: 상세(/api/notices/<번호>). 상세를 못 받았으면 null.
 */
export function toNotice(row, detail, now = new Date()) {
  const d = detail ?? {};
  const attachmentsRaw = d.attachments ?? [];
  const schedule = {
    applyStart: isoOf(row.attend_req_begin_at),
    applyEnd: isoOf(row.attend_req_close_at),
    bidStart: isoOf(row.bid_begin_at),
    bidEnd: isoOf(row.bid_end_at),
    opening: isoOf(d.open_bid_at),
    qualifyDue: null, // 통보일 이후 정해져 날짜가 없다 (상세 화면에서 원문 문구로 안내)
  };
  const lastDate = schedule.bidEnd ?? schedule.applyEnd;
  const closed = row.closed === true || (lastDate && new Date(lastDate) < now);
  const corrected = d.corrected === true;
  const kind = noticeType(row, d);

  return {
    id: row.notice_no,
    title: row.title,
    issuer: d.place_name ? `한국전력공사 ${d.place_name}` : "한국전력공사",
    ...kind,
    ...noticeAreas(d.region, d.place_name),
    placeName: d.place_name ?? null,
    category: `${NOTICE_TYPE[kind.type]} · 적격심사`,
    status: closed ? "CLOSED" : corrected ? "CORRECTED" : "OPEN",
    // 참가신청 기간이 아직 시작되지 않음 (SRM '참가신청전'). 저장된 progress_state는 수집 시점 값이라 날짜로 판단한다
    notStarted: !closed && Boolean(schedule.applyStart) && new Date(schedule.applyStart) > now,
    correction: corrected ? `정정 공고입니다 (SRM 정정 ${d.revision}차). 바뀐 내용은 공고 원문에서 확인하세요.` : null,
    revision: d.revision ?? null,
    estimatedPrice: row.presumed_price ?? null,
    basePrice: d.estimated_price_basic_amount ?? null,
    aAmount: d.a_amount ?? null,
    noticeDate: d.notice_date ?? null,
    regionLabel: regionLabel(d.region),
    schedule,
    scheduleMeta: {
      applyEnd: { confirmed: true, source: "공식 Open API 공고 일정" },
      bidEnd: { confirmed: true, source: "공식 Open API 공고 일정" },
      opening: { confirmed: true, source: "SRM 상세 개찰일시" },
    },
    detailLoaded: Boolean(detail),
    detailStatus: row.detail_status ?? d.detail_status ?? null,
    conditions: {
      license: d.license ?? null,
      qualification: d.qualification ?? null,
      region: d.region ?? null,
      licenseMain: d.license_main ?? null,
    },
    documents: {
      notice: docState(d.notice_doc_found, attachmentsRaw, "NOTICE"),
      criteria: docState(d.criteria_doc_found, attachmentsRaw, "CRITERIA"),
    },
    attachments: attachmentsRaw.map((a) => ({
      id: a.id,
      kind: KIND[a.doc_kind] ?? "기타",
      name: a.filename,
      ext: a.file_ext,
      size: fileSize(a.size_bytes),
      origin: ORIGIN[a.source] ?? a.source ?? "",
      ok: a.download_status === "SUCCESS",
    })),
    // 아직 백엔드에 없는 분석 결과 (공고문 추가 조건, 필요서류). 연결되면 여기서 채운다.
    extraConditions: null,
    requiredDocs: [],
  };
}

/* ───────── 업체 ───────── */

/** GET /api/companies/<id> → 화면 모양 */
export function toCompany(c) {
  if (!c) return null;
  return {
    id: c.company_id,
    name: c.company_name,
    bizNo: c.business_registration_no ?? "",
    address: c.head_office_address ?? "",
    areaLimit: c.area_limit,
    areaName: c.region?.area_limit_name ?? c.area_limit,
    subAreaCode: c.sub_area_code,
    subAreaName: c.region?.region_name ?? "",
    addressStatus: c.address_validation_status,
    licenses: (c.licenses ?? []).map((l) => ({
      code: l.license_code,
      name: l.license_name,
      regNo: l.license_registration_no ?? "",
      regDate: l.license_registration_date ?? "",
    })),
    qualifications: (c.qualifications ?? []).map((q) => ({ code: q.qualification_code, name: q.qualification_name })),
    updatedAt: c.updated_at,
  };
}

/** 업체정보 화면 → POST/PUT 본문. 주소를 새로 고르지 않았으면 주소 칸을 보내지 않는다 (서버가 기존 값 유지). */
export function toCompanyPayload(form, { includeAddress }) {
  const body = {
    company_name: form.name.trim(),
    business_registration_no: form.bizNo.replace(/\D/g, "") || null,
    licenses: form.licenses.map((l) => ({
      license_code: l.code,
      license_registration_no: l.regNo.trim(),
      license_registration_date: l.regDate,
    })),
    qualifications: form.qualifications.map((q) => q.code),
  };
  if (includeAddress) {
    const detail = form.addressDetail?.trim();
    body.head_office_address = detail ? `${form.address} ${detail}` : form.address;
    body.juso = form.juso;
  }
  return body;
}
