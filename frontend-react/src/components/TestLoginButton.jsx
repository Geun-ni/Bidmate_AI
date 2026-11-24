import { useNavigate } from "react-router-dom";
import { useAppState } from "../state/AppState.jsx";

/** 테스트용 자동 로그인 버튼 (개발 서버에서만 보인다. 배포 빌드에는 나오지 않음) */
export default function TestLoginButton({ to = "/" }) {
  const { testEnabled, startTestMode } = useAppState();
  const navigate = useNavigate();
  if (!testEnabled) return null;
  return (
    <div className="test-login">
      <button
        type="button"
        className="btn btn-sm"
        onClick={() => {
          startTestMode();
          navigate(to, { replace: true });
        }}
      >
        🧪 자동로그인 (테스트)
      </button>
      <p className="xsmall muted">서버 계정 없이 테스트 사용자로 들어갑니다. 업체정보·관심공고는 이 브라우저에만 저장됩니다.</p>
    </div>
  );
}
