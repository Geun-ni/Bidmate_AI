import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { fetchRecommendations } from "../api/client.js";
import { MOCK_COMPANY } from "../mocks/company.js";
import { load, save } from "../lib/storage.js";

const AppState = createContext(null);

function usePersistent(key, initial) {
  const [value, setValue] = useState(() => load(key, initial));
  useEffect(() => save(key, value), [key, value]);
  return [value, setValue];
}

export function AppStateProvider({ children }) {
  // 화면 설정
  const [theme, setTheme] = usePersistent("theme", "system"); // system | light | dark
  const [fontSize, setFontSize] = usePersistent("fontSize", "normal"); // normal | large | xlarge
  // 사용자 관리 정보 (초안: 브라우저 저장)
  const [savedCompany, setSavedCompany] = usePersistent("company", null);
  const [favorites, setFavorites] = usePersistent("favorites", ["E012600812"]);
  const [checklists, setChecklists] = usePersistent("checklists", {});
  // 개발용 목업 시나리오
  const [scenario, setScenario] = usePersistent("scenario", "default");

  const [notices, setNotices] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    if (fontSize === "normal") delete root.dataset.font;
    else root.dataset.font = fontSize;
  }, [theme, fontSize]);

  const reload = useCallback(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    fetchRecommendations(scenario)
      .then((data) => alive && setNotices(data))
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [scenario]);

  useEffect(() => reload(), [reload]);

  const company = scenario === "noCompany" ? null : savedCompany ?? MOCK_COMPANY;

  const toggleFavorite = useCallback(
    (id) => setFavorites((list) => (list.includes(id) ? list.filter((x) => x !== id) : [...list, id])),
    [setFavorites],
  );

  /**
   * 서류 상태 (FUNC-025): ready 준비 / todo 미준비 / na 해당 없음 / check 확인 필요.
   * 예전 형식(준비된 서류 이름 배열)도 읽어서 ready로 바꾼다.
   */
  const docStatuses = useCallback(
    (noticeId) => {
      const saved = checklists[noticeId];
      if (Array.isArray(saved)) return Object.fromEntries(saved.map((name) => [name, "ready"]));
      return saved ?? {};
    },
    [checklists],
  );

  const setDocStatus = useCallback(
    (noticeId, docName, status) =>
      setChecklists((all) => {
        const saved = all[noticeId];
        const current = Array.isArray(saved) ? Object.fromEntries(saved.map((n) => [n, "ready"])) : { ...(saved ?? {}) };
        if (status === "todo") delete current[docName];
        else current[docName] = status;
        return { ...all, [noticeId]: current };
      }),
    [setChecklists],
  );

  const value = useMemo(
    () => ({
      theme, setTheme, fontSize, setFontSize,
      company, saveCompany: setSavedCompany,
      favorites, toggleFavorite, isFavorite: (id) => favorites.includes(id),
      docStatuses, setDocStatus,
      scenario, setScenario,
      notices, loading, error, reload,
    }),
    [theme, fontSize, company, setSavedCompany, favorites, toggleFavorite, docStatuses, setDocStatus, scenario, setScenario, notices, loading, error, reload, setTheme, setFontSize],
  );

  return <AppState.Provider value={value}>{children}</AppState.Provider>;
}

export function useAppState() {
  const ctx = useContext(AppState);
  if (!ctx) throw new Error("AppStateProvider 안에서 사용하세요.");
  return ctx;
}
