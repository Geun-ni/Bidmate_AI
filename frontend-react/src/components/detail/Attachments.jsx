import { useState } from "react";
import { downloadAttachment } from "../../api/client.js";
import "./Attachments.css";

const SRM_URL = "https://srm.kepco.net";

/**
 * FUNC-018: 수집한 원본 문서(공고문·적격심사기준) 목록.
 * 파일 이름이나 [다운로드]를 누르면 서버에 저장된 원본을 받는다 (GET /api/attachments/<id>).
 * 서버가 아직 파일 받기를 지원하지 않으면(404) 그 줄에 안내와 SRM 바로가기를 보여 준다.
 */
export default function Attachments({ notice }) {
  const missing = Object.entries(notice.documents).filter(([, d]) => d.status !== "OK");
  const [state, setState] = useState({}); // 첨부 id → { busy, error }

  const download = async (f) => {
    setState((s) => ({ ...s, [f.id]: { busy: true } }));
    try {
      await downloadAttachment(f.id, f.name);
      setState((s) => ({ ...s, [f.id]: {} }));
    } catch (e) {
      const error = e.status === 404 ? "서버에서 아직 파일 받기를 지원하지 않습니다. SRM에서 받아 주세요." : `받지 못했습니다: ${e.message}`;
      setState((s) => ({ ...s, [f.id]: { error } }));
    }
  };

  return (
    <section className="card card-pad" aria-labelledby="att-title" data-reveal>
      <h2 id="att-title" className="section-title">원본 문서</h2>
      <p className="section-desc">배점·일정은 아래 문서에서 추출했습니다. 판단이 필요한 내용은 SRM 원문으로 꼭 확인하세요.</p>

      <ul className="att-list">
        {notice.attachments.map((f) => (
          <li key={f.id} className={f.ok ? undefined : "att-missing"}>
            <span className="att-kind">{f.kind}</span>
            <div className="att-main">
              {f.ok ? (
                <button type="button" className="att-name att-link" onClick={() => download(f)} title="눌러서 원본 파일 받기">{f.name}</button>
              ) : (
                <span className="att-name">{f.name}</span>
              )}
              <span className="xsmall muted">
                {f.ok ? `${f.ext?.toUpperCase() ?? ""} · ${f.size} · 출처: ${f.origin}` : `수집 실패 · 출처: ${f.origin}`}
              </span>
              {state[f.id]?.error && <span className="xsmall att-error" role="status">{state[f.id].error}</span>}
            </div>
            <div className="att-actions">
              {f.ok && (
                <button type="button" className="btn btn-sm btn-primary" onClick={() => download(f)} disabled={state[f.id]?.busy}>
                  {state[f.id]?.busy ? "받는 중…" : "⬇ 다운로드"}
                </button>
              )}
              <a className="btn btn-sm" href={SRM_URL} target="_blank" rel="noreferrer">SRM ↗</a>
            </div>
          </li>
        ))}
        {missing.map(([key, d]) => (
          <li key={key} className="att-missing">
            <span className="att-kind">{key === "criteria" ? "적격심사기준" : "공고문"}</span>
            <div className="att-main">
              <span className="att-name">{d.status === "PENDING" ? "수집 전" : "미확보"}</span>
              <span className="xsmall muted">
                {d.status === "PENDING" ? "아직 첨부파일을 수집하지 않았습니다." : "공식 API 첨부와 SRM 추가 조회에서 찾지 못했습니다."}
              </span>
            </div>
            <div className="att-actions">
              <a className="btn btn-sm" href={SRM_URL} target="_blank" rel="noreferrer">SRM에서 찾기 ↗</a>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}
