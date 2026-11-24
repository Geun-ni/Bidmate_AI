import { Link } from "react-router-dom";
import { Term } from "../ui.jsx";

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
          <dt>보유 면허</dt>
          <dd className="tags">
            {company.licenses.length ? company.licenses.map((l) => <span key={l.code} className="badge badge-outline">{l.name}</span>) : <span className="muted">없음</span>}
          </dd>
        </div>
        <div>
          <dt><Term>제한경쟁입찰자격</Term></dt>
          <dd className="tags">
            {company.qualifications.length ? company.qualifications.map((q) => <span key={q.code} className="badge badge-outline">{q.name}</span>) : <span className="muted">없음</span>}
          </dd>
        </div>
      </dl>
      <p className="xsmall muted company-note">업체가 직접 입력한 정보이며 별도로 검증되지 않았습니다.</p>
    </section>
  );
}
