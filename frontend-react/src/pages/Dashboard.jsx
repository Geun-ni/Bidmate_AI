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
import { surveyDone } from "../lib/feedback.js";
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

const DEFAULT_FILTERS = {
  judgment: "ALL", // ALL | MATCH | REVIEW
  type: "ALL", // ALL | CONSTRUCTION | SERVICE
  sort: "deadline", // deadline | price | priceAsc
  favoritesOnly: false,
  showInactive: false,
  showMismatch: false,
  region: "ALL", // ALL | MINE(우리 지역 한정 공고) | NATIONWIDE(지역 제한 없음)
  area: "", // 보고 싶은 시·도 (SRM 광역 코드, NONE=미분류). 비어 있으면 전체
  myLicense: false, // 우리 면허가 요구 조건에 들어간 공고만
  noRestriction: false, // 면허·자격·지역·주력분야 제한이 하나도 없는 공고만
  priceMin: null, // 추정가격 (원)
  priceMax: null,
};

/** 금액 빠른 선택 (추정가격) */
const EOK = 1e8;
const PRICE_PRESETS = [
  { label: "1억 미만", min: null, max: EOK },
  { label: "1~3억", min: EOK, max: 3 * EOK },
  { label: "3~10억", min: 3 * EOK, max: 10 * EOK },
  { label: "10~50억", min: 10 * EOK, max: 50 * EOK },
  { label: "50억 이상", min: 50 * EOK, max: null },
];
const eokText = (v) => `${Math.round((v / EOK) * 100) / 100}억`;
const priceLabel = (min, max) => (min != null && max != null ? `${eokText(min)}~${eokText(max)}` : min != null ? `${eokText(min)} 이상` : `${eokText(max)} 미만`);

/** 상세 필터(패널 안) 중 기본값에서 바뀐 것들 → 위쪽 요약 칩 */
function activeChips(f) {
  const chips = [];
  if (f.favoritesOnly) chips.push({ key: "favoritesOnly", label: "★ 관심공고만", reset: { favoritesOnly: false } });
  if (f.showInactive) chips.push({ key: "showInactive", label: "마감건 포함", reset: { showInactive: false } });
  if (f.showMismatch) chips.push({ key: "showMismatch", label: "조건불일치 포함", reset: { showMismatch: false } });
  if (f.region === "MINE") chips.push({ key: "region", label: "우리 지역 한정 공고", reset: { region: "ALL" } });
  if (f.region === "NATIONWIDE") chips.push({ key: "region", label: "지역 제한 없음(전국)", reset: { region: "ALL" } });
  if (f.area) chips.push({ key: "area", label: `지역 ${AREA_LABEL[f.area]}`, reset: { area: "" } });
  if (f.myLicense) chips.push({ key: "myLicense", label: "우리 면허 요구 공고", reset: { myLicense: false } });
  if (f.noRestriction) chips.push({ key: "noRestriction", label: "제한 조건 없는 공고", reset: { noRestriction: false } });
  if (f.priceMin != null || f.priceMax != null)
    chips.push({ key: "price", label: `금액 ${priceLabel(f.priceMin, f.priceMax)}`, reset: { priceMin: null, priceMax: null } });
  return chips;
}

/** 공고 지역 고르기: SRM 광역 코드 묶음 (지역 제한 광역 또는 발주 본부로 정한 notice.areas 와 비교) */
/** 공고 지역 고르기 (시·도): SRM 광역 코드. 공고의 notice.areas(지역 제한 광역 또는 발주 본부로 정한 광역)와 비교 */
const AREA_OPTIONS = [
  ["S", "서울"], ["H", "인천"], ["G", "경기"], ["F", "강원"], ["C", "충북"], ["D", "대전"], ["L", "세종"], ["R", "충남"],
  ["J", "전북"], ["I", "광주·전남"], ["T", "대구"], ["K", "경북"], ["B", "부산"], ["Z", "울산"], ["M", "경남"], ["E", "제주"],
  ["NONE", "지역 미분류"],
];
const AREA_LABEL = Object.fromEntries(AREA_OPTIONS);
const inArea = (n, code) => (code === "NONE" ? !n.areas?.length : n.areas?.includes(code));

const isRestrictionFree = (n) => ["license", "qualification", "region", "licenseMain"].every((k) => n.conditions[k]?.status === "제한 없음");

export default function Dashboard() {
  const { user, company, companyLoading, notices: allNotices, lastRun, loading: noticesLoading, error, reload, favorites } = useAppState();
  // 업체 기준 판정 전에는 목록을 보여 주지 않는다 (판정 없는 목록이 추천처럼 보이지 않게)
  const loading = noticesLoading || user === undefined || companyLoading;
  const notices = allNotices;
  const [filters, setFilters] = useState(() => {
    const saved = { ...DEFAULT_FILTERS, ...load("filters", {}) };
    delete saved.areas; // 예전 형식(여러 개 고르기) 정리
    return { ...saved, area: AREA_LABEL[saved.area] ? saved.area : "" };
  });
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
      // 상세 필터 패널을 잠깐 여는 동안에는 다시 재지 않는다 (열고 닫을 때 왼쪽 카드가 늘었다 줄었다 하지 않게)
      if (stickyRef.current.querySelector(".filter-panel")) return undefined;
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
  const noticesRef = useRef(notices);
  noticesRef.current = notices;
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
        // 서버 검색은 공고명·공고번호만 본다 → 지금 목록(진행 중·관심공고)은 발주부서로도 찾는다
        const key = q.toLowerCase();
        noticesRef.current.forEach((n) => n.placeName?.toLowerCase().includes(key) && ids.add(n.id));
        // 지금 목록(진행 중)에 없는 결과 = 참가신청이 끝난 공고 → 가벼운 목록으로 (판정 없이). 걸러내기는 closedExtra에서
        setSearch({ q, ids, closed: filters.showInactive ? r.notices : [], loading: false, error: null });
      })
      .catch((e) => alive && setSearch({ q, ids: null, closed: [], loading: false, error: e }));
    return () => {
      alive = false;
    };
    // 공고 목록을 다 받은 뒤 한 번 더 (발주부서 검색은 목록이 있어야 해서)
  }, [q, filters.showInactive, noticesLoading]);
  const searching = Boolean(q);

  const update = (patch) =>
    setFilters((f) => {
      const next = { ...f, ...patch };
      save("filters", next);
      return next;
    });

  /** 이 공고를 지금 필터가 숨기는지 (모든 필터를 한곳에서 판단) */
  const filterOut = useMemo(() => {
    const held = new Set((company?.licenses ?? []).map((l) => l.code));
    return (n) => {
      if (!filters.showMismatch && n.judgment.overall === "MISMATCH") return true;
      if (filters.favoritesOnly && !favorites.includes(n.id)) return true;
      // "참가 가능"/"확인 필요" 필터에는 마감된 공고를 넣지 않는다 (참가할 수 없으므로)
      if (filters.judgment !== "ALL" && (n.judgment.overall !== filters.judgment || isInactive(n))) return true;
      if (!filters.showInactive && isInactive(n)) return true;
      if (filters.type !== "ALL" && n.type !== filters.type) return true;
      const region = n.conditions.region;
      if (filters.region === "NATIONWIDE" && region?.status !== "제한 없음") return true;
      if (filters.region === "MINE" && !(region?.status === "수집됨" && n.judgment.items.find((i) => i.key === "region")?.status === "MATCH")) return true;
      if (filters.area && !inArea(n, filters.area)) return true;
      if (filters.myLicense && !(n.conditions.license?.status === "수집됨" && n.conditions.license.items.some((i) => held.has(i.code)))) return true;
      if (filters.noRestriction && !isRestrictionFree(n)) return true;
      const price = n.estimatedPrice;
      if (filters.priceMin != null && !(price != null && price >= filters.priceMin)) return true;
      if (filters.priceMax != null && !(price != null && price < filters.priceMax)) return true;
      return false;
    };
  }, [filters, favorites, company]);
  const chips = activeChips(filters);
  const [panelOpen, setPanelOpen] = useState(false);
  // 스크롤해서 상단이 고정되면 상세 필터 패널은 닫는다 (고정 영역이 목록을 너무 가리지 않게)
  useEffect(() => {
    if (stuck) setPanelOpen(false);
  }, [stuck]);
  // 검색 결과에 든 공고 (검색 중이 아니면 전체)
  const matched = useMemo(
    () => (searching && search.ids ? notices.filter((n) => search.ids.has(n.id)) : searching ? [] : notices),
    [notices, searching, search.ids],
  );
  const closedExtra = useMemo(() => {
    const known = new Set(notices.map((n) => n.id));
    return search.closed.filter((r) => !known.has(r.notice_no));
  }, [search.closed, notices]);

  const visible = useMemo(() => {
    const list = matched.filter((n) => !filterOut(n));
    const byDeadline = (a, b) => new Date(nextDeadline(a).iso) - new Date(nextDeadline(b).iso);
    const byPrice = (a, b) => (b.estimatedPrice ?? 0) - (a.estimatedPrice ?? 0);
    // 금액 낮은순: 금액이 없는 공고는 맨 뒤로
    const byPriceAsc = (a, b) => (a.estimatedPrice ?? Infinity) - (b.estimatedPrice ?? Infinity);
    const bySort = { price: byPrice, priceAsc: byPriceAsc, deadline: byDeadline };
    // 종료(마감·취소) 공고는 항상 아래로
    return list.sort((a, b) => isInactive(a) - isInactive(b) || (bySort[filters.sort] ?? byDeadline)(a, b));
  }, [matched, filters, filterOut]);

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
    <>
    <SurveyBanner />
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
          <div className="filter-box" data-reveal style={{ "--i": 2 }}>
            <div className="toolbar card" role="group" aria-label="필터와 정렬">
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
              <div className="seg-light" role="group" aria-label="공사·용역">
                {[
                  ["ALL", "공사+용역"],
                  ["CONSTRUCTION", "공사"],
                  ["SERVICE", "용역"],
                ].map(([value, label]) => (
                  <button key={value} type="button" aria-pressed={filters.type === value} onClick={() => update({ type: value })}>
                    {label}
                  </button>
                ))}
              </div>
              <button
                type="button"
                className={`filter-toggle${panelOpen ? " open" : ""}${chips.length ? " on" : ""}`}
                aria-expanded={panelOpen}
                aria-controls="filter-panel"
                onClick={() => setPanelOpen((o) => !o)}
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 6h16M7 12h10M10 18h4" /></svg>
                상세 필터{chips.length > 0 && <span className="filter-count num">{chips.length}</span>}
              </button>
              <div className="sort">
                <Select
                  size="sm"
                  ariaLabel="정렬"
                  value={filters.sort}
                  onChange={(v) => update({ sort: v })}
                  options={[
                    { value: "deadline", label: "마감 임박순" },
                    { value: "price", label: "금액 높은순" },
                    { value: "priceAsc", label: "금액 낮은순" },
                  ]}
                />
              </div>
            </div>

            {panelOpen && <FilterPanel filters={filters} update={update} onClose={() => setPanelOpen(false)} />}

            {!panelOpen && chips.length > 0 && (
              <div className="filter-chips" aria-label="적용된 상세 필터">
                {chips.map((c) => (
                  <button key={c.key} type="button" className="filter-chip" onClick={() => update(c.reset)} aria-label={`${c.label} 필터 해제`}>
                    {c.label} <span aria-hidden="true">✕</span>
                  </button>
                ))}
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => update({ ...activeChips(filters).reduce((o, c) => ({ ...o, ...c.reset }), {}) })}>모두 해제</button>
              </div>
            )}
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
              placeholder="공고명·공고번호·발주부서로 검색 (예: 배전, E012608055, 경기본부)"
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
                    {!filters.showInactive && <button type="button" className="btn" onClick={() => update({ showInactive: true })}>마감 공고까지 찾기</button>}
                  </div>
                }
              >
                공고명이나 공고번호의 일부로 다시 검색해 보세요.
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
    </>
  );
}

/** 상세 필터 패널: 보기 범위 · 지역 · 면허·제한 조건 · 금액 */
function FilterPanel({ filters, update, onClose }) {
  const [min, setMin] = useState(filters.priceMin != null ? String(filters.priceMin / EOK) : "");
  const [max, setMax] = useState(filters.priceMax != null ? String(filters.priceMax / EOK) : "");
  useEffect(() => {
    setMin(filters.priceMin != null ? String(filters.priceMin / EOK) : "");
    setMax(filters.priceMax != null ? String(filters.priceMax / EOK) : "");
  }, [filters.priceMin, filters.priceMax]);
  const applyRange = () => {
    const toWon = (t) => (t.trim() === "" || Number.isNaN(Number(t)) ? null : Math.round(Number(t) * EOK));
    let a = toWon(min);
    let b = toWon(max);
    if (a != null && b != null && a > b) [a, b] = [b, a];
    update({ priceMin: a, priceMax: b });
  };
  const presetOn = (p) => filters.priceMin === p.min && filters.priceMax === p.max;
  const noPrice = filters.priceMin == null && filters.priceMax == null;
  const check = (key, label, hint) => (
    <label className="chip-check" title={hint}>
      <input type="checkbox" checked={filters[key]} onChange={(e) => update({ [key]: e.target.checked })} />
      {label}
    </label>
  );
  return (
    <div id="filter-panel" className="filter-panel card" role="group" aria-label="상세 필터">
      <div className="fp-row">
        <span className="fp-label">보기 범위</span>
        <div className="fp-items">
          {check("favoritesOnly", "★ 관심공고만")}
          {check("showInactive", "마감건 포함", "참가신청이 끝난 관심공고·검색 결과도 함께")}
          {check("showMismatch", "조건불일치 포함", "우리 업체 조건과 맞지 않아 기본으로 숨긴 공고도 함께")}
        </div>
      </div>
      <div className="fp-row">
        <span className="fp-label">지역</span>
        <div className="fp-items">
          <div className="seg-light" role="group" aria-label="지역 제한">
            {[
              ["ALL", "전체"],
              ["MINE", "우리 지역 한정"],
              ["NATIONWIDE", "지역 제한 없음(전국)"],
            ].map(([value, label]) => (
              <button key={value} type="button" aria-pressed={filters.region === value} onClick={() => update({ region: value })}>{label}</button>
            ))}
          </div>
          <div className="fp-area" title="지역 제한이 있으면 그 지역, 없으면 발주 지역본부 기준(추정). 건설본부·본사 발주 등은 '지역 미분류'.">
            <Select
              size="sm"
              ariaLabel="공고 지역 (시·도)"
              value={filters.area}
              onChange={(v) => update({ area: v })}
              placeholder="시·도 전체"
              options={[{ value: "", label: "시·도 전체" }, ...AREA_OPTIONS.map(([value, label]) => ({ value, label }))]}
            />
          </div>
        </div>
      </div>
      <div className="fp-row">
        <span className="fp-label">조건</span>
        <div className="fp-items">
          {check("myLicense", "우리 면허가 요구되는 공고만", "면허 제한에 우리 업체 보유 면허가 들어간 공고")}
          {check("noRestriction", "제한 조건이 하나도 없는 공고만", "면허·자격·지역·주력분야 제한이 모두 없는 공고")}
        </div>
      </div>
      <div className="fp-row">
        <span className="fp-label">추정가격</span>
        <div className="fp-items">
          <button type="button" className={`fp-preset${noPrice ? " on" : ""}`} onClick={() => update({ priceMin: null, priceMax: null })}>전체</button>
          {PRICE_PRESETS.map((p) => (
            <button key={p.label} type="button" className={`fp-preset${presetOn(p) ? " on" : ""}`} onClick={() => update({ priceMin: p.min, priceMax: p.max })}>{p.label}</button>
          ))}
          <span className="fp-range">
            <input className="input num" inputMode="decimal" value={min} onChange={(e) => setMin(e.target.value)} onBlur={applyRange} onKeyDown={(e) => e.key === "Enter" && applyRange()} placeholder="최소" aria-label="최소 금액(억)" />
            <span className="muted small">~</span>
            <input className="input num" inputMode="decimal" value={max} onChange={(e) => setMax(e.target.value)} onBlur={applyRange} onKeyDown={(e) => e.key === "Enter" && applyRange()} placeholder="최대" aria-label="최대 금액(억)" />
            <span className="muted small">억</span>
          </span>
        </div>
      </div>
      <div className="fp-foot">
        <button
          type="button"
          className="btn btn-ghost btn-sm"
          onClick={() => update({ favoritesOnly: false, showInactive: false, showMismatch: false, region: "ALL", area: "", myLicense: false, noRestriction: false, priceMin: null, priceMax: null })}
        >
          상세 필터 초기화
        </button>
        <button type="button" className="btn btn-primary btn-sm" onClick={onClose}>닫기</button>
      </div>
    </div>
  );
}

/**
 * 베타 설문 안내 띠 (추천 공고 화면 맨 위). 닫기 버튼 없이 늘 보인다.
 * 설문을 제출한 브라우저에서만 보이지 않는다.
 */
function SurveyBanner() {
  if (surveyDone()) return null;
  return (
    <div className="survey-banner" role="region" aria-label="베타 설문 안내">
      <span className="survey-banner-icon" aria-hidden="true">🧪</span>
      <p>
        <strong>베타테스트 중입니다.</strong> 써 보신 소감을 알려 주시면 정식 서비스에 반영할게요.
      </p>
      <Link to="/feedback" className="btn btn-sm btn-primary">설문 참여 (약 3분)</Link>
    </div>
  );
}
