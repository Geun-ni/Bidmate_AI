import { useEffect, useRef, useState } from "react";
import { SCENARIOS } from "../mocks/scenarios.js";
import { useAppState } from "../state/AppState.jsx";
import "./DevPanel.css";

/** 개발 모드 전용: 목업 시나리오를 바꿔 여러 화면 상태를 확인한다. 빌드 결과에는 포함되지 않는다. */
export default function DevPanel() {
  const { scenario, setScenario } = useAppState();
  const [open, setOpen] = useState(false);
  const ref = useRef(null);

  // 바깥을 누르거나 Esc를 누르면 닫기
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => !ref.current?.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (key) => {
    setScenario(key);
    setOpen(false); // 고르면 바로 닫는다
  };

  return (
    <div className="devpanel" ref={ref}>
      {open && (
        <div className="devpanel-body" role="dialog" aria-label="목업 시나리오">
          <p className="devpanel-title">목업 시나리오 <span className="muted xsmall">개발 모드 전용</span></p>
          <ul>
            {Object.entries(SCENARIOS).map(([key, s]) => (
              <li key={key}>
                <label className="devpanel-option">
                  <input type="radio" name="scenario" checked={scenario === key} onChange={() => pick(key)} onClick={() => scenario === key && setOpen(false)} />
                  <span>
                    <strong>{s.label}</strong>
                    <span className="muted xsmall">{s.description}</span>
                  </span>
                </label>
              </li>
            ))}
          </ul>
        </div>
      )}
      <button type="button" className="devpanel-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        목업 · {SCENARIOS[scenario]?.label ?? "기본"}
      </button>
    </div>
  );
}
