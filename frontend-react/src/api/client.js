import { toNotice } from "../lib/adapt.js";

/**
 * 백엔드 API (backend/server.py) 호출.
 * 같은 주소(/api)로 부른다: 배포에서는 nginx가, 개발에서는 vite 프록시(vite.config.js)가 백엔드로 넘긴다.
 * 로그인은 백엔드가 주는 HttpOnly 세션 쿠키라서 화면에서는 따로 저장하지 않는다.
 */
export class ApiError extends Error {
  constructor(status, message) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body, { raw = false } = {}) {
  const init = { method, credentials: "same-origin", headers: {} };
  if (raw) {
    init.body = body;
  } else if (body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, "서버에 연결하지 못했습니다. 네트워크를 확인해 주세요.");
  }
  let data = null;
  try {
    data = await res.json();
  } catch {
    /* 본문이 JSON이 아니면 아래에서 상태 코드로만 알린다 */
  }
  if (!res.ok) throw new ApiError(res.status, data?.error ?? `서버 오류 (HTTP ${res.status})`);
  return data;
}

const get = (path) => request("GET", path);
const enc = encodeURIComponent;

/** 404를 '없음'(null)으로 바꾼다 (저장된 계산 결과·업로드 기준서 조회용) */
const orNull = (promise) => promise.catch((e) => (e.status === 404 ? null : Promise.reject(e)));

/** 동시에 limit개씩만 요청한다 (공고 상세를 한꺼번에 받을 때 서버 부담을 줄인다) */
async function mapPool(items, limit, fn) {
  const out = new Array(items.length);
  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const i = next++;
      try {
        out[i] = await fn(items[i]);
      } catch {
        out[i] = null; // 상세를 못 받은 공고는 조건을 '확인 필요'로 보여 준다
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
  return out;
}

/* ───────── 공고 ───────── */
const detailCache = new Map();

export async function fetchNoticeDetail(no, { fresh = false } = {}) {
  if (!fresh && detailCache.has(no)) return detailCache.get(no);
  const detail = await get(`/api/notices/${enc(no)}`);
  detailCache.set(no, detail);
  return detail;
}

/** 진행 중 공고 목록 + 공고별 상세(조건·첨부·A값). 목록에는 조건이 없어 상세를 함께 받는다. */
export async function fetchNotices() {
  const list = await get("/api/notices");
  const details = await mapPool(list.notices, 8, (row) => fetchNoticeDetail(row.notice_no, { fresh: true }));
  return { notices: list.notices.map((row, i) => toNotice(row, details[i])), lastRun: list.last_run };
}

/** 목록에 없는 공고(마감된 관심공고, 주소로 바로 들어온 공고) 1건 */
export async function fetchNotice(no) {
  const detail = await fetchNoticeDetail(no);
  return toNotice(detail, detail);
}

/** 공고 검색 (공고명·공고번호). includeClosed면 마감 공고까지 → { notices: 목록 행, count } */
export const searchNotices = (q, includeClosed = false) =>
  get(`/api/notices?q=${enc(q)}${includeClosed ? "&include_closed=1" : ""}`);

export const fetchEvaluation = (no) => get(`/api/notices/${enc(no)}/evaluation`);
export const fetchNoticeSchedule = (no) => get(`/api/notices/${enc(no)}/schedule`);
export const calculateScore = (no, inputs, companyId) =>
  request("POST", `/api/notices/${enc(no)}/score`, { inputs, company_id: companyId || undefined });

/* ───────── 로그인 ───────── */
export const fetchMe = () => get("/api/auth/me");
export const login = (email, password) => request("POST", "/api/auth/login", { email, password });
export const signup = (body) => request("POST", "/api/auth/signup", body);
export const logout = () => request("POST", "/api/auth/logout");

/* ───────── 관심공고 ───────── */
export const fetchFavorites = () => get("/api/favorites");
export const addFavorite = (no) => request("PUT", `/api/favorites/${enc(no)}`);
export const removeFavorite = (no) => request("DELETE", `/api/favorites/${enc(no)}`);

/* ───────── 업체 ───────── */
export const fetchCompanies = () => get("/api/companies");
export const fetchCompany = (id) => get(`/api/companies/${enc(id)}`);
export const createCompany = (body) => request("POST", "/api/companies", body);
export const updateCompany = (id, body) => request("PUT", `/api/companies/${enc(id)}`, body);
export const lookupCompany = (brn) => get(`/api/companies/lookup?brn=${enc(brn)}`);
export const fetchMembers = (id) => get(`/api/companies/${enc(id)}/members`);
export const setMemberStatus = (id, userId, status) =>
  request("PUT", `/api/companies/${enc(id)}/members/${enc(userId)}`, { status });

export const fetchDraft = (cid, no) => orNull(get(`/api/companies/${enc(cid)}/evaluation-drafts/${enc(no)}`));
export const saveDraft = (cid, no, inputs) =>
  request("PUT", `/api/companies/${enc(cid)}/evaluation-drafts/${enc(no)}`, { inputs });

export const fetchUploadedCriteria = (cid, no) => orNull(get(`/api/companies/${enc(cid)}/uploaded-criteria/${enc(no)}`));
export const uploadCriteria = (cid, no, file) =>
  request("PUT", `/api/companies/${enc(cid)}/uploaded-criteria/${enc(no)}?filename=${enc(file.name)}`, file, { raw: true });
export const deleteUploadedCriteria = (cid, no) => request("DELETE", `/api/companies/${enc(cid)}/uploaded-criteria/${enc(no)}`);

/* ───────── 기준정보 ───────── */
export const searchLicenses = (q) => get(`/api/reference/licenses?q=${enc(q)}`);
export const fetchQualifications = () => get("/api/reference/qualifications");
export const fetchRegions = () => get("/api/reference/regions");
export const matchRegion = (si, sgg) => get(`/api/reference/region-match?si=${enc(si)}&sgg=${enc(sgg ?? "")}`);

/* ───────── 첨부파일 다운로드 ─────────
 * 서버에 저장된 원본(공고문·적격심사기준)을 받는다: GET /api/attachments/<첨부 id>
 * (백엔드에 이 경로가 아직 없으면 404 → 화면에서 SRM 안내로 대신한다)
 */
export async function downloadAttachment(id, filename) {
  let res;
  try {
    res = await fetch(`/api/attachments/${enc(id)}`, { credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "서버에 연결하지 못했습니다.");
  }
  const type = res.headers.get("Content-Type") ?? "";
  if (!res.ok || type.includes("application/json")) {
    let message = `HTTP ${res.status}`;
    try {
      message = (await res.json()).error ?? message;
    } catch {
      /* 본문 없음 */
    }
    throw new ApiError(res.ok ? 404 : res.status, message);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/* ───────── AI 질문 (Q&A) ─────────
 * POST /api/qna  { question, notice_no?, company_id?, history? }
 *   → { answer, sources?: [{ label, notice_no?, field? }] }
 * 백엔드가 질문을 받아 DB 표 설명과 함께 LLM에 넘기고, 조회한 데이터로 답을 만든다 (백엔드 담당).
 * 아직 경로가 없으면 404 → 화면에서 '준비 중'으로 안내한다.
 */
export const askQna = (body) => request("POST", "/api/qna", body);
