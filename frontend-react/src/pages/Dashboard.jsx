import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { searchNotices } from "../api/client.js";
import { isoOf } from "../lib/adapt.js";
import CompanyCard from "../components/dashboard/CompanyCard.jsx";
import FavoritesCalendar from "../components/dashboard/FavoritesCalendar.jsx";
import NoticeCard from "../components/dashboard/NoticeCard.jsx";
import Select from "../components/Select.jsx";
import { Disclaimer, EmptyState, Highlight, Money, isInactive } from "../components/ui.jsx";
import { nextDeadline } from "../lib/notice.js";
import { load, save } from "../lib/storage.js";
import { fullDateTime, shortDateTime } from "../lib/format.js";
import { useCountUp } from "../lib/motion.js";
import { useAppState } from "../state/AppState.jsx";
import TestLoginButton from "../components/TestLoginButton.jsx";
import "./Dashboard.css";

/** 요약 숫자: 화면에 들어오면 0부터 올라간다 */
function Stat({ value, label, tone }) {
  const [ref, shown] = useCountUp(value);
  return (
    <li className={tone ? `stat-${tone}` : undefined}>
      <span ref={ref} className="stat-num num">{shown}</span>
      <span className="stat-label">{label}</span>
    </li>
  );
}

const DEFAULT_FILTERS = { favoritesOnly: false, judgment: "ALL", showInactive: false, showMismatch: false, sort: "deadline" };

export default function Dashboard() {
  const { user, company, companyLoading, notices: allNotices, lastRun, loading: noticesLoading, error, reload, favorites } = useAppState();
  // 업체 기준 판정 전에는 목록을 보여 주지 않는다 (판정 없는 목록이 추천처럼 보이지 않게)
  const loading = noticesLoading || user === undefined || companyLoading;
  const notices = allNotices;
  const [filters, setFilters] = useState(() => ({ ...DEFAULT_FILTERS, ...load("filters", {}) }));
  const sentinelRef = useRef(null);
  const sideRef = useRef(null);
  const stickyRef = useRef(null);
  const stuckRef = useRef(false);
  // 왼쪽 업체 카드 높이: 아래 달력이 오른쪽 공고 목록(안내 문구)과 같은 높이에서 시작하도록 맞춘다
  const [companyHeight, setCompanyHeight] = useState(null);
  // 왼쪽 칸이 화면보다 길면 고정을 푼다 (칸 안 스크롤바 없이 아래까지 보이게)
  const [sideTooTall, setSideTooTall] = useState(false);
  useEffect(() => {
    const side = sideRef.current;
    if (!side) return undefined;
    const check = () => {
      const header = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--header-h")) || 64;
      setSideTooTall(side.offsetHeight > window.innerHeight - header - 36);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(side);
    window.addEventListener("resize", check);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", check);
    };
  }, []);
  useEffect(() => {
    const layoutTop = (el) => {
      let y = 0;
      for (let e = el; e; e = e.offsetParent) y += e.offsetTop; // transform(등장 효과)의 영향을 받지 않는 위치
      return y;
    };
    const measure = () => {
      const side = sideRef.current;
      const next = stickyRef.current?.nextElementSibling;
      // 두 열 배치일 때만, 그리고 고정(축소) 상태가 아닐 때만 잰다 (스크롤 중 카드가 커졌다 작아졌다 하지 않게)
      if (!side || !next || window.innerWidth <= 960) return setCompanyHeight(null);
      if (stuckRef.current) return undefined;
      const gap = parseFloat(getComputedStyle(side).rowGap) || 16;
      const h = layoutTop(next) - layoutTop(side) - gap;
      setCompanyHeight(h > 0 ? Math.round(h) : null);
      return undefined;
    };
    measure();
    const ro = new ResizeObserver(measure);
    if (stickyRef.current) ro.observe(stickyRef.current);
    window.addEventListener("resize", measure);
    return () => {
      ro.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, []);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    // 표시가 상단 헤더(64px) 위로 올라가면 고정 상태로 본다
    // (값이 같으면 React가 다시 그리지 않으므로 스크롤마다 호출해도 가볍다)
    const check = () => {
      const el = sentinelRef.current;
      if (el) {
        stuckRef.current = el.getBoundingClientRect().top < 65;
        setStuck(stuckRef.current);
      }
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);

  /* ───────── 검색 (백엔드 GET /api/notices?q=, 공고명·공고번호) ─────────
   * 검색어는 주소(?q=)에 남겨 상세를 보고 돌아와도 유지한다.
   * 진행 중 공고는 지금 목록(판정 포함)에서 결과만 남기고, '마감건 포함'이면 마감 공고도 서버에서 찾아 따로 보여 준다. */
  const [params, setParams] = useSearchParams();
  const q = (params.get("q") ?? "").trim();
  const [text, setText] = useState(q);
  const [search, setSearch] = useState({ q: "", ids: null, closed: [], loading: false, error: null });
  const [closedShown, setClosedShown] = useState(20);
  useEffect(() => setText(q), [q]);
  useEffect(() => {
    // 입력이 멈추면(0.3초) 주소의 검색어를 바꾼다
    const next = text.trim();
    if (next === q) return undefined;
    const t = setTimeout(() => setParams(next ? { q: next } : {}, { replace: true }), 300);
    return () => clearTimeout(t);
  }, [text, q, setParams]);
  useEffect(() => {
    if (!q) {
      setSearch({ q: "", ids: null, closed: [], loading: false, error: null });
      return undefined;
    }
    let alive = true;
    setSearch((s) => ({ ...s, loading: true, error: null }));
    setClosedShown(20);
    searchNotices(q, filters.showInactive)
      .then((r) => {
        if (!alive) return;
        const ids = new Set(r.notices.map((n) => n.notice_no));
        // 지금 목록(진행 중)에 없는 결과 = 참가신청이 끝난 공고 → 가벼운 목록으로 (판정 없이). 걸러내기는 closedExtra에서
        setSearch({ q, ids, closed: filters.showInactive ? r.notices : [], loading: false, error: null });
      })
      .catch((e) => alive && setSearch({ q, ids: null, closed: [], loading: false, error: e }));
    return () => {
      alive = false;
    };
  }, [q, filters.showInactive]);
  const searching = Boolean(q);

  const update = (patch) =>
    setFilters((f) => {
      const next = { ...f, ...patch };
      save("filters", next);
      return next;
    });

  const filterOut = useMemo(() => (n) => {
    if (!filters.showMismatch && n.judgment.overall === "MISMATCH") return true;
    if (filters.favoritesOnly && !favorites.includes(n.id)) return true;
    if (filters.judgment !== "ALL" && (n.judgment.overall !== filters.judgment || isInactive(n))) return true;
    if (!filters.showInactive && isInactive(n)) return true;
    return false;
  }, [filters, favorites]);
  // 검색 결과에 든 공고 (검색 중이 아니면 전체)
  const matched = useMemo(
    () => (searching && search.ids ? notices.filter((n) => search.ids.has(n.id)) : searching ? [] : notices),
    [notices, searching, search.ids],
  );
  const hiddenByFilter = searching ? matched.filter(filterOut).length : 0;
  const closedExtra = useMemo(() => {
    const known = new Set(notices.map((n) => n.id));
    return search.closed.filter((r) => !known.has(r.notice_no));
  }, [search.closed, notices]);

  const visible = useMemo(() => {
    const list = matched.filter((n) => {
      if (!filters.showMismatch && n.judgment.overall === "MISMATCH") return false;
      if (filters.favoritesOnly && !favorites.includes(n.id)) return false;
      // "참가 가능"/"확인 필요" 필터에는 마감·취소된 공고를 넣지 않는다 (참가할 수 없으므로)
      if (filters.judgment !== "ALL" && (n.judgment.overall !== filters.judgment || isInactive(n))) return false;
      if (!filters.showInactive && isInactive(n)) return false;
      return true;
    });
    const byDeadline = (a, b) => new Date(nextDeadline(a).iso) - new Date(nextDeadline(b).iso);
    const byPrice = (a, b) => (b.estimatedPrice ?? 0) - (a.estimatedPrice ?? 0);
    // 종료(마감·취소) 공고는 항상 아래로
    return list.sort((a, b) => isInactive(a) - isInactive(b) || (filters.sort === "price" ? byPrice(a, b) : byDeadline(a, b)));
  }, [matched, filters, favorites]);

  const stats = useMemo(() => {
    const active = notices.filter((n) => !isInactive(n) && n.judgment.overall !== "MISMATCH");
    const weekLater = Date.now() + 7 * 86400000;
    return {
      active: active.length,
      review: active.filter((n) => n.judgment.overall === "REVIEW").length,
      thisWeek: active.filter((n) => new Date(nextDeadline(n).iso).getTime() <= weekLater).length,
      favorites: notices.filter((n) => favorites.includes(n.id)).length, // 목록에 실제로 있는 관심공고만
    };
  }, [notices, favorites]);


  return (
    <div className="dashboard">
      <aside ref={sideRef} className={`dash-side${sideTooTall ? " too-tall" : ""}`}>
        <div data-reveal className="company-slot" style={companyHeight ? { minHeight: companyHeight } : undefined}><CompanyCard company={company} /></div>
        <div data-reveal style={{ "--i": 1 }}><FavoritesCalendar notices={notices.filter((n) => favorites.includes(n.id))} /></div>
      </aside>

      <section className="dash-main" aria-labelledby="list-title">
        {/* 이 표시가 화면 위로 사라지면 = 제목~필터 영역이 상단에 고정된 상태 */}
        <div ref={sentinelRef} className="sticky-sentinel" aria-hidden="true" />
        <div ref={stickyRef} className={`dash-sticky${stuck ? " is-stuck" : ""}`}>
        <div className="dash-head" data-reveal>
          <div>
            <h1 id="list-title" className="page-title">맞춤 입찰 공고</h1>
            <p className="page-sub">
              우리 업체의 면허·자격·지역과 한전 SRM 공고 조건을 비교한 결과입니다.
              {lastRun?.finished_at && <span className="muted"> 마지막 수집 {fullDateTime(lastRun.finished_at)}</span>}
            </p>
          </div>
        </div>

        {company && !error && (
          <ul className="stat-row" aria-label="요약" data-reveal style={{ "--i": 1 }}>
            <Stat value={loading ? null : stats.active} label="진행 중 추천" />
            <Stat value={loading ? null : stats.review} label="확인 필요" tone="warn" />
            <Stat value={loading ? null : stats.thisWeek} label="7일 내 마감" tone="hot" />
            <Stat value={stats.favorites} label="관심공고" />
          </ul>
        )}

        {company && !error && (
          <div className="toolbar card" role="group" aria-label="필터와 정렬" data-reveal style={{ "--i": 2 }}>
            <div className="seg-light" role="group" aria-label="판정 상태">
              {[
                ["ALL", "전체"],
                ["MATCH", "참가 가능"],
                ["REVIEW", "확인 필요"],
              ].map(([value, label]) => (
                <button key={value} type="button" aria-pressed={filters.judgment === value} onClick={() => update({ judgment: value })}>
                  {label}
                </button>
              ))}
            </div>
            <label className="chip-check">
              <input type="checkbox" checked={filters.favoritesOnly} onChange={(e) => update({ favoritesOnly: e.target.checked })} />
              ★ 관심공고만
            </label>
            <label className="chip-check">
              <input type="checkbox" checked={filters.showInactive} onChange={(e) => update({ showInactive: e.target.checked })} />
              마감건 포함
            </label>
            <label className="chip-check">
              <input type="checkbox" checked={filters.showMismatch} onChange={(e) => update({ showMismatch: e.target.checked })} />
              조건불일치 포함
            </label>
            <div className="sort">
              <Select
                size="sm"
                ariaLabel="정렬"
                value={filters.sort}
                onChange={(v) => update({ sort: v })}
                options={[
                  { value: "deadline", label: "마감 임박순" },
                  { value: "price", label: "금액 높은순" },
                ]}
              />
            </div>
          </div>
        )}

        {company && !error && (
          <div className="dash-search" role="search" data-reveal>
            <svg className="dash-search-icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
              <circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" />
            </svg>
            <input
              type="search"
              value={text}
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") setParams(text.trim() ? { q: text.trim() } : {}, { replace: true });
                if (e.key === "Escape") setText("");
              }}
              placeholder="공고명 또는 공고번호로 검색 (예: 배전, 감리, E012608055)"
              aria-label="공고 검색"
            />
            {searching && (
              <span className="dash-search-meta" aria-live="polite">
                {search.loading ? "찾는 중…" : search.error ? "검색 실패" : `${visible.length}건${closedExtra.length ? ` · 마감 ${closedExtra.length}건` : ""}`}
              </span>
            )}
            {text && <button type="button" className="dash-search-clear" onClick={() => setText("")} aria-label="검색어 지우기">✕</button>}
          </div>
        )}
        </div>

        {error ? (
          <div className="card">
            <EmptyState title="공고를 불러오지 못했습니다" action={<button type="button" className="btn btn-primary" onClick={reload}>다시 시도</button>}>
              {error.message} 잠시 후 다시 시도해 주세요. 문제가 계속되면 관리자에게 알려 주세요.
            </EmptyState>
          </div>
        ) : !loading && !user ? (
          <div className="card">
            <EmptyState title="로그인하고 업체정보를 등록해 주세요" action={<><Link to="/login" className="btn btn-primary">로그인 / 회원가입</Link><TestLoginButton /></>}>
              보유 면허·제한경쟁입찰자격·본점 소재지를 등록하면 진행 중인 한전 SRM 공고 {allNotices.length}건 중 참가할 수 있는 공고를 추천해 드립니다.
            </EmptyState>
          </div>
        ) : !loading && !company ? (
          <div className="card">
            <EmptyState
              title="업체정보를 먼저 등록해 주세요"
              action={<Link to="/company" className="btn btn-primary">업체정보 등록하기</Link>}
            >
              보유 면허·제한경쟁입찰자격·본점 소재지를 등록하면 참가할 수 있는 한전 SRM 공고를 추천해 드립니다.
            </EmptyState>
          </div>
        ) : loading ? (
          <ul className="notice-list" aria-busy="true" aria-label="공고 불러오는 중">
            {[0, 1, 2].map((i) => (
              <li key={i} className="card card-pad">
                <div className="skeleton" style={{ height: 18, width: "40%" }} />
                <div className="skeleton" style={{ height: 22, width: "75%", marginTop: 10 }} />
                <div className="skeleton" style={{ height: 14, width: "55%", marginTop: 10 }} />
              </li>
            ))}
          </ul>
        ) : searching && search.error ? (
          <div className="card">
            <EmptyState title="검색하지 못했습니다" action={<button type="button" className="btn" onClick={() => setParams({ q }, { replace: true })}>다시 시도</button>}>
              {search.error.message}
            </EmptyState>
          </div>
        ) : searching && (search.loading || search.q !== q) ? (
          <div className="card card-pad"><div className="skeleton" style={{ height: 80 }} /></div>
        ) : visible.length === 0 && !closedExtra.length ? (
          <div className="card">
            {searching ? (
              <EmptyState
                title={`‘${q}’ 검색 결과가 없습니다`}
                action={
                  <div className="form-actions" style={{ justifyContent: "center" }}>
                    {hiddenByFilter > 0 && <button type="button" className="btn btn-primary" onClick={() => update(DEFAULT_FILTERS)}>필터 풀고 {hiddenByFilter}건 보기</button>}
                    {!filters.showInactive && <button type="button" className="btn" onClick={() => update({ showInactive: true })}>마감 공고까지 찾기</button>}
                  </div>
                }
              >
                {hiddenByFilter > 0 ? `검색어에 맞는 공고 ${hiddenByFilter}건이 지금 필터(조건불일치·관심공고 등) 때문에 숨겨져 있습니다.` : "공고명이나 공고번호의 일부로 다시 검색해 보세요."}
              </EmptyState>
            ) : notices.length === 0 ? (
              <EmptyState title="지금은 조건에 맞는 공고가 없습니다" action={<Link to="/company" className="btn">업체정보 확인하기</Link>}>
                새 공고가 올라오면 이곳에 표시됩니다. 등록한 면허와 지역이 맞는지 확인해 보세요.
              </EmptyState>
            ) : (
              <EmptyState title="필터 조건에 맞는 공고가 없습니다" action={<button type="button" className="btn" onClick={() => update(DEFAULT_FILTERS)}>필터 초기화</button>} />
            )}
          </div>
        ) : (
          <>
            {searching && hiddenByFilter > 0 && (
              <p className="small muted">
                ‘{q}’ 검색 결과 중 {hiddenByFilter}건은 지금 필터 때문에 숨겨져 있습니다.{" "}
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => update(DEFAULT_FILTERS)}>필터 풀기</button>
              </p>
            )}
            {visible.length > 0 && <div data-reveal><Disclaimer /></div>}
            <ul className="notice-list">
              {visible.map((n, i) => (
                <li key={n.id} data-reveal style={{ "--i": Math.min(i, 4) }}>
                  <NoticeCard notice={n} highlight={searching ? q : undefined} />
                </li>
              ))}
            </ul>
            {searching && closedExtra.length > 0 && (
              <section className="card card-pad closed-results" aria-labelledby="closed-title">
                <h2 id="closed-title">참가신청이 끝난 공고 <span className="muted small num">{closedExtra.length}건</span></h2>
                <p className="xsmall muted">참가가 끝난 공고라 업체 조건 판정 없이 보여 줍니다. 눌러서 상세를 볼 수 있습니다.</p>
                {closedExtra.slice(0, closedShown).map((r) => (
                  <Link key={r.notice_no} to={`/notices/${r.notice_no}`} className="closed-row">
                    <span className="ct"><Highlight text={r.title} query={q} /></span>
                    <span className="xsmall muted num"><Highlight text={r.notice_no} query={q} /> · 투찰 마감 {shortDateTime(isoOf(r.bid_end_at))}</span>
                    <span className="small"><Money value={r.presumed_price} showKorean={false} /></span>
                  </Link>
                ))}
                {closedExtra.length > closedShown && (
                  <button type="button" className="btn btn-sm" onClick={() => setClosedShown((n) => n + 20)}>
                    더 보기 ({closedExtra.length - closedShown}건 남음)
                  </button>
                )}
              </section>
            )}
          </>
        )}

      </section>
    </div>
  );
}
