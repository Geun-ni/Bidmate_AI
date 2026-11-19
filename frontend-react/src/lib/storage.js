/** localStorage는 사생활 보호 모드 등에서 실패할 수 있으므로 항상 감싸서 쓴다. */
const PREFIX = "bidmate:";

export function load(key, fallback) {
  try {
    const raw = localStorage.getItem(PREFIX + key);
    return raw === null ? fallback : JSON.parse(raw);
  } catch {
    return fallback;
  }
}

export function save(key, value) {
  try {
    localStorage.setItem(PREFIX + key, JSON.stringify(value));
  } catch {
    /* 저장 실패는 무시: 새로고침하면 초기화될 뿐이다. */
  }
}
