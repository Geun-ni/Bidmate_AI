import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import * as api from "../api/client.js";
import { NoticeBox, Term } from "../components/ui.jsx";
import Select from "../components/Select.jsx";
import { toCompanyPayload } from "../lib/adapt.js";
import { openPostcode } from "../lib/postcode.js";
import { useAppState } from "../state/AppState.jsx";
import TestLoginButton from "../components/TestLoginButton.jsx";
import "./CompanyForm.css";

const EMPTY = { name: "", bizNo: "", address: "", addressDetail: "", juso: null, licenses: [], qualifications: [] };

function validate(form, { isNew }) {
  const errors = {};
  if (!form.name.trim()) errors.name = "업체명을 입력하세요.";
  if (form.bizNo && !/^\d{10}$/.test(form.bizNo.replace(/-/g, ""))) errors.bizNo = "숫자 10자리로 입력하세요. (하이픈은 자동으로 제거됩니다)";
  if (isNew && !form.juso) errors.address = "주소 검색으로 본점 소재지를 선택하세요.";
  form.licenses.forEach((l) => {
    if (!l.regNo.trim()) errors[`lic-${l.code}-regNo`] = "면허(등록)번호를 입력하세요.";
    if (!/^\d{4}-\d{2}-\d{2}$/.test(l.regDate)) errors[`lic-${l.code}-regDate`] = "실제 등록일자를 입력하세요.";
  });
  return errors;
}

export default function CompanyForm() {
  const { user, company, companies, joinRequests, companyLoading, refreshCompany, selectCompany, testMode, saveTestCompany } = useAppState();
  const navigate = useNavigate();
  const isNew = !company;
  const [form, setForm] = useState(EMPTY);
  const [region, setRegion] = useState(null); // 주소 검색 결과 → SRM 지역 매칭 결과
  const [errors, setErrors] = useState({});
  const [serverError, setServerError] = useState("");
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [qualOptions, setQualOptions] = useState([]);
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // 저장된 업체를 칸에 채운다 (업체를 바꾸면 다시)
  useEffect(() => {
    setForm(company ? { ...EMPTY, ...company, juso: null, addressDetail: "" } : EMPTY);
    setRegion(null);
    setErrors({});
    setServerError("");
  }, [company]);

  useEffect(() => {
    api.fetchQualifications().then((r) => setQualOptions(r.qualifications)).catch(() => setQualOptions([]));
  }, []);

  // 면허 검색 (입력이 멈추면 서버에 묻는다)
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      setResults([]);
      return undefined;
    }
    let alive = true;
    const t = setTimeout(() => {
      api
        .searchLicenses(q)
        .then((r) => alive && setResults(r.licenses.filter((l) => !form.licenses.some((x) => x.code === l.license_code)).slice(0, 12)))
        .catch(() => alive && setResults([]));
    }, 250);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [query, form.licenses]);

  if (user === undefined || (user && companyLoading && !company)) {
    return <div className="card card-pad"><div className="skeleton" style={{ height: 200 }} /></div>;
  }
  if (!user) {
    return (
      <div className="card card-pad company-form">
        <h1 className="page-title">업체정보</h1>
        <p className="page-sub">업체정보는 로그인한 사용자만 등록·수정할 수 있습니다.</p>
        <div className="form-actions">
          <Link to="/login" state={{ from: "/company" }} className="btn btn-primary">로그인 / 회원가입</Link>
        </div>
        <TestLoginButton to="/company" />
      </div>
    );
  }

  const searchAddress = async () => {
    setServerError("");
    try {
      const picked = await openPostcode();
      if (!picked) return;
      set({ address: picked.address, juso: picked.juso, addressDetail: "" });
      setErrors((e) => ({ ...e, address: undefined }));
      const m = await api.matchRegion(picked.juso.siNm, picked.juso.sggNm).catch(() => null);
      setRegion(m);
    } catch (e) {
      setServerError(e.message);
    }
  };

  const addLicense = (lic) => {
    set({ licenses: [...form.licenses, { code: lic.license_code, name: lic.license_name, regNo: "", regDate: "" }] });
    setQuery("");
  };
  const updateLicense = (code, patch) => set({ licenses: form.licenses.map((l) => (l.code === code ? { ...l, ...patch } : l)) });
  const toggleQualification = (q) =>
    set({
      qualifications: form.qualifications.some((x) => x.code === q.qualification_code)
        ? form.qualifications.filter((x) => x.code !== q.qualification_code)
        : [...form.qualifications, { code: q.qualification_code, name: q.qualification_name }],
    });

  const submit = async (e) => {
    e.preventDefault();
    setServerError("");
    const found = validate(form, { isNew });
    setErrors(found);
    if (Object.keys(found).length) {
      requestAnimationFrame(() => document.querySelector('[aria-invalid="true"]')?.focus());
      return;
    }
    setSaving(true);
    if (testMode) {
      // 테스트 모드: 서버에 저장하지 않고 이 브라우저에만 남긴다
      try {
        let geo = { areaLimit: company?.areaLimit, areaName: company?.areaName, subAreaCode: company?.subAreaCode, subAreaName: company?.subAreaName };
        if (form.juso) {
          if (!region?.matched) throw new Error("SRM 지역과 매칭되지 않는 주소입니다. 다른 주소로 다시 검색해 주세요.");
          const { regions } = await api.fetchRegions();
          const g = regions.find((x) => x.area_limit === region.area_limit);
          const d = g?.districts.find((x) => x.region_code === region.sub_area_code);
          geo = { areaLimit: region.area_limit, areaName: g?.area_limit_name ?? region.area_limit, subAreaCode: region.sub_area_code, subAreaName: d?.region_name ?? "" };
        }
        const detail = form.addressDetail?.trim();
        saveTestCompany({
          id: "test-company",
          name: form.name.trim(),
          bizNo: form.bizNo.replace(/\D/g, ""),
          address: form.juso ? (detail ? `${form.address} ${detail}` : form.address) : form.address,
          ...geo,
          licenses: form.licenses,
          qualifications: form.qualifications,
        });
        navigate("/", { state: { saved: true } });
      } catch (err) {
        setServerError(err.message);
      } finally {
        setSaving(false);
      }
      return;
    }
    try {
      const body = toCompanyPayload(form, { includeAddress: Boolean(form.juso) });
      const saved = isNew ? await api.createCompany(body) : await api.updateCompany(company.id, body);
      await refreshCompany(saved.company_id);
      navigate("/", { state: { saved: true } });
    } catch (err) {
      setServerError(err.message);
    } finally {
      setSaving(false);
    }
  };

  const err = (key) => (errors[key] ? <p className="field-error" id={`${key}-err`}>{errors[key]}</p> : null);
  const inv = (key) => ({ "aria-invalid": Boolean(errors[key]), "aria-describedby": errors[key] ? `${key}-err` : undefined });
  const myRole = companies.find((c) => c.company_id === company?.id)?.role;

  return (
    <form className="company-form" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">업체정보</h1>
        <p className="page-sub">입력한 면허·자격·지역으로 참가할 수 있는 공고를 찾아 드립니다.</p>
      </div>

      {companies.length > 1 && (
        <div className="field">
          <label className="field-label" htmlFor="company-pick">수정할 업체</label>
          <Select
            id="company-pick"
            value={company?.id ?? ""}
            onChange={(v) => selectCompany(v)}
            options={companies.map((c) => ({ value: c.company_id, label: c.company_name }))}
          />
        </div>
      )}

      {joinRequests.filter((r) => r.membership_status === "PENDING").map((r) => (
        <NoticeBox key={r.company_id} tone="warn" icon="!">
          <strong>{r.company_name}</strong> 합류 요청이 승인 대기 중입니다. 업체 소유자가 승인하면 업체정보를 함께 쓸 수 있습니다.
        </NoticeBox>
      ))}

      <NoticeBox tone="info">
        입력한 정보는 <strong>업체가 직접 제공한 정보</strong>로, 입찰메이트가 진위를 검증하지 않습니다.
      </NoticeBox>
      {testMode && (
        <NoticeBox tone="warn" icon="🧪">
          <strong>테스트 모드</strong> — 업체정보를 서버가 아니라 이 브라우저에만 저장합니다. 판정·추천은 실제 공고로 계산됩니다.
        </NoticeBox>
      )}

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
            <span className="field-hint">동료가 가입할 때 이 번호로 업체를 찾아 합류를 요청합니다.</span>
            {err("bizNo")}
          </label>
        </div>
      </section>

      <section className="card card-pad form-section" aria-labelledby="sec-region" data-reveal>
        <h2 id="sec-region" className="card-title">본점 소재지</h2>
        <div className="form-grid">
          <div className="field span-2">
            <span className="field-label">본점 주소<span className="req">*</span></span>
            <div className="join-row">
              <input className="input" readOnly value={form.address} placeholder="주소 검색 버튼을 눌러 선택하세요" onClick={searchAddress} {...inv("address")} />
              <button type="button" className="btn" onClick={searchAddress}>{form.address ? "주소 변경" : "주소 검색"}</button>
            </div>
            <span className="field-hint">등기부등본의 본점 주소를 검색해 고르세요. SRM 지역(광역·시군구)은 고른 주소로 자동으로 정해집니다.</span>
            {err("address")}
          </div>
          {form.juso && (
            <label className="field span-2">
              <span className="field-label">상세 주소</span>
              <input className="input" placeholder="동·호수 등 (선택)" value={form.addressDetail} onChange={(e) => set({ addressDetail: e.target.value })} />
            </label>
          )}
          <div className="field span-2">
            <span className="field-label">SRM 지역</span>
            {form.juso ? (
              region?.matched ? (
                <p className="small"><span className="badge badge-ok">매칭됨</span> {form.juso.siNm} {form.juso.sggNm} → <span className="num">{region.area_limit}_{region.sub_area_code}</span></p>
              ) : region ? (
                <p className="small"><span className="badge badge-danger">매칭 실패</span> SRM 지역 기준정보에 없는 시군구입니다. 다른 주소로 다시 검색하거나 관리자에게 알려 주세요.</p>
              ) : (
                <p className="small muted">확인 중…</p>
              )
            ) : company ? (
              <p className="small">{company.areaName} · {company.subAreaName} <span className="muted xsmall num">({company.areaLimit}_{company.subAreaCode})</span></p>
            ) : (
              <p className="small muted">주소를 고르면 표시됩니다.</p>
            )}
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
                  <li key={l.license_code}>
                    <button type="button" className="search-item" onClick={() => addLicense(l)}>
                      <span>{l.license_name}</span>
                      <span className="muted xsmall num">{l.license_code}</span>
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
          {qualOptions.map((q) => (
            <label key={q.qualification_code} className="chip-check">
              <input type="checkbox" checked={form.qualifications.some((x) => x.code === q.qualification_code)} onChange={() => toggleQualification(q)} />
              {q.qualification_name}
            </label>
          ))}
        </div>
        <p className="xsmall muted">주력분야는 입력받지 않습니다. 주력분야 제한이 있는 공고는 &lsquo;확인 필요&rsquo;로 표시됩니다.</p>
      </section>

      {company && (myRole === "OWNER" || user.is_admin) && <Members companyId={company.id} />}

      {serverError && <NoticeBox tone="danger" icon="!">{serverError}</NoticeBox>}

      <div className="form-actions">
        <span className="spacer" />
        <Link to="/" className="btn">취소</Link>
        <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? "저장 중…" : "저장하고 추천 공고 보기"}</button>
      </div>
    </form>
  );
}

/** 업체 소유자·관리자: 합류 요청 승인·거절 */
function Members({ companyId }) {
  const [members, setMembers] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.fetchMembers(companyId).then((r) => setMembers(r.members)).catch((e) => setError(e.message));
  }, [companyId]);
  const decide = async (userId, status) => {
    setError("");
    try {
      const r = await api.setMemberStatus(companyId, userId, status);
      setMembers(r.members);
    } catch (e) {
      setError(e.message);
    }
  };
  if (!members || members.length <= 1) return error ? <NoticeBox tone="danger" icon="!">{error}</NoticeBox> : null;
  const LABEL = { ACTIVE: "소속", PENDING: "승인 대기", REJECTED: "거절됨" };
  return (
    <section className="card card-pad form-section" aria-labelledby="sec-members">
      <h2 id="sec-members" className="card-title">업체 구성원</h2>
      <ul className="member-list">
        {members.map((m) => (
          <li key={m.user_id}>
            <span>{m.email}</span>
            <span className="badge badge-outline">{m.role === "OWNER" ? "소유자" : LABEL[m.membership_status]}</span>
            {m.role !== "OWNER" && m.membership_status === "PENDING" && (
              <span className="member-actions">
                <button type="button" className="btn btn-sm btn-primary" onClick={() => decide(m.user_id, "ACTIVE")}>승인</button>
                <button type="button" className="btn btn-sm" onClick={() => decide(m.user_id, "REJECTED")}>거절</button>
              </span>
            )}
          </li>
        ))}
      </ul>
      {error && <NoticeBox tone="danger" icon="!">{error}</NoticeBox>}
    </section>
  );
}
