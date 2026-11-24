import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import * as api from "../api/client.js";
import { toCompany, toNotice } from "../lib/adapt.js";
import { judge } from "../lib/judgment.js";
import { load, save } from "../lib/storage.js";

const AppState = createContext(null);

/**
 * 테스트 모드 (개발 서버에서만): 서버 계정 없이 '테스트 사용자'로 로그인한 것처럼 쓴다.
 * 업체정보·관심공고·점수 입력값은 서버가 아니라 이 브라우저에만 저장해, 팀 운영 DB에 테스트 데이터가 쌓이지 않게 한다.
 * 공고·배점·점수 계산은 실제 API를 그대로 쓴다.
 */
const TEST_ENABLED = import.meta.env.DEV;
const TEST_USER = { user_id: "test-user", email: "test@localhost", display_name: "테스트 사용자", is_admin: false, test: true };
export const DEFAULT_TEST_COMPANY = {
  id: "test-company",
  name: "테스트 전기공사 (가상)",
  bizNo: "",
  address: "서울특별시 강남구 (가상 주소)",
  areaLimit: "S",
  areaName: "서울",
  subAreaCode: "091001",
  subAreaName: "강남구",
  licenses: [{ code: "A06", name: "전기공사업", regNo: "TEST-0001", regDate: "2015-03-02" }],
  qualifications: [],
};

function usePersistent(key, initial) {
  const [value, setValue] = useState(() => load(key, initial));
  useEffect(() => save(key, value), [key, value]);
  return [value, setValue];
}

export function AppStateProvider({ children }) {
  const navigate = useNavigate();
  const location = useLocation();

  // 화면 설정 (브라우저 저장)
  const [theme, setTheme] = usePersistent("theme", "system"); // system | light | dark
  const [fontSize, setFontSize] = usePersistent("fontSize", "normal"); // normal | large | xlarge
  // 서류 준비 상태: 백엔드에 저장 API가 없어 이 브라우저에만 남긴다
  const [checklists, setChecklists] = usePersistent("checklists", {});
  const [companyId, setCompanyId] = usePersistent("companyId", null);
  // 테스트 모드 상태 (브라우저 저장)
  const [testFlag, setTestFlag] = usePersistent("testMode", false);
  const testMode = TEST_ENABLED && testFlag;
  const [testCompany, setTestCompany] = usePersistent("testCompany", DEFAULT_TEST_COMPANY);
  const [testFavorites, setTestFavorites] = usePersistent("testFavorites", []);

  // 로그인 사용자: undefined = 확인 중, null = 로그인 안 함
  const [user, setUser] = useState(undefined);
  // 업체: 내 업체 목록과 고른 업체 상세
  const [companies, setCompanies] = useState([]);
  const [joinRequests, setJoinRequests] = useState([]);
  const [company, setCompany] = useState(null);
  const [companyLoading, setCompanyLoading] = useState(false);
  // 공고
  const [baseNotices, setBaseNotices] = useState([]);
  const [lastRun, setLastRun] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  // 관심공고 (로그인 사용자별, 서버 저장)
  const [favoriteRows, setFavoriteRows] = useState([]);
  const [extraNotices, setExtraNotices] = useState([]); // 진행 중 목록에 없는 관심공고 (마감 등)

  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") delete root.dataset.theme;
    else root.dataset.theme = theme;
    if (fontSize === "normal") delete root.dataset.font;
    else root.dataset.font = fontSize;
  }, [theme, fontSize]);

  /* 로그인 확인 */
  useEffect(() => {
    if (testMode) {
      setUser(TEST_USER);
      return;
    }
    api.fetchMe().then((r) => setUser(r.user)).catch(() => setUser(null));
  }, [testMode]);

  /* 공고 목록 (로그인과 무관) */
  const reload = useCallback(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    api
      .fetchNotices()
      .then((r) => {
        if (!alive) return;
        setBaseNotices(r.notices);
        setLastRun(r.lastRun);
      })
      .catch((e) => alive && setError(e))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);
  useEffect(() => reload(), [reload]);

  /* 업체 */
  // 고른 업체 ID는 ref로도 들고 있어, 바꿔도 refreshCompany가 새로 만들어지지 않게 한다
  const companyIdRef = useRef(companyId);
  const refreshCompany = useCallback(
    async (preferId = companyIdRef.current) => {
      if (testMode) {
        setCompanies([]);
        setJoinRequests([]);
        setCompany(testCompany);
        return testCompany;
      }
      if (!user) {
        setCompanies([]);
        setJoinRequests([]);
        setCompany(null);
        return null;
      }
      setCompanyLoading(true);
      try {
        const list = await api.fetchCompanies();
        setCompanies(list.companies);
        setJoinRequests(list.join_requests ?? []);
        const pick = list.companies.find((c) => c.company_id === preferId) ?? list.companies[0];
        const detail = pick ? toCompany(await api.fetchCompany(pick.company_id)) : null;
        setCompany(detail);
        companyIdRef.current = detail?.id ?? null;
        setCompanyId(companyIdRef.current);
        return detail;
      } catch {
        setCompany(null);
        return null;
      } finally {
        setCompanyLoading(false);
      }
    },
    [user, setCompanyId, testMode, testCompany],
  );
  useEffect(() => {
    if (user !== undefined) refreshCompany();
  }, [user, refreshCompany]);
  const selectCompany = useCallback((id) => refreshCompany(id), [refreshCompany]);

  /* 관심공고 */
  useEffect(() => {
    if (testMode) {
      setFavoriteRows(testFavorites.map((no) => ({ notice_no: no })));
      return;
    }
    if (!user) {
      setFavoriteRows([]);
      return;
    }
    api.fetchFavorites().then((r) => setFavoriteRows(r.favorites)).catch(() => setFavoriteRows([]));
  }, [user, testMode, testFavorites]);

  // 진행 중 목록에 없는 관심공고는 따로 상세를 받아 마감 공고로 함께 보여 준다
  useEffect(() => {
    if (loading) return;
    const known = new Set(baseNotices.map((n) => n.id));
    const missing = favoriteRows.filter((f) => !known.has(f.notice_no));
    if (!missing.length) {
      setExtraNotices([]);
      return;
    }
    let alive = true;
    Promise.all(
      missing.map((f) =>
        api
          .fetchNoticeDetail(f.notice_no)
          .catch(() => null)
          .then((detail) => (detail || f.title ? toNotice({ ...(detail ?? {}), ...f }, detail) : null)),
      ),
    ).then((list) => alive && setExtraNotices(list.filter(Boolean)));
    return () => {
      alive = false;
    };
  }, [favoriteRows, baseNotices, loading]);

  const favorites = useMemo(() => favoriteRows.map((f) => f.notice_no), [favoriteRows]);

  const requireLogin = useCallback(() => navigate("/login", { state: { from: location.pathname + location.search } }), [navigate, location]);

  const toggleFavorite = useCallback(
    async (id) => {
      if (!user) {
        requireLogin();
        return;
      }
      const on = favorites.includes(id);
      if (testMode) {
        setTestFavorites((list) => (on ? list.filter((x) => x !== id) : [...list, id]));
        return;
      }
      // 바로 반영하고, 실패하면 되돌린다
      setFavoriteRows((rows) => (on ? rows.filter((r) => r.notice_no !== id) : [...rows, { notice_no: id }]));
      try {
        const r = on ? await api.removeFavorite(id) : await api.addFavorite(id);
        setFavoriteRows(r.favorites);
      } catch {
        setFavoriteRows((rows) => (on ? [...rows, { notice_no: id }] : rows.filter((r) => r.notice_no !== id)));
      }
    },
    [user, favorites, requireLogin, testMode, setTestFavorites],
  );

  /* 로그인·로그아웃 */
  const signIn = useCallback(async (email, password) => {
    const r = await api.login(email, password);
    setUser(r.user);
    return r.user;
  }, []);
  const signUp = useCallback(async (body) => {
    const r = await api.signup(body);
    setUser(r.user);
    return r.user;
  }, []);
  /** 자동 로그인 (테스트 모드 시작) */
  const startTestMode = useCallback(() => {
    setTestFlag(true);
    setUser(TEST_USER);
  }, [setTestFlag]);
  /** 테스트 업체 저장 (서버 대신 브라우저에) */
  const saveTestCompany = useCallback((c) => {
    setTestCompany(c);
    setCompany(c);
  }, [setTestCompany]);

  const signOut = useCallback(async () => {
    if (testMode) {
      setTestFlag(false);
      setUser(null);
      navigate("/");
      return;
    }
    await api.logout().catch(() => {});
    setUser(null);
    navigate("/");
  }, [navigate, testMode, setTestFlag]);

  /** 서류 상태 (FUNC-025): ready 준비 / todo 미준비 / na 해당 없음 / check 확인 필요. */
  const docStatuses = useCallback((noticeId) => checklists[noticeId] ?? {}, [checklists]);
  const setDocStatus = useCallback(
    (noticeId, docName, status) =>
      setChecklists((all) => {
        const current = { ...(all[noticeId] ?? {}) };
        if (status === "todo") delete current[docName];
        else current[docName] = status;
        return { ...all, [noticeId]: current };
      }),
    [setChecklists],
  );

  /* 업체 기준 판정을 붙인 공고 */
  const notices = useMemo(
    () => [...baseNotices, ...extraNotices].map((n) => ({ ...n, judgment: judge(n, company) })),
    [baseNotices, extraNotices, company],
  );
  const withJudgment = useCallback((n) => (n ? { ...n, judgment: judge(n, company) } : n), [company]);

  const value = useMemo(
    () => ({
      theme, setTheme, fontSize, setFontSize,
      user, signIn, signUp, signOut, requireLogin,
      testEnabled: TEST_ENABLED, testMode, startTestMode, saveTestCompany,
      company, companies, joinRequests, companyLoading, refreshCompany, selectCompany,
      favorites, toggleFavorite, isFavorite: (id) => favorites.includes(id),
      docStatuses, setDocStatus,
      notices, lastRun, loading, error, reload, withJudgment,
    }),
    [theme, setTheme, fontSize, setFontSize, user, signIn, signUp, signOut, requireLogin, startTestMode, saveTestCompany, testMode, company, companies, joinRequests, companyLoading, refreshCompany, selectCompany, favorites, toggleFavorite, docStatuses, setDocStatus, notices, lastRun, loading, error, reload, withJudgment],
  );

  return <AppState.Provider value={value}>{children}</AppState.Provider>;
}

export function useAppState() {
  const ctx = useContext(AppState);
  if (!ctx) throw new Error("AppStateProvider 안에서 사용하세요.");
  return ctx;
}
