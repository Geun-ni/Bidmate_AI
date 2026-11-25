/**
 * 베타테스트 의견 수집 — 세 갈래가 모두 POST /api/feedback 한 곳으로 보낸다 (백엔드 구현 예정).
 *
 *   A. 상시 '의견 보내기'  { kind: "general",  category, message, email?, attachment? }
 *   B. 맥락형 👍👎          { kind: "reaction", target, value: "up"|"down", comment? }
 *   C. 베타 설문 페이지      { kind: "survey",   answers: { ... } }
 *
 * 모든 의견에 context(보던 화면·공고번호·업체·브라우저·화면 크기·시각)를 자동으로 붙인다.
 * 형식은 docs/design/frontend-requirements.md 7장에 정리했다.
 */
import { sendFeedback as post } from "../api/client.js";
import { load, save } from "./storage.js";

/** 지금 화면 정보 (테스터가 따로 적지 않아도 어디서 남긴 의견인지 알 수 있게) */
export function feedbackContext({ user, company, testMode, noticeId } = {}) {
  const path = window.location.pathname + window.location.search;
  return {
    page: path,
    notice_no: noticeId ?? window.location.pathname.match(/^\/notices\/([^/?#]+)/)?.[1] ?? null,
    user_id: testMode ? null : user?.user_id ?? null,
    company_id: testMode ? null : company?.id ?? null,
    test_mode: Boolean(testMode),
    viewport: `${window.innerWidth}x${window.innerHeight}`,
    user_agent: navigator.userAgent,
    created_at: new Date().toISOString(),
  };
}

/** 보내기. 백엔드 경로가 아직 없으면(404) 알아볼 수 있는 문장으로 바꿔 던진다 */
export async function sendFeedback(body) {
  try {
    return await post(body);
  } catch (e) {
    if (e.status === 404) throw new Error("의견 저장 기능이 아직 준비 중입니다. (서버에 /api/feedback 이 없습니다)");
    throw e;
  }
}

/* B. 같은 대상에 이미 반응했는지 (브라우저 기억, 버튼 상태 표시용) */
const REACTED = "feedbackReacted";
export const reactedValue = (key) => load(REACTED, {})[key] ?? null;
export const rememberReaction = (key, value) => save(REACTED, { ...load(REACTED, {}), [key]: value });

/* C. 설문 제출 여부 */
export const surveyDone = () => load("betaSurveyDone", false);
export const markSurveyDone = () => save("betaSurveyDone", true);

/** 첨부 이미지 → { name, type, size, data(base64 data URL) }. 너무 크면 거절 */
export const MAX_ATTACHMENT = 3 * 1024 * 1024;
export function readAttachment(file) {
  return new Promise((resolve, reject) => {
    if (!file.type.startsWith("image/")) return reject(new Error("이미지 파일만 첨부할 수 있습니다."));
    if (file.size > MAX_ATTACHMENT) return reject(new Error("이미지는 3MB까지 첨부할 수 있습니다."));
    const r = new FileReader();
    r.onload = () => resolve({ name: file.name, type: file.type, size: file.size, data: r.result });
    r.onerror = () => reject(new Error("파일을 읽지 못했습니다."));
    r.readAsDataURL(file);
  });
}
