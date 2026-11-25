import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { scrollToTop, useScrollReveal, useSmoothScroll } from "../lib/motion.js";
import { useAppState } from "../state/AppState.jsx";
import QnaPanel, { QnaProvider } from "./QnaPanel.jsx";
import { FeedbackProvider, useFeedback } from "./Feedback.jsx";

/** 지금 화면에 실제로 적용된 모드 (자동이면 시스템 설정을 따른다) */
function useEffectiveTheme(theme) {
  const query = "(prefers-color-scheme: dark)";
  const [systemDark, setSystemDark] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (e) => setSystemDark(e.matches);
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, []);
  return theme === "system" ? (systemDark ? "dark" : "light") : theme;
}
const FONTS = [
  { value: "normal", label: "가", title: "글자 크기 보통" },
  { value: "large", label: "가+", title: "글자 크기 크게" },
  { value: "xlarge", label: "가++", title: "글자 크기 아주 크게" },
];

function Header() {
  const { theme, setTheme, fontSize, setFontSize, user, signOut } = useAppState();
  const { openFeedback } = useFeedback();
  const effective = useEffectiveTheme(theme);
  const isDark = effective === "dark";
  const toggleLabel = isDark ? "라이트 모드로 전환" : "다크 모드로 전환";
  // 스크롤하면 헤더 아래에 얇은 선이 생긴다
  const [scrolled, setScrolled] = useState(false);
  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header className={`topbar${scrolled ? " scrolled" : ""}`}>
      <div className="wrap topbar-inner">
        <NavLink to="/" className="brand">
          <img className="brand-logo" src="/favicon.svg" alt="" aria-hidden="true" width="30" height="30" />
          입찰메이트
        </NavLink>
        <nav className="topbar-nav" aria-label="주 메뉴">
          <NavLink to="/" end>추천 공고</NavLink>
          <NavLink to="/company">업체정보</NavLink>
        </nav>
        <div className="topbar-tools">
          <button type="button" className="btn btn-sm btn-ghost fb-open" onClick={() => openFeedback()} title="베타테스트 의견 보내기">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z" /><path d="M8 9h8M8 13h5" /></svg>
            <span className="fb-open-label">의견 보내기</span>
          </button>
          {user === undefined ? null : user ? (
            <span className="user-menu">
              <span className="small muted user-name" title={user.email}>
                {user.display_name || user.email}
                {user.is_admin && <span className="badge badge-brand" style={{ marginLeft: 6 }}>관리자</span>}
                {user.test && <span className="badge badge-warn" style={{ marginLeft: 6 }}>테스트 모드</span>}
              </span>
              <button type="button" className="btn btn-sm btn-ghost" onClick={signOut}>로그아웃</button>
            </span>
          ) : (
            <NavLink to="/login" className="btn btn-sm">로그인</NavLink>
          )}
          <div className="seg" role="group" aria-label="글자 크기">
            {FONTS.map((f) => (
              <button key={f.value} type="button" title={f.title} aria-label={f.title} aria-pressed={fontSize === f.value} onClick={() => setFontSize(f.value)}>
                {f.label}
              </button>
            ))}
          </div>
          {/* 한 번 누르면 지금 보이는 모드의 반대로 바로 바뀐다 */}
          <button type="button" className="tool-btn" onClick={() => setTheme(isDark ? "light" : "dark")} title={toggleLabel} aria-label={toggleLabel}>
            {isDark ? (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                <circle cx="12" cy="12" r="4.5" />
                <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
              </svg>
            ) : (
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" aria-hidden="true">
                <path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" />
              </svg>
            )}
            <span className="theme-label">{isDark ? "라이트" : "다크"}</span>
          </button>
        </div>
      </div>
    </header>
  );
}

export default function Layout() {
  const location = useLocation();
  useSmoothScroll();
  // 화면을 옮기면 맨 위에서 시작 (같은 화면 안의 탭 전환은 제외)
  useEffect(() => {
    scrollToTop();
  }, [location.pathname]);
  useScrollReveal([location.pathname, location.search]);

  return (
    <QnaProvider>
      <FeedbackProvider>
      <Header />
      <main className="wrap page">
        <Outlet />
      </main>
      <footer className="wrap site-footer">
        입찰메이트는 참고용 서비스이며 법적 효력이 없습니다. 추천·예상 점수는 1차 검토 결과이며 낙찰을 보장하지 않습니다. 최종 확인은 공고 원문과 발주처 기준에 따릅니다.
      </footer>
      <QnaPanel />
      </FeedbackProvider>
    </QnaProvider>
  );
}
