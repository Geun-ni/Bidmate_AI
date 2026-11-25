import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { feedbackContext, readAttachment, reactedValue, rememberReaction, sendFeedback, surveyDone } from "../lib/feedback.js";
import { useAppState } from "../state/AppState.jsx";
import { NoticeBox } from "./ui.jsx";
import "./Feedback.css";

/* ───────── A. 상시 '의견 보내기' ───────── */

const CATEGORIES = [
  { value: "BUG", label: "오류가 있어요", hint: "화면이 깨지거나, 값이 틀리거나, 동작하지 않아요" },
  { value: "INCONVENIENT", label: "불편해요", hint: "찾기 어렵거나, 이해하기 어렵거나, 손이 많이 가요" },
  { value: "IDEA", label: "이런 기능이 있었으면", hint: "새로 필요한 기능이나 정보" },
  { value: "GOOD", label: "좋아요", hint: "도움이 된 점" },
];

const FeedbackContext = createContext(null);
export const useFeedback = () => useContext(FeedbackContext);

export function FeedbackProvider({ children }) {
  const [open, setOpen] = useState(false);
  const [preset, setPreset] = useState(null); // 다른 곳에서 열 때 미리 정할 유형·내용
  const openFeedback = useCallback((p = null) => {
    setPreset(p);
    setOpen(true);
  }, []);
  return (
    <FeedbackContext.Provider value={{ openFeedback }}>
      {children}
      {open && <FeedbackDialog preset={preset} onClose={() => setOpen(false)} />}
    </FeedbackContext.Provider>
  );
}

function FeedbackDialog({ preset, onClose }) {
  const { user, company, testMode } = useAppState();
  const dialogRef = useRef(null);
  const [category, setCategory] = useState(preset?.category ?? "");
  const [message, setMessage] = useState(preset?.message ?? "");
  const [email, setEmail] = useState(testMode ? "" : user?.email ?? "");
  const [attachment, setAttachment] = useState(null);
  const [state, setState] = useState({ busy: false, done: false, error: "" });
  const context = feedbackContext({ user, company, testMode });

  // 창은 화면에서 빠질 때 함께 사라지므로 따로 닫지 않는다
  // (정리 단계에서 close()를 부르면 개발 모드의 이중 실행 때 'close' 이벤트로 창이 바로 닫혀 버린다)
  useEffect(() => {
    const d = dialogRef.current;
    if (d && !d.open) d.showModal();
  }, []);

  const attach = async (file) => {
    setState((s) => ({ ...s, error: "" }));
    if (!file) return setAttachment(null);
    try {
      setAttachment(await readAttachment(file));
    } catch (e) {
      setState((s) => ({ ...s, error: e.message }));
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    if (!category || !message.trim()) return;
    setState({ busy: true, done: false, error: "" });
    try {
      await sendFeedback({
        kind: "general",
        category,
        message: message.trim(),
        email: email.trim() || null,
        attachment,
        context,
      });
      setState({ busy: false, done: true, error: "" });
    } catch (err) {
      setState({ busy: false, done: false, error: err.message });
    }
  };

  return (
    <dialog ref={dialogRef} className="fb-dialog" aria-labelledby="fb-title" onClose={onClose} onCancel={onClose}>
      <div className="fb-head">
        <div>
          <h2 id="fb-title">의견 보내기</h2>
          <p className="xsmall muted">베타테스트 중입니다. 불편한 점, 틀린 정보, 바라는 기능을 알려 주세요.</p>
        </div>
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => dialogRef.current?.close()} aria-label="닫기">✕</button>
      </div>

      {state.done ? (
        <div className="fb-done">
          <p className="fb-done-title">의견 고맙습니다!</p>
          <p className="small muted">보내 주신 내용은 서비스 개선에 반영하겠습니다.</p>
          {!surveyDone() && (
            <p className="small">
              시간이 되시면 <Link to="/feedback" onClick={() => dialogRef.current?.close()}>베타 설문(약 3분)</Link>에도 참여해 주세요.
            </p>
          )}
          <button type="button" className="btn btn-primary" onClick={() => dialogRef.current?.close()}>닫기</button>
        </div>
      ) : (
        <form className="fb-form" onSubmit={submit}>
          <fieldset className="fb-cats">
            <legend className="field-label">어떤 의견인가요?<span className="req">*</span></legend>
            {CATEGORIES.map((c) => (
              <label key={c.value} className={`fb-cat${category === c.value ? " on" : ""}`}>
                <input type="radio" name="fb-cat" value={c.value} checked={category === c.value} onChange={() => setCategory(c.value)} />
                <span className="fb-cat-label">{c.label}</span>
                <span className="xsmall muted">{c.hint}</span>
              </label>
            ))}
          </fieldset>

          <label className="field">
            <span className="field-label">내용<span className="req">*</span></span>
            <textarea
              className="input fb-text"
              rows={5}
              maxLength={2000}
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="예: 이 공고는 지역 제한이 있는데 '지역 제한 없음'으로 나와요."
              required
            />
            <span className="field-hint num">{message.length} / 2000</span>
          </label>

          <div className="fb-row">
            <label className="field">
              <span className="field-label">스크린샷 (선택)</span>
              <input type="file" accept="image/*" onChange={(e) => attach(e.target.files?.[0] ?? null)} />
              {attachment && <span className="field-hint">{attachment.name} · {Math.round(attachment.size / 1024)}KB</span>}
            </label>
            <label className="field">
              <span className="field-label">답변 받을 이메일 (선택)</span>
              <input className="input" type="email" value={email} onChange={(e) => setEmail(e.target.value)} placeholder="확인이 필요할 때만 연락드려요" />
            </label>
          </div>

          <details className="fb-ctx">
            <summary className="xsmall">함께 보내는 정보 보기</summary>
            <ul className="xsmall muted">
              <li>보고 있던 화면: {context.page}</li>
              {context.notice_no && <li>공고번호: {context.notice_no}</li>}
              <li>화면 크기·브라우저 종류, 보낸 시각{context.company_id ? ", 업체 ID" : ""}</li>
            </ul>
          </details>

          {state.error && <NoticeBox tone="danger" icon="!">{state.error}</NoticeBox>}

          <div className="fb-actions">
            <span className="xsmall muted">입력한 이메일은 의견 확인 목적으로만 씁니다.</span>
            <button type="button" className="btn" onClick={() => dialogRef.current?.close()}>취소</button>
            <button type="submit" className="btn btn-primary" disabled={state.busy || !category || !message.trim()}>
              {state.busy ? "보내는 중…" : "보내기"}
            </button>
          </div>
        </form>
      )}
    </dialog>
  );
}

/* ───────── B. 맥락형 👍👎 ───────── */

/**
 * 결과 옆의 짧은 반응. 👎이면 "무엇이 틀렸나요?" 한 줄을 받는다.
 * target: judgment | score | qna, refId: 공고번호·답변 id 등 (같은 대상에 다시 누르면 바뀐 값으로 보낸다)
 */
export function FeedbackReaction({ target, refId, question, extra, compact = false }) {
  const { user, company, testMode } = useAppState();
  const key = `${target}:${refId}`;
  const [value, setValue] = useState(() => reactedValue(key));
  const [askWhy, setAskWhy] = useState(false);
  const [comment, setComment] = useState("");
  const [state, setState] = useState({ busy: false, sent: false, error: "" });

  const send = async (v, why = null) => {
    setState({ busy: true, sent: false, error: "" });
    try {
      await sendFeedback({
        kind: "reaction",
        target,
        ref_id: refId,
        value: v,
        comment: why,
        extra: extra ?? null,
        context: feedbackContext({ user, company, testMode, noticeId: target !== "qna" ? refId : undefined }),
      });
      rememberReaction(key, v);
      setValue(v);
      setState({ busy: false, sent: true, error: "" });
      setAskWhy(false);
    } catch (e) {
      setState({ busy: false, sent: false, error: e.message });
    }
  };

  const pick = (v) => {
    if (v === "down") {
      setValue("down");
      setAskWhy(true);
      return;
    }
    send("up");
  };

  return (
    <div className={`fb-react${compact ? " compact" : ""}`}>
      {!compact && <span className="xsmall muted">{question}</span>}
      <div className="fb-react-btns" role="group" aria-label={question}>
        <button type="button" className={`fb-thumb${value === "up" ? " on" : ""}`} aria-pressed={value === "up"} onClick={() => pick("up")} disabled={state.busy} title="정확해요">👍</button>
        <button type="button" className={`fb-thumb${value === "down" ? " on" : ""}`} aria-pressed={value === "down"} onClick={() => pick("down")} disabled={state.busy} title="틀렸어요">👎</button>
      </div>
      {state.sent && !askWhy && <span className="xsmall muted">고맙습니다!</span>}
      {askWhy && (
        <form
          className="fb-why"
          onSubmit={(e) => {
            e.preventDefault();
            send("down", comment.trim() || null);
          }}
        >
          <input className="input" value={comment} onChange={(e) => setComment(e.target.value)} placeholder="무엇이 틀렸나요? (선택)" maxLength={300} autoFocus />
          <button type="submit" className="btn btn-sm btn-primary" disabled={state.busy}>보내기</button>
        </form>
      )}
      {state.error && <span className="xsmall fb-err">{state.error}</span>}
    </div>
  );
}
