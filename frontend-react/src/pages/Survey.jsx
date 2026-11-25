import { useState } from "react";
import { Link } from "react-router-dom";
import { NoticeBox } from "../components/ui.jsx";
import { feedbackContext, markSurveyDone, sendFeedback, surveyDone } from "../lib/feedback.js";
import { useAppState } from "../state/AppState.jsx";
import "../components/Feedback.css";

/**
 * C. 베타 설문 (/feedback). 테스트를 마칠 때 한 번 작성한다.
 * 모든 테스터에게 같은 질문을 해 비교·집계할 수 있게 한다. 응답은 POST /api/feedback { kind: "survey", answers }.
 */
const LIKERT = [
  { key: "find_easy", q: "맞춤 공고를 찾기 쉬웠나요?" },
  { key: "judgment_trust", q: "참가 가능 여부(1차 판정)를 믿을 수 있었나요?" },
  { key: "score_useful", q: "점수 계산이 실제 입찰 준비에 도움이 됐나요?" },
  { key: "screen_clear", q: "화면과 용어가 이해하기 쉬웠나요?" },
  { key: "qna_useful", q: "AI 질문 답변이 도움이 됐나요?", optional: true },
];
const LIKERT_LABELS = ["전혀 아니다", "아니다", "보통", "그렇다", "매우 그렇다"];
const FEATURES = ["맞춤 공고 추천", "참가자격 판정", "점수 계산", "관심공고·달력", "검색·필터", "AI 질문", "원본 문서 다운로드"];
const ROLES = ["공사 업체", "용역(감리·설계) 업체", "공사·용역 모두", "기타"];

export default function Survey() {
  const { user, company, testMode } = useAppState();
  const [a, setA] = useState({ role: "", likert: {}, best: [], pain: "", wish: "", nps: null });
  const [state, setState] = useState({ busy: false, done: surveyDone(), error: "" });
  const set = (patch) => {
    setA((x) => ({ ...x, ...patch }));
    setState((s) => (s.error ? { ...s, error: "" } : s)); // 답을 고치면 경고는 지운다
  };
  const required = LIKERT.filter((l) => !l.optional);
  const answered = required.filter((l) => a.likert[l.key]).length;
  const ok = answered === required.length && a.nps !== null;

  const submit = async (e) => {
    e.preventDefault();
    if (!ok) {
      setState((s) => ({ ...s, error: "별표(*) 문항에 모두 답해 주세요." }));
      return;
    }
    setState({ busy: true, done: false, error: "" });
    try {
      await sendFeedback({
        kind: "survey",
        answers: {
          role: a.role || null,
          likert: a.likert,
          best_features: a.best,
          pain: a.pain.trim() || null,
          wish: a.wish.trim() || null,
          nps: a.nps,
        },
        context: feedbackContext({ user, company, testMode }),
      });
      markSurveyDone();
      setState({ busy: false, done: true, error: "" });
    } catch (err) {
      setState({ busy: false, done: false, error: err.message });
    }
  };

  if (state.done) {
    return (
      <div className="survey">
        <div className="card card-pad fb-done">
          <p className="fb-done-title">설문에 참여해 주셔서 고맙습니다!</p>
          <p className="small muted">보내 주신 의견은 정식 서비스 준비에 반영하겠습니다. 사용하다 불편한 점이 생기면 언제든 「의견 보내기」로 알려 주세요.</p>
          <div className="form-actions" style={{ justifyContent: "center" }}>
            <Link to="/" className="btn btn-primary">추천 공고로 돌아가기</Link>
            <button type="button" className="btn btn-ghost" onClick={() => setState({ busy: false, done: false, error: "" })}>다시 작성하기</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <form className="survey" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">베타 설문</h1>
        <p className="page-sub">입찰메이트를 써 보신 경험을 알려 주세요. 약 3분 걸립니다. <span className="req">*</span>는 필수 문항입니다.</p>
      </div>

      <section className="card card-pad survey-sec">
        <h2 className="card-title">1. 어떤 업체에서 쓰셨나요?</h2>
        <div className="fp-items">
          {ROLES.map((r) => (
            <button key={r} type="button" className={`fp-preset${a.role === r ? " on" : ""}`} aria-pressed={a.role === r} onClick={() => set({ role: a.role === r ? "" : r })}>{r}</button>
          ))}
        </div>
      </section>

      <section className="card card-pad survey-sec">
        <h2 className="card-title">2. 기능별 만족도</h2>
        <ul className="likert">
          {LIKERT.map((l) => (
            <li key={l.key}>
              <p className="likert-q">{l.q}{!l.optional && <span className="req">*</span>}{l.optional && <span className="xsmall muted"> (써 보신 경우만)</span>}</p>
              <div className="likert-scale" role="radiogroup" aria-label={l.q}>
                {LIKERT_LABELS.map((label, i) => {
                  const v = i + 1;
                  const on = a.likert[l.key] === v;
                  return (
                    <label key={v} className={`likert-opt${on ? " on" : ""}`}>
                      <input type="radio" name={`lk-${l.key}`} checked={on} onChange={() => set({ likert: { ...a.likert, [l.key]: v } })} />
                      <span className="num">{v}</span>
                      <span className="xsmall">{label}</span>
                    </label>
                  );
                })}
              </div>
            </li>
          ))}
        </ul>
      </section>

      <section className="card card-pad survey-sec">
        <h2 className="card-title">3. 가장 유용했던 기능 (여러 개 선택)</h2>
        <div className="fp-items">
          {FEATURES.map((f) => {
            const on = a.best.includes(f);
            return (
              <button key={f} type="button" className={`fp-preset${on ? " on" : ""}`} aria-pressed={on} onClick={() => set({ best: on ? a.best.filter((x) => x !== f) : [...a.best, f] })}>{f}</button>
            );
          })}
        </div>
      </section>

      <section className="card card-pad survey-sec">
        <h2 className="card-title">4. 자유 의견</h2>
        <label className="field">
          <span className="field-label">가장 불편했던 점</span>
          <textarea className="input fb-text" rows={3} maxLength={1000} value={a.pain} onChange={(e) => set({ pain: e.target.value })} placeholder="예: 점수 계산 항목이 많아 입력이 오래 걸렸어요." />
        </label>
        <label className="field">
          <span className="field-label">추가됐으면 하는 기능</span>
          <textarea className="input fb-text" rows={3} maxLength={1000} value={a.wish} onChange={(e) => set({ wish: e.target.value })} placeholder="예: 마감 하루 전에 알림을 받고 싶어요." />
        </label>
      </section>

      <section className="card card-pad survey-sec">
        <h2 className="card-title">5. 동료 업체에 입찰메이트를 추천하시겠어요?<span className="req">*</span></h2>
        <div className="nps" role="radiogroup" aria-label="추천 의향 (0~10)">
          {Array.from({ length: 11 }, (_, v) => (
            <label key={v} className={`nps-opt${a.nps === v ? " on" : ""}`}>
              <input type="radio" name="nps" checked={a.nps === v} onChange={() => set({ nps: v })} />
              <span className="num">{v}</span>
            </label>
          ))}
        </div>
        <div className="nps-ends xsmall muted"><span>전혀 추천하지 않음</span><span>매우 추천함</span></div>
      </section>


      {state.error && <NoticeBox tone="danger" icon="!">{state.error}</NoticeBox>}

      <div className="form-actions">
        <span className="small muted num">필수 문항 {answered + (a.nps !== null ? 1 : 0)} / {required.length + 1}</span>
        <span className="spacer" />
        <Link to="/" className="btn">나중에 하기</Link>
        <button type="submit" className="btn btn-primary" disabled={state.busy}>{state.busy ? "보내는 중…" : "설문 제출"}</button>
      </div>
    </form>
  );
}
