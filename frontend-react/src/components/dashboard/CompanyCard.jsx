import { useState } from "react";
import { Link } from "react-router-dom";
import { Term } from "../ui.jsx";

const SHOW = 3; // 이보다 많으면 접어 두고 '+N 펼치기' (아래 관심공고 달력이 너무 내려가지 않게)

/** 면허·자격 배지 목록: 많으면 앞 3개만 보이고 펼치기/접기 */
function Tags({ items, label }) {
  const [open, setOpen] = useState(false);
  if (!items.length) return <span className="muted">없음</span>;
  const extra = items.length - SHOW;
  const shown = open || extra <= 0 ? items : items.slice(0, SHOW);
  return (
    <>
      {shown.map((it) => (
        <span key={it.code} className="badge badge-outline" title={`${it.name} (${it.code})`}>{it.name}</span>
      ))}
      {extra > 0 && (
        <button type="button" className="tag-more" onClick={() => setOpen((o) => !o)} aria-expanded={open} aria-label={open ? `${label} 접기` : `${label} ${extra}개 더 보기`}>
          {open ? "접기" : `+${extra}개 펼치기`}
        </button>
      )}
    </>
  );
}

export default function CompanyCard({ company }) {
  if (!company) {
    return (
      <section className="card card-pad" aria-labelledby="company-title">
        <h2 id="company-title" className="card-title">우리 업체</h2>
        <p className="small muted" style={{ margin: "8px 0 12px" }}>아직 등록된 업체정보가 없습니다.</p>
        <Link to="/company" className="btn btn-primary btn-sm">업체정보 등록</Link>
      </section>
    );
  }
  return (
    <section className="card card-pad company-card" aria-labelledby="company-title">
      <div className="card-head">
        <h2 id="company-title" className="card-title">{company.name}</h2>
        <Link to="/company" className="btn btn-sm">정보 수정</Link>
      </div>
      <dl className="kv">
        <div>
          <dt>본점 소재지</dt>
          <dd>
            {company.areaName} · {company.subAreaName}
            <span className="muted xsmall"> ({company.areaLimit}_{company.subAreaCode})</span>
          </dd>
        </div>
        <div>
          <dt>보유 면허 {company.licenses.length > 0 && <span className="muted xsmall num">{company.licenses.length}</span>}</dt>
          <dd className="tags"><Tags items={company.licenses} label="보유 면허" /></dd>
        </div>
        <div>
          <dt><Term>제한경쟁입찰자격</Term> {company.qualifications.length > 0 && <span className="muted xsmall num">{company.qualifications.length}</span>}</dt>
          <dd className="tags"><Tags items={company.qualifications} label="제한경쟁입찰자격" /></dd>
        </div>
      </dl>
      <p className="xsmall muted company-note">업체가 직접 입력한 정보이며 별도로 검증되지 않았습니다.</p>
    </section>
  );
}
