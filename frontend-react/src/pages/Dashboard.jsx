import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import CompanyCard from "../components/dashboard/CompanyCard.jsx";
import FavoritesCalendar from "../components/dashboard/FavoritesCalendar.jsx";
import NoticeCard from "../components/dashboard/NoticeCard.jsx";
import Select from "../components/Select.jsx";
import { Disclaimer, EmptyState, isInactive } from "../components/ui.jsx";
import { nextDeadline } from "../lib/notice.js";
import { load, save } from "../lib/storage.js";
import { useCountUp } from "../lib/motion.js";
import { useAppState } from "../state/AppState.jsx";
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

const DEFAULT_FILTERS = { favoritesOnly: false, judgment: "ALL", hideInactive: false, showMismatch: false, sort: "deadline" };

export default function Dashboard() {
  const { company, notices, loading, error, reload, favorites } = useAppState();
  const [filters, setFilters] = useState(() => ({ ...DEFAULT_FILTERS, ...load("filters", {}) }));
  const sentinelRef = useRef(null);
  const [stuck, setStuck] = useState(false);
  useEffect(() => {
    // 표시가 상단 헤더(64px) 위로 올라가면 고정 상태로 본다
    // (값이 같으면 React가 다시 그리지 않으므로 스크롤마다 호출해도 가볍다)
    const check = () => {
      const el = sentinelRef.current;
      if (el) setStuck(el.getBoundingClientRect().top < 65);
    };
    check();
    window.addEventListener("scroll", check, { passive: true });
    window.addEventListener("resize", check);
    return () => {
      window.removeEventListener("scroll", check);
      window.removeEventListener("resize", check);
    };
  }, []);

  const update = (patch) =>
    setFilters((f) => {
      const next = { ...f, ...patch };
      save("filters", next);
      return next;
    });

  const visible = useMemo(() => {
    const list = notices.filter((n) => {
      if (!filters.showMismatch && n.judgment.overall === "MISMATCH") return false;
      if (filters.favoritesOnly && !favorites.includes(n.id)) return false;
      // "참가 가능"/"확인 필요" 필터에는 마감·취소된 공고를 넣지 않는다 (참가할 수 없으므로)
      if (filters.judgment !== "ALL" && (n.judgment.overall !== filters.judgment || isInactive(n))) return false;
      if (filters.hideInactive && isInactive(n)) return false;
      return true;
    });
    const byDeadline = (a, b) => new Date(nextDeadline(a).iso) - new Date(nextDeadline(b).iso);
    const byPrice = (a, b) => b.estimatedPrice - a.estimatedPrice;
    // 종료(마감·취소) 공고는 항상 아래로
    return list.sort((a, b) => isInactive(a) - isInactive(b) || (filters.sort === "price" ? byPrice(a, b) : byDeadline(a, b)));
  }, [notices, filters, favorites]);

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

  const mismatchCount = notices.filter((n) => n.judgment.overall === "MISMATCH").length;

  return (
    <div className="dashboard">
      <aside className="dash-side">
        <div data-reveal><CompanyCard company={company} /></div>
        <div data-reveal style={{ "--i": 1 }}><FavoritesCalendar notices={notices.filter((n) => favorites.includes(n.id))} /></div>
      </aside>

      <section className="dash-main" aria-labelledby="list-title">
        {/* 이 표시가 화면 위로 사라지면 = 제목~필터 영역이 상단에 고정된 상태 */}
        <div ref={sentinelRef} className="sticky-sentinel" aria-hidden="true" />
        <div className={`dash-sticky${stuck ? " is-stuck" : ""}`}>
        <div className="dash-head" data-reveal>
          <div>
            <h1 id="list-title" className="page-title">맞춤 입찰 공고</h1>
            <p className="page-sub">우리 업체의 면허·자격·지역과 SRM 공고 조건을 비교한 결과입니다.</p>
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
            <label className="chip-check">
              <input type="checkbox" checked={filters.favoritesOnly} onChange={(e) => update({ favoritesOnly: e.target.checked })} />
              ★ 관심공고만
            </label>
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
              <input type="checkbox" checked={filters.hideInactive} onChange={(e) => update({ hideInactive: e.target.checked })} />
              마감·취소 숨기기
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
        </div>

        {!company ? (
          <div className="card">
            <EmptyState
              title="업체정보를 먼저 등록해 주세요"
              action={<Link to="/company" className="btn btn-primary">업체정보 등록하기</Link>}
            >
              보유 면허·제한경쟁입찰자격·본점 소재지를 등록하면 참가할 수 있는 한전 SRM 공고를 추천해 드립니다.
            </EmptyState>
          </div>
        ) : error ? (
          <div className="card">
            <EmptyState title="공고를 불러오지 못했습니다" action={<button type="button" className="btn btn-primary" onClick={reload}>다시 시도</button>}>
              잠시 후 다시 시도해 주세요. 문제가 계속되면 관리자에게 알려 주세요.
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
        ) : visible.length === 0 ? (
          <div className="card">
            {notices.length === 0 ? (
              <EmptyState title="지금은 조건에 맞는 공고가 없습니다" action={<Link to="/company" className="btn">업체정보 확인하기</Link>}>
                새 공고가 올라오면 이곳에 표시됩니다. 등록한 면허와 지역이 맞는지 확인해 보세요.
              </EmptyState>
            ) : (
              <EmptyState title="필터 조건에 맞는 공고가 없습니다" action={<button type="button" className="btn" onClick={() => update(DEFAULT_FILTERS)}>필터 초기화</button>} />
            )}
          </div>
        ) : (
          <>
            <div data-reveal><Disclaimer /></div>
            <ul className="notice-list">
              {visible.map((n, i) => (
                <li key={n.id} data-reveal style={{ "--i": Math.min(i, 4) }}>
                  <NoticeCard notice={n} />
                </li>
              ))}
            </ul>
          </>
        )}

        {company && !loading && !error && mismatchCount > 0 && (
          <label className="mismatch-toggle small muted">
            <input type="checkbox" checked={filters.showMismatch} onChange={(e) => update({ showMismatch: e.target.checked })} />
            조건 불일치로 제외된 공고 {mismatchCount}건도 보기
          </label>
        )}
      </section>
    </div>
  );
}
