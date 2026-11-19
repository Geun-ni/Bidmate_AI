import { noticesFor } from "../mocks/scenarios.js";

/**
 * 데이터 계층. 지금은 목업을 돌려준다.
 * 백엔드 API가 준비되면 이 파일의 함수만 fetch("/api/...")로 바꾸면 된다.
 * (vite.config.js에서 /api 요청은 Python 서버 8000번으로 전달된다.)
 */
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

export async function fetchRecommendations(scenario) {
  await delay(450);
  if (scenario === "error") throw new Error("HTTP 500");
  return noticesFor(scenario);
}
