import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { lookupCompany } from "../api/client.js";
import { NoticeBox } from "../components/ui.jsx";
import { useAppState } from "../state/AppState.jsx";
import TestLoginButton from "../components/TestLoginButton.jsx";
import "./CompanyForm.css";

/** 로그인·회원가입 (이메일·비밀번호, 세션 쿠키). 가입할 때 사업자번호로 기존 업체 합류를 요청할 수 있다. */
export default function Login() {
  const { user, signIn, signUp } = useAppState();
  const navigate = useNavigate();
  const location = useLocation();
  const from = location.state?.from ?? "/";
  const [mode, setMode] = useState("login"); // login | signup
  const [form, setForm] = useState({ email: "", password: "", password2: "", displayName: "", brn: "" });
  const [found, setFound] = useState(null); // 사업자번호로 찾은 업체 목록
  const [joinId, setJoinId] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (patch) => setForm((f) => ({ ...f, ...patch }));

  // 이미 로그인한 채로 들어오면 돌려보낸다 (방금 제출해서 로그인된 경우는 submit이 직접 이동)
  const submitted = useRef(false);
  useEffect(() => {
    if (user && !submitted.current) navigate(from, { replace: true });
  }, [user, from, navigate]);

  const find = async () => {
    setError("");
    setJoinId("");
    try {
      const r = await lookupCompany(form.brn);
      setFound(r.companies);
    } catch (e) {
      setFound(null);
      setError(e.message);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setError("");
    if (mode === "signup") {
      if (form.password.length < 8) return setError("비밀번호는 8자 이상입니다.");
      if (form.password !== form.password2) return setError("비밀번호 확인이 일치하지 않습니다.");
    }
    setBusy(true);
    submitted.current = true;
    try {
      if (mode === "login") await signIn(form.email, form.password);
      else await signUp({ email: form.email, password: form.password, display_name: form.displayName || undefined, join_company_id: joinId || undefined });
      // 업체가 없는 새 사용자는 업체정보 등록으로 보낸다
      navigate(mode === "signup" && !joinId ? "/company" : from, { replace: true });
    } catch (err) {
      submitted.current = false;
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  const switchMode = (next) => {
    setMode(next);
    setError("");
  };

  return (
    <form className="company-form auth-form" onSubmit={submit} noValidate>
      <div>
        <h1 className="page-title">{mode === "login" ? "로그인" : "회원가입"}</h1>
        <p className="page-sub">로그인하면 업체정보·관심공고·점수 계산 결과를 저장할 수 있습니다.</p>
      </div>

      <div className="seg-light" role="group" aria-label="로그인 또는 회원가입">
        <button type="button" aria-pressed={mode === "login"} onClick={() => switchMode("login")}>로그인</button>
        <button type="button" aria-pressed={mode === "signup"} onClick={() => switchMode("signup")}>회원가입</button>
      </div>

      <section className="card card-pad form-section">
        <div className="form-grid">
          <label className="field span-2">
            <span className="field-label">이메일<span className="req">*</span></span>
            <input className="input" type="email" autoComplete="email" required value={form.email} onChange={(e) => set({ email: e.target.value })} />
          </label>
          <label className="field span-2">
            <span className="field-label">비밀번호<span className="req">*</span></span>
            <input className="input" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"} required value={form.password} onChange={(e) => set({ password: e.target.value })} />
            {mode === "signup" && <span className="field-hint">8자 이상</span>}
          </label>
          {mode === "signup" && (
            <>
              <label className="field span-2">
                <span className="field-label">비밀번호 확인<span className="req">*</span></span>
                <input className="input" type="password" autoComplete="new-password" value={form.password2} onChange={(e) => set({ password2: e.target.value })} />
              </label>
              <label className="field span-2">
                <span className="field-label">이름</span>
                <input className="input" placeholder="선택" value={form.displayName} onChange={(e) => set({ displayName: e.target.value })} />
              </label>
            </>
          )}
        </div>
      </section>

      {mode === "signup" && (
        <section className="card card-pad form-section" aria-labelledby="join-title">
          <h2 id="join-title" className="card-title">기존 업체에 합류 (선택)</h2>
          <p className="small muted">동료가 이미 등록한 업체가 있으면 사업자등록번호로 찾아 합류를 요청하세요. 업체 소유자가 승인하면 업체정보를 함께 씁니다. 새 업체를 등록할 거라면 비워 두세요.</p>
          <div className="join-row">
            <input className="input num" inputMode="numeric" placeholder="사업자등록번호 10자리" value={form.brn} onChange={(e) => set({ brn: e.target.value })} />
            <button type="button" className="btn" onClick={find} disabled={!form.brn.trim()}>찾기</button>
          </div>
          {found && found.length === 0 && <p className="small muted">이 번호로 등록된 업체가 없습니다.</p>}
          {found && found.length > 0 && (
            <div className="option-list">
              {found.map((c) => (
                <label key={c.company_id} className={`option${joinId === c.company_id ? " on" : ""}`}>
                  <input type="radio" name="join" checked={joinId === c.company_id} onChange={() => setJoinId(c.company_id)} onClick={() => joinId === c.company_id && setJoinId("")} />
                  <span>{c.company_name}</span>
                  <span className="muted small">{c.area_limit_name} · {c.region_name}</span>
                </label>
              ))}
            </div>
          )}
        </section>
      )}

      {error && <NoticeBox tone="danger" icon="!">{error}</NoticeBox>}

      <div className="form-actions">
        <Link to="/" className="btn">취소</Link>
        <span className="spacer" />
        <button type="submit" className="btn btn-primary" disabled={busy || !form.email || !form.password}>
          {busy ? "처리 중…" : mode === "login" ? "로그인" : joinId ? "가입하고 합류 요청" : "가입하기"}
        </button>
      </div>
      <TestLoginButton to={from} />
    </form>
  );
}
