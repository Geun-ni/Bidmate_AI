import { useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import "./Select.css";

/**
 * 직접 만든 드롭다운.
 * 브라우저 기본 <select> 목록은 화면 배율·임베드 환경에 따라 위치·열이 어긋나서 직접 그린다.
 * 키보드: ↑↓ 이동, Home/End, Enter·Space 선택, Esc 닫기, 글자 입력 시 해당 항목으로 이동.
 */
export default function Select({ id, value, onChange, options, placeholder = "선택하세요", disabled, invalid, describedBy, ariaLabel, size = "md", className = "" }) {
  const autoId = useId();
  const triggerId = id ?? `select-${autoId}`;
  const listId = `${triggerId}-list`;
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const [dropUp, setDropUp] = useState(false);
  const rootRef = useRef(null);
  const listRef = useRef(null);
  const typeahead = useRef({ text: "", timer: 0 });

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = options[selectedIndex];

  const openList = () => {
    if (disabled) return;
    setActive(selectedIndex >= 0 ? selectedIndex : 0);
    setOpen(true);
  };
  const close = () => setOpen(false);
  const choose = (index) => {
    const option = options[index];
    if (option) onChange(option.value);
    close();
  };

  // 바깥을 누르면 닫기
  useEffect(() => {
    if (!open) return undefined;
    const onDown = (e) => !rootRef.current?.contains(e.target) && close();
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // 아래 공간이 부족하면 위로 펼친다
  useLayoutEffect(() => {
    if (!open || !rootRef.current) return;
    const rect = rootRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - rect.bottom;
    setDropUp(spaceBelow < 260 && rect.top > spaceBelow);
  }, [open]);

  // 선택 중인 항목이 보이도록 스크롤
  useLayoutEffect(() => {
    if (!open || active < 0) return;
    listRef.current?.children[active]?.scrollIntoView({ block: "nearest" });
  }, [open, active]);

  const onKeyDown = (e) => {
    if (disabled) return;
    if (!open) {
      if (["ArrowDown", "ArrowUp", "Enter", " "].includes(e.key)) {
        e.preventDefault();
        openList();
      }
      return;
    }
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((i) => Math.min(options.length - 1, i + 1));
        break;
      case "ArrowUp":
        e.preventDefault();
        setActive((i) => Math.max(0, i - 1));
        break;
      case "Home":
        e.preventDefault();
        setActive(0);
        break;
      case "End":
        e.preventDefault();
        setActive(options.length - 1);
        break;
      case "Enter":
      case " ":
        e.preventDefault();
        choose(active);
        break;
      case "Escape":
        e.preventDefault();
        close();
        break;
      case "Tab":
        close();
        break;
      default:
        if (e.key.length === 1) {
          const t = typeahead.current;
          clearTimeout(t.timer);
          t.text += e.key;
          t.timer = setTimeout(() => (t.text = ""), 600);
          const found = options.findIndex((o) => o.label.startsWith(t.text));
          if (found >= 0) setActive(found);
        }
    }
  };

  return (
    <div ref={rootRef} className={`sel sel-${size} ${className}`}>
      <button
        id={triggerId}
        type="button"
        role="combobox"
        className="sel-trigger"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listId}
        aria-activedescendant={open && active >= 0 ? `${listId}-${active}` : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={describedBy}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => (open ? close() : openList())}
        onKeyDown={onKeyDown}
      >
        <span className={`sel-value${selected ? "" : " sel-placeholder"}`}>{selected ? selected.short ?? selected.label : placeholder}</span>
        <span className="sel-caret" aria-hidden="true">▾</span>
      </button>
      {open && (
        <ul id={listId} ref={listRef} data-lenis-prevent role="listbox" className={`sel-list${dropUp ? " up" : ""}`} aria-labelledby={triggerId}>
          {options.map((o, i) => (
            <li
              key={o.value}
              id={`${listId}-${i}`}
              role="option"
              aria-label={o.hint ? `${o.label} (${o.hint})` : o.label}
              aria-selected={o.value === value}
              className={`sel-option${i === active ? " active" : ""}`}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => choose(i)}
            >
              <span className="sel-check" aria-hidden="true">{o.value === value ? "✓" : ""}</span>
              <span className="sel-label">{o.label}</span>
              {o.hint && <span className="sel-hint">{o.hint}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
