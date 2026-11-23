import { useEffect, useId, useLayoutEffect, useReducer, useRef } from "react";
import { formatMoneyInput, koreanWon } from "../lib/format.js";

const MAX_DIGITS = 13; // 9조 원대까지

/** 문자열에서 앞에서부터 n번째 숫자 바로 뒤의 위치 (n=0이면 맨 앞) */
function positionAfterDigits(text, n) {
  if (n <= 0) return 0;
  let seen = 0;
  for (let i = 0; i < text.length; i++) {
    if (/\d/.test(text[i]) && ++seen === n) return i + 1;
  }
  return text.length;
}

/** 커서가(범위 선택이 아닐 때) 콤마 바로 뒤에 있으면 콤마 앞으로 옮긴다 */
function snapElement(el) {
  if (!el) return;
  const { selectionStart: s, selectionEnd: end, value: v } = el;
  if (s === end && s > 0 && v[s - 1] === ",") el.setSelectionRange(s - 1, s - 1);
}

/**
 * 금액 입력칸.
 * - 입력하는 대로 세 자리마다 콤마를 넣되, 커서는 "커서 앞에 있던 숫자 개수" 기준으로 제자리를 지킨다.
 *   (중간 숫자를 지우거나 끼워 넣어도 커서가 맨 뒤로 튀지 않는다)
 * - 숫자가 아닌 글자(한글·영문·기호)는 무시하고 커서도 움직이지 않는다.
 * - 콤마 바로 뒤에서 Backspace를 누르면 콤마 앞 숫자를 지운다. Delete도 같은 방식.
 */
export default function MoneyInput({ label, value, onChange, hint, required }) {
  const id = useId();
  const ref = useRef(null);
  const caretDigits = useRef(null);
  const [, rerender] = useReducer((x) => x + 1, 0);
  const display = formatMoneyInput(value);

  // 렌더 후 커서 복원 (값이 그대로여도 다시 실행되도록 의존성 배열을 두지 않는다)
  useLayoutEffect(() => {
    const el = ref.current;
    if (caretDigits.current === null || !el) return;
    const pos = positionAfterDigits(el.value, caretDigits.current);
    caretDigits.current = null;
    if (document.activeElement === el) el.setSelectionRange(pos, pos);
  });

  const handleChange = (e) => {
    const raw = e.target.value;
    const caret = e.target.selectionStart ?? raw.length;
    let before = raw.slice(0, caret).replace(/\D/g, "").length;
    let digits = raw.replace(/\D/g, "");

    // 앞자리 0 제거 (커서 앞에서 지워진 만큼 보정)
    const trimmed = digits.replace(/^0+(?=\d)/, "");
    before -= Math.min(before, digits.length - trimmed.length);
    digits = trimmed;

    // 자릿수 초과 입력은 무시
    if (digits.length > MAX_DIGITS) {
      before -= digits.length - MAX_DIGITS;
      digits = String(value ?? "");
    }

    caretDigits.current = Math.max(0, before);
    onChange(digits ? Number(digits) : null);
    rerender(); // 숫자가 안 바뀐 입력(한글 등)도 화면 값을 원래대로 되돌리고 커서를 복원한다
  };

  /**
   * 콤마는 없는 글자처럼 다룬다.
   * - ←/→: 콤마에서 멈추지 않고 다음 숫자 사이로 바로 이동 (커서는 항상 콤마 앞에 둔다)
   * - Backspace/Delete: 콤마 대신 그 옆 숫자를 지운다
   * Shift(범위 선택)나 Ctrl/Alt/⌘ 조합은 브라우저 기본 동작을 따른다.
   */
  const handleKeyDown = (e) => {
    const el = e.currentTarget;
    const { selectionStart: s, selectionEnd: end, value: v } = el;
    if (s !== end || e.shiftKey || e.ctrlKey || e.altKey || e.metaKey) return;

    // 화살표는 "숫자 단위"로 움직인다: 커서 앞 숫자 개수를 하나 줄이거나 늘린 자리(콤마 앞)로 이동.
    // 커서가 어디에 있든(콤마 앞이든 뒤든) 한 번 누르면 반드시 숫자 하나를 지나간다.
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      const before = v.slice(0, s).replace(/\D/g, "").length;
      const total = v.replace(/\D/g, "").length;
      const next = e.key === "ArrowLeft" ? before - 1 : before + 1;
      if (next < 0 || next > total) return; // 맨 앞/맨 끝에서는 기본 동작
      e.preventDefault();
      const pos = positionAfterDigits(v, next);
      el.setSelectionRange(pos, pos);
      return;
    }
    if (e.key === "Backspace" && s > 0 && v[s - 1] === ",") el.setSelectionRange(s - 1, s - 1);
    if (e.key === "Delete" && v[s] === ",") el.setSelectionRange(s + 1, s + 1);
  };

  /**
   * 마우스 클릭·터치로 커서가 콤마 바로 뒤에 놓이면 콤마 앞(앞 숫자 바로 뒤)으로 옮긴다.
   * 범위를 드래그해 선택한 경우는 건드리지 않는다.
   */
  const snapCaret = (e) => snapElement(e.currentTarget);

  // 클릭·터치·탭 이동·프로그램 이동 등 어떤 경로로든 커서가 콤마 뒤에 오면 콤마 앞으로 옮긴다.
  useEffect(() => {
    const onSelectionChange = () => {
      if (document.activeElement === ref.current) snapElement(ref.current);
    };
    document.addEventListener("selectionchange", onSelectionChange);
    return () => document.removeEventListener("selectionchange", onSelectionChange);
  }, []);

  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {label}
        {required && <span className="req">*</span>}
      </label>
      <div className="money-wrap">
        <input
          ref={ref}
          id={id}
          className="input input-money num"
          inputMode="numeric"
          autoComplete="off"
          placeholder="0"
          value={display}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onMouseUp={snapCaret}
          onTouchEnd={snapCaret}
          onFocus={(e) => requestAnimationFrame(() => snapCaret({ currentTarget: e.target }))}
          aria-describedby={`${id}-ko`}
        />
        <span className="money-unit">원</span>
      </div>
      <span id={`${id}-ko`} className="field-hint num">{value ? koreanWon(value) : hint}</span>
    </div>
  );
}
