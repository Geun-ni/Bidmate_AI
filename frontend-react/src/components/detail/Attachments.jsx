import { useRef, useState } from "react";
import { NoticeBox } from "../ui.jsx";
import "./Attachments.css";

const SRM_URL = "https://srm.kepco.net";

/**
 * FUNC-018: 공고문·적격심사기준 등 원본 문서 열람(미리보기)·다운로드.
 * 지금은 목업 텍스트 파일(public/mock-docs)을 보여준다. 실제로는 백엔드가 준 파일 주소를 쓴다.
 */
export default function Attachments({ notice }) {
  const dialogRef = useRef(null);
  const [preview, setPreview] = useState({ file: null, text: "", state: "idle" });
  const missing = Object.entries(notice.documents).filter(([, d]) => d.status !== "OK");

  const open = async (file) => {
    setPreview({ file, text: "", state: "loading" });
    dialogRef.current?.showModal();
    try {
      const res = await fetch(file.url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setPreview({ file, text: await res.text(), state: "done" });
    } catch {
      setPreview({ file, text: "", state: "error" });
    }
  };

  return (
    <section className="card card-pad" aria-labelledby="att-title" data-reveal>
      <h2 id="att-title" className="section-title">원본 문서</h2>
      <p className="section-desc">요약은 아래 문서에서 추출했습니다. 판단이 필요한 내용은 원문으로 꼭 확인하세요.</p>

      <ul className="att-list">
        {notice.attachments.map((f) => (
          <li key={f.name}>
            <span className="att-kind">{f.kind}</span>
            <div className="att-main">
              <span className="att-name">{f.name}</span>
              <span className="xsmall muted">{f.size} · 출처: {f.origin}</span>
            </div>
            <div className="att-actions">
              {f.url ? (
                <>
                  <button type="button" className="btn btn-sm" onClick={() => open(f)}>미리보기</button>
                  <a className="btn btn-sm" href={f.url} download={`${f.name}.txt`}>다운로드</a>
                </>
              ) : (
                <a className="btn btn-sm" href={SRM_URL} target="_blank" rel="noreferrer">SRM에서 보기 ↗</a>
              )}
            </div>
          </li>
        ))}
        {missing.map(([key]) => (
          <li key={key} className="att-missing">
            <span className="att-kind">{key === "criteria" ? "적격심사기준" : "공고문"}</span>
            <div className="att-main">
              <span className="att-name">미확보</span>
              <span className="xsmall muted">공식 API 첨부(최대 5개)에서 찾지 못했습니다.</span>
            </div>
            <div className="att-actions">
              <a className="btn btn-sm" href={SRM_URL} target="_blank" rel="noreferrer">SRM에서 찾기 ↗</a>
            </div>
          </li>
        ))}
      </ul>

      <dialog ref={dialogRef} className="att-dialog" aria-labelledby="att-dialog-title" onClose={() => setPreview({ file: null, text: "", state: "idle" })}>
        <div className="att-dialog-head">
          <div>
            <p className="xsmall muted">{preview.file?.kind} 미리보기 · 추출 텍스트</p>
            <h3 id="att-dialog-title">{preview.file?.name}</h3>
          </div>
          <button type="button" className="btn btn-sm" onClick={() => dialogRef.current?.close()} aria-label="미리보기 닫기">닫기</button>
        </div>
        <div className="att-dialog-body" data-lenis-prevent>
          {preview.state === "loading" && <div className="skeleton" style={{ height: 240 }} />}
          {preview.state === "error" && <NoticeBox tone="danger" icon="!">문서를 불러오지 못했습니다. SRM에서 원문을 확인해 주세요.</NoticeBox>}
          {preview.state === "done" && <pre>{preview.text}</pre>}
        </div>
        <p className="xsmall muted att-dialog-foot">HWP·PDF 원본의 표·이미지·수식은 추출 텍스트에서 빠질 수 있습니다. 판단은 원본 파일 기준입니다.</p>
      </dialog>
    </section>
  );
}
