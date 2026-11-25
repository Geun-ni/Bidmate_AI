import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { askQna, fetchEvaluation, fetchNotice, fetchNoticeSchedule } from "../api/client.js";
import { answer as localAnswer } from "../lib/qna.js";
import { FeedbackReaction } from "./Feedback.jsx";
import { load, save } from "../lib/storage.js";
import { useAppState } from "../state/AppState.jsx";
import "./QnaPanel.css";

/**
 * AI 질문 패널 (Q&A).
 * - 오른쪽 하단 고정 버튼 → 오른쪽에서 열리는 패널 (휴대폰은 전체 화면)
 * - 공고 상세에서 열면 그 공고를 질문 기준으로 붙인다 (notice_no를 함께 보냄)
 * - 처음 열 때 책임 고지에 동의해야 질문할 수 있다 (브라우저에 기억)
 * - 답변: 백엔드 POST /api/qna (LLM)가 있으면 그 답을, 없으면(404) 화면이 가진 데이터로 규칙 기반 답(lib/qna.js)
 */

const QnaContext = createContext(null);
export const useQna = () => useContext(QnaContext);

const DISCLAIMER =
  "AI 답변은 수집된 데이터를 바탕으로 자동 생성되며 정확하지 않을 수 있습니다. 답변을 근거로 한 입찰 참가·투찰 등의 판단과 그 결과에 대한 책임은 질문자 본인에게 있습니다. 최종 확인은 반드시 공고 원문과 발주기관(한전 SRM)을 통해 해 주세요.";

const SUGGEST_NOTICE = ["우리 업체가 이 공고에 참가할 수 있어?", "적격심사 통과 기준점수는?", "이 공고 일정 정리해줘", "A값이 얼마야?"];
const SUGGEST_GENERAL = ["이번 주에 마감되는 공고 알려줘", "우리 업체가 참가 가능한 공고는?", "적격심사가 뭐야?", "A값이 뭐야?"];
const HISTORY_LIMIT = 6; // 이전 대화는 최근 6개만 함께 보낸다
let qnaApiMissing = false; // 백엔드 질문 API가 없다고 확인되면 이번 접속 동안은 바로 규칙 기반으로 답한다

/** 지금 주소가 공고 상세면 그 공고번호 */
function noticeFromPath(pathname) {
  const m = pathname.match(/^\/notices\/([^/?#]+)/);
  return m ? decodeURIComponent(m[1]) : null;
}

export function QnaProvider({ children }) {
  const location = useLocation();
  const [open, setOpen] = useState(false);
  const [pinned, setPinned] = useState(null); // 질문 기준 공고 (null이면 전체)
  const [messages, setMessages] = useState([]); // { id, role: user|assistant, text, sources?, kind?: answer|pending|unavailable|error }
  const [consent, setConsent] = useState(() => load("qnaConsent", false));
  const pageNotice = noticeFromPath(location.pathname);

  // 공고 상세로 옮기면 그 공고를 기준으로, 목록으로 가면 기준을 푼다 (패널이 열려 있어도 따라간다)
  useEffect(() => setPinned(pageNotice), [pageNotice]);

  const openQna = useCallback((noticeId) => {
    if (noticeId !== undefined) setPinned(noticeId);
    setOpen(true);
  }, []);

  const agree = useCallback(() => {
    setConsent(true);
    save("qnaConsent", true);
  }, []);

  const value = useMemo(
    () => ({ open, setOpen, openQna, pinned, setPinned, messages, setMessages, consent, agree }),
    [open, openQna, pinned, messages, consent, agree],
  );
  return <QnaContext.Provider value={value}>{children}</QnaContext.Provider>;
}

/** 오른쪽 하단 고정 버튼 + 패널 */
export default function QnaPanel() {
  const { open, setOpen, openQna, pinned, setPinned, messages, setMessages, consent, agree } = useQna();
  const { company, testMode, notices, withJudgment } = useAppState();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [checked, setChecked] = useState(false);
  const listRef = useRef(null);
  const inputRef = useRef(null);
  const fabRef = useRef(null);
  const pinnedTitle = pinned ? notices.find((n) => n.id === pinned)?.title : null;

  // 새 메시지가 오면 맨 아래로
  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, open]);

  // 열리면 입력창에 커서, Esc로 닫기
  useEffect(() => {
    if (!open) return undefined;
    if (consent) requestAnimationFrame(() => inputRef.current?.focus());
    const onKey = (e) => {
      if (e.key === "Escape") {
        setOpen(false);
        fabRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, consent, setOpen]);

  const send = async (question) => {
    const q = question.trim();
    if (!q || busy) return;
    const id = Date.now();
    const history = messages
      .filter((m) => m.kind === "answer" || m.kind === "local" || m.role === "user")
      .slice(-HISTORY_LIMIT)
      .map((m) => ({ role: m.role, text: m.text }));
    setMessages((ms) => [...ms, { id, role: "user", text: q, notice: pinned }, { id: id + 1, role: "assistant", kind: "pending", text: "", question: q }]);
    setText("");
    setBusy(true);
    let reply = null;
    if (!qnaApiMissing) {
      try {
        const r = await askQna({
          question: q,
          notice_no: pinned ?? undefined,
          company_id: testMode ? undefined : company?.id, // 테스트 업체는 서버에 없다
          history,
        });
        reply = { kind: "answer", text: r.answer ?? "(빈 답변)", sources: r.sources ?? [] };
      } catch (e) {
        if (e.status === 404) qnaApiMissing = true;
        else reply = { kind: "error", text: `답변을 받지 못했습니다: ${e.message}`, retry: q };
      }
    }
    if (!reply) {
      // LLM이 없으면 규칙 기반 답변 (공고·배점·일정 데이터)
      try {
        let notice = pinned ? notices.find((n) => n.id === pinned) ?? null : null;
        if (pinned && !notice) notice = withJudgment(await fetchNotice(pinned));
        const r = await localAnswer(q, {
          notice,
          notices,
          company,
          loadEvaluation: fetchEvaluation,
          loadSchedule: fetchNoticeSchedule,
        });
        reply = { kind: r.help ? "help" : "local", text: r.text, sources: r.sources };
      } catch (e) {
        reply = { kind: "error", text: `답변을 만들지 못했습니다: ${e.message}`, retry: q };
      }
    }
    setMessages((ms) => ms.map((m) => (m.id === id + 1 ? { ...m, ...reply } : m)));
    setBusy(false);
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  const onKeyDown = (e) => {
    // Enter 보내기, Shift+Enter 줄바꿈 (한글 조합 중 Enter는 무시)
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send(text);
    }
  };

  const suggestions = pinned ? SUGGEST_NOTICE : SUGGEST_GENERAL;

  return (
    <>
      {!open && (
        <button ref={fabRef} type="button" className="qna-fab" onClick={() => openQna()} aria-label="AI에게 질문하기">
          <span aria-hidden="true">💬</span>
          <span className="qna-fab-label">AI 질문</span>
        </button>
      )}

      {open && (
        <aside className="qna-panel" role="dialog" aria-modal="false" aria-labelledby="qna-title">
          <header className="qna-head">
            <div>
              <h2 id="qna-title">AI에게 질문하기</h2>
              <p className="xsmall muted">수집한 공고·배점·일정 데이터로 답합니다 · 베타</p>
            </div>
            <div className="qna-head-actions">
              {messages.length > 0 && (
                <button type="button" className="btn btn-ghost btn-sm" onClick={() => setMessages([])} title="대화 지우기">지우기</button>
              )}
              <button type="button" className="btn btn-ghost btn-sm" onClick={() => setOpen(false)} aria-label="질문 창 닫기">✕</button>
            </div>
          </header>

          {!consent ? (
            <div className="qna-consent">
              <p className="qna-consent-title">이용 전에 확인해 주세요</p>
              <p className="small">{DISCLAIMER}</p>
              <label className="qna-check">
                <input type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
                위 내용을 확인했으며, 답변 활용에 대한 책임이 저에게 있음에 동의합니다.
              </label>
              <button type="button" className="btn btn-primary" disabled={!checked} onClick={agree}>동의하고 시작하기</button>
            </div>
          ) : (
            <>
              <div className="qna-context">
                {pinned ? (
                  <span className="qna-chip" title={pinnedTitle ?? pinned}>
                    📌 <span className="num">{pinned}</span> 기준{pinnedTitle && <span className="qna-chip-title"> · {pinnedTitle}</span>}
                    <button type="button" onClick={() => setPinned(null)} aria-label="공고 기준 해제">✕</button>
                  </span>
                ) : (
                  <span className="xsmall muted">전체 공고 기준 · 공고 상세에서 열면 그 공고를 기준으로 묻습니다</span>
                )}
                {company && <span className="xsmall muted">· 업체: {company.name}</span>}
              </div>

              <div ref={listRef} className="qna-list" data-lenis-prevent aria-live="polite">
                {messages.length === 0 && (
                  <div className="qna-empty">
                    <p className="small muted">이런 걸 물어볼 수 있어요</p>
                    <div className="qna-suggest">
                      {suggestions.map((s) => (
                        <button key={s} type="button" className="qna-suggest-btn" onClick={() => send(s)}>{s}</button>
                      ))}
                    </div>
                  </div>
                )}
                {messages.map((m) =>
                  m.role === "user" ? (
                    <div key={m.id} className="qna-msg user">
                      <p className="qna-bubble">{m.text}</p>
                      {m.notice && <span className="xsmall muted num">📌 {m.notice}</span>}
                    </div>
                  ) : (
                    <div key={m.id} className={`qna-msg bot ${m.kind}`}>
                      {m.kind === "pending" ? (
                        <p className="qna-bubble qna-typing" aria-label="답변 작성 중"><span /><span /><span /></p>
                      ) : (
                        <>
                          <p className="qna-bubble">{m.text}</p>
                          {m.sources?.length > 0 && (
                            <p className="xsmall muted qna-sources">
                              근거:{" "}
                              {m.sources.map((s, i) => (
                                <span key={i}>
                                  {i > 0 && ", "}
                                  {s.notice_no ? <Link to={`/notices/${s.notice_no}`}>{s.label ?? s.notice_no}</Link> : s.label}
                                </span>
                              ))}
                            </p>
                          )}
                          {(m.kind === "answer" || m.kind === "local") && (
                            <>
                              <p className="xsmall qna-warn">
                                {m.kind === "local" ? "데이터 기반 자동 답변 · " : ""}참고용 답변 · 공고 원문 확인 필요
                              </p>
                              <FeedbackReaction
                                compact
                                target="qna"
                                refId={String(m.id)}
                                question="이 답변이 도움이 됐나요?"
                                extra={{ question: m.question, answer: m.text.slice(0, 1000), source: m.kind === "local" ? "rule" : "llm", notice_no: m.notice ?? pinned ?? null }}
                              />
                            </>
                          )}
                          {m.kind === "error" && m.retry && (
                            <button type="button" className="btn btn-sm" onClick={() => send(m.retry)}>다시 시도</button>
                          )}
                        </>
                      )}
                    </div>
                  ),
                )}
              </div>

              <form
                className="qna-form"
                onSubmit={(e) => {
                  e.preventDefault();
                  send(text);
                }}
              >
                <textarea
                  ref={inputRef}
                  className="input qna-input"
                  rows={2}
                  value={text}
                  onChange={(e) => setText(e.target.value)}
                  onKeyDown={onKeyDown}
                  placeholder={pinned ? "이 공고에 대해 질문하세요" : "질문을 입력하세요"}
                  title="Enter 보내기 · Shift+Enter 줄바꿈"
                  maxLength={500}
                  aria-label="질문 입력"
                />
                <button type="submit" className="btn btn-primary" disabled={busy || !text.trim()}>보내기</button>
              </form>
              <p className="qna-foot xsmall muted">AI 답변은 참고용이며 정확하지 않을 수 있습니다. 활용에 대한 책임은 질문자에게 있습니다.</p>
            </>
          )}
        </aside>
      )}
    </>
  );
}
