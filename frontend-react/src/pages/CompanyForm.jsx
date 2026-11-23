import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import LICENSES from "../data/licenses.json";
import QUALIFICATIONS from "../data/qualifications.json";
import REGIONS from "../data/regions.json";
import { NoticeBox, Term } from "../components/ui.jsx";
import Select from "../components/Select.jsx";
import { MOCK_COMPANY } from "../mocks/company.js";
import { useAppState } from "../state/AppState.jsx";
import "./CompanyForm.css";

const REGION_OPTIONS = REGIONS.map((g) => ({ value: g.areaLimit, label: g.name, hint: `${g.areaLimit} · ${g.districts.length}곳` }));

const EMPTY = { name: "", bizNo: "", address: "", email: "", areaLimit: "", subAreaCode: "", licenses: [], qualifications: [] };

function validate(form) {
  const errors = {};
  if (!form.name.trim()) errors.name = "업체명을 입력하세요.";
  if (form.bizNo && !/^\d{10}$/.test(form.bizNo.replace(/-/g, ""))) errors.bizNo = "숫자 10자리로 입력하세요. (하이픈은 자동으로 제거됩니다)";
  if (!form.address.trim()) errors.address = "본점 주소를 입력하세요.";
  if (form.email && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(form.email)) errors.email = "이메일 형식을 확인하세요.";
  if (!form.areaLimit) errors.areaLimit = "SRM 지역그룹을 선택하세요.";
  else if (!form.subAreaCode) errors.subAreaCode = "시·군·구를 선택하세요.";
  form.licenses.forEach((l) => {
    if (!l.regNo.trim()) errors[`lic-${l.code}-regNo`] = "면허(등록)번호를 입력하세요.";
    if (!l.regDate) errors[`lic-${l.code}-regDate`] = "실제 등록일자를 입력하세요.";
  });
  return errors;
}

export default function CompanyForm() {
  const { company, saveCompany } = useAppState();
  const navigate = useNavigate();
  const [form, setForm] = useState(() => ({ ...EMPTY, ...(company ?? {}) }));
  const [errors, setErrors] = useState({});
  const [query, setQuery] = useState("");
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  const group = REGIONS.find((g) => g.areaLimit === form.areaLimit);
  const results = useMemo(() => {
    const q = query.trim();
    if (!q) return [];
    const held = new Set(form.licenses.map((l) => l.code));
    const Q = q.toUpperCase();
    // 정확히 같은 이름·코드 → 이름이 검색어로 시작 → 이름에 포함 순으로 보여준다
    const rank = (l) => (l.name === q || l.code.toUpperCase() === Q ? 0 : l.name.startsWith(q) ? 1 : 2);
    return LICENSES.filter((l) => !held.has(l.code) && (l.name.includes(q) || l.code.toUpperCase() === Q))
      .sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length)
      .slice(0, 12);
  }, [query, form.licenses]);

  const addLicense = (lic) => {
    set({ licenses: [...form.licenses, { ...lic, regNo: "", regDate: "" }] });
    setQuery("");
  };
  const updateLicense = (code, patch) => set({ licenses: form.licenses.map((l) => (l.code === code ? { ...l, ...patch } : l)) });
  const toggleQualification = (q) =>
    set({
      qualifications: form.qualifications.some((x) => x.code === q.code)
        ? form.qualifications.filter((x) => x.code !== q.code)
        : [...form.qualifications, q],
    });

  const submit = (e) => {
    e.preventDefault();
    const found = validate(form);
    setErrors(found);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => document.querySelector('[aria-invalid="true"]')?.focus());
      return;
    }
    const district = group.districts.find((d) => d.code === form.subAreaCode);
    saveCompany({ ...form, bizNo: form.bizNo.replace(/-/g, ""), areaName: group.name, subAreaName: district?.name ?? "" });
    navigate("/", { state: { saved: true } });
  };

  const err = (key) => (errors[key] ? <p className="field-error" id={`${key}-err`}>{errors[key]}</p> : null);
  const inv = (key) => ({ "aria-invalid": Boolean(errors[key]), "aria-describedby": errors[key] ? `${key}-err` : undefined });

  return (
    <form className="company-form" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">업체정보</h1>
        <p className="page-sub">입력한 면허·자격·지역으로 참가할 수 있는 공고를 찾아 드립니다.</p>
      </div>

      <NoticeBox tone="info">
        입력한 정보는 <strong>업체가 직접 제공한 정보</strong>로, 입찰메이트가 진위를 검증하지 않습니다. 사업자번호가 같아도 다른 계정의 정보와 연결되지 않습니다.
      </NoticeBox>

      <section className="card card-pad form-section" aria-labelledby="sec-basic" data-reveal>
        <h2 id="sec-basic" className="card-title">기본 정보</h2>
        <div className="form-grid">
          <label className="field">
            <span className="field-label">업체명<span className="req">*</span></span>
            <input className="input" value={form.name} onChange={(e) => set({ name: e.target.value })} {...inv("name")} />
            {err("name")}
          </label>
          <label className="field">
            <span className="field-label">사업자등록번호</span>
            <input className="input num" inputMode="numeric" placeholder="숫자 10자리 (선택)" value={form.bizNo} onChange={(e) => set({ bizNo: e.target.value })} {...inv("bizNo")} />
            {err("bizNo")}
          </label>
          <label className="field span-2">
            <span className="field-label">본점 전체 주소<span className="req">*</span></span>
            <input className="input" placeholder="등기부등본에 적힌 본점 주소" value={form.address} onChange={(e) => set({ address: e.target.value })} {...inv("address")} />
            <span className="field-hint">주소와 아래에서 고른 지역이 다르면 지역 판정이 &lsquo;확인 필요&rsquo;로 표시됩니다.</span>
            {err("address")}
          </label>
          <label className="field span-2">
            <span className="field-label">알림 받을 이메일</span>
            <input className="input" type="email" placeholder="선택 · 맞춤 공고 알림 기능 준비 중" value={form.email} onChange={(e) => set({ email: e.target.value })} {...inv("email")} />
            {err("email")}
          </label>
        </div>
      </section>

      <section className="card card-pad form-section" aria-labelledby="sec-region" data-reveal>
        <h2 id="sec-region" className="card-title">본점 소재지 (SRM 지역)</h2>
        <div className="form-grid">
          <div className="field">
            <label className="field-label" htmlFor="areaLimit">지역그룹<span className="req">*</span></label>
            <Select
              id="areaLimit"
              value={form.areaLimit}
              onChange={(v) => set({ areaLimit: v, subAreaCode: "" })}
              options={REGION_OPTIONS}
              invalid={Boolean(errors.areaLimit)}
              describedBy={errors.areaLimit ? "areaLimit-err" : "areaLimit-hint"}
            />
            <span id="areaLimit-hint" className="field-hint">전남·광주 소재 업체는 &lsquo;전남광주&rsquo;를 선택하세요.</span>
            {err("areaLimit")}
          </div>
          <div className="field">
            <label className="field-label" htmlFor="subAreaCode">시·군·구<span className="req">*</span></label>
            <Select
              id="subAreaCode"
              value={form.subAreaCode}
              onChange={(v) => set({ subAreaCode: v })}
              options={group ? group.districts.map((d) => ({ value: d.code, label: d.name })) : []}
              placeholder={group ? "선택하세요" : "지역그룹을 먼저 고르세요"}
              disabled={!group}
              invalid={Boolean(errors.subAreaCode)}
              describedBy={errors.subAreaCode ? "subAreaCode-err" : undefined}
            />
            {err("subAreaCode")}
          </div>
        </div>
      </section>

      <section className="card card-pad form-section" aria-labelledby="sec-license" data-reveal>
        <h2 id="sec-license" className="card-title">보유 면허</h2>
        <div className="field license-search">
          <label className="field-label" htmlFor="lic-search">면허 검색</label>
          <input id="lic-search" className="input" placeholder="예: 전기공사업, A06" value={query} onChange={(e) => setQuery(e.target.value)} autoComplete="off" />
          {query && (
            <ul className="search-results" data-lenis-prevent role="listbox" aria-label="면허 검색 결과">
              {results.length ? (
                results.map((l) => (
                  <li key={l.code}>
                    <button type="button" className="search-item" onClick={() => addLicense(l)}>
                      <span>{l.name}</span>
                      <span className="muted xsmall num">{l.code}</span>
                    </button>
                  </li>
                ))
              ) : (
                <li className="muted small" style={{ padding: 10 }}>검색 결과가 없습니다.</li>
              )}
            </ul>
          )}
        </div>

        {form.licenses.length === 0 ? (
          <p className="small muted">등록한 면허가 없습니다. 위에서 검색해 추가하세요.</p>
        ) : (
          <ul className="license-rows">
            {form.licenses.map((l) => (
              <li key={l.code} className="license-row">
                <div className="license-name">
                  <strong>{l.name}</strong>
                  <span className="muted xsmall num">{l.code}</span>
                </div>
                <label className="field">
                  <span className="field-label small">면허(등록)번호<span className="req">*</span></span>
                  <input className="input" value={l.regNo} onChange={(e) => updateLicense(l.code, { regNo: e.target.value })} {...inv(`lic-${l.code}-regNo`)} />
                  {err(`lic-${l.code}-regNo`)}
                </label>
                <label className="field">
                  <span className="field-label small">실제 등록일자<span className="req">*</span></span>
                  <input className="input" type="date" value={l.regDate} onChange={(e) => updateLicense(l.code, { regDate: e.target.value })} {...inv(`lic-${l.code}-regDate`)} />
                  {err(`lic-${l.code}-regDate`)}
                </label>
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => set({ licenses: form.licenses.filter((x) => x.code !== l.code) })} aria-label={`${l.name} 삭제`}>
                  삭제
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="card card-pad form-section" aria-labelledby="sec-qual" data-reveal>
        <h2 id="sec-qual" className="card-title"><Term>제한경쟁입찰자격</Term></h2>
        <p className="small muted">보유한 자격을 모두 선택하세요. 등록번호와 일자는 받지 않습니다.</p>
        <div className="qual-grid">
          {QUALIFICATIONS.map((q) => (
            <label key={q.code} className="chip-check">
              <input type="checkbox" checked={form.qualifications.some((x) => x.code === q.code)} onChange={() => toggleQualification(q)} />
              {q.name}
            </label>
          ))}
        </div>
        <p className="xsmall muted">주력분야는 입력받지 않습니다. 주력분야 제한이 있는 공고는 &lsquo;확인 필요&rsquo;로 표시됩니다.</p>
      </section>

      <div className="form-actions">
        {import.meta.env.DEV && (
          <button type="button" className="btn btn-ghost" onClick={() => setForm({ ...EMPTY, ...MOCK_COMPANY })}>가상 업체로 채우기</button>
        )}
        <span className="spacer" />
        <Link to="/" className="btn">취소</Link>
        <button type="submit" className="btn btn-primary">저장하고 추천 공고 보기</button>
      </div>
      <p className="xsmall muted">초안 안내: 추천·판정 결과는 목업 데이터라 업체정보를 바꿔도 아직 달라지지 않습니다. 저장한 정보는 이 브라우저에만 남습니다.</p>
    </form>
  );
}
