import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import * as api from "../../api/client.js";
import { won } from "../../lib/format.js";
import { load, save as saveLocal } from "../../lib/storage.js";
import { useEvaluation } from "../../lib/useEvaluation.js";
import { useAppState } from "../../state/AppState.jsx";
import MoneyInput from "../MoneyInput.jsx";
import Select from "../Select.jsx";
import { EmptyState, NoticeBox, Term } from "../ui.jsx";
import { FeedbackReaction } from "../Feedback.jsx";
import "./ScoreTab.css";

/**
 * 적격심사 예상 점수 (FUNC-019~023).
 * 공고마다 다른 배점 규칙(GET /evaluation)으로 입력칸을 만들고, 계산은 서버(POST /score)가 한다.
 * 입력하지 않은 칸은 0점이 아니라 '미산정'이며, 미산정이 남으면 총점을 확정하지 않는다.
 * 입력 키·값 형식은 backend/scoring.py 맨 위 설명을 따른다.
 */

const CREDIT_GRADES = ["AAA", "AA+", "AA0", "AA-", "A+", "A0", "A-", "BBB+", "BBB0", "BBB-", "BB+", "BB0", "BB-",
  "B+", "B0", "B-", "CCC+", "CCC", "CCC-", "CC", "C", "D"];
const RATIO_UNIT = { debt_ratio: "%", current_ratio: "%", equity_ratio: "%", business_years: "년", similar_amount: "원" };

const fmtScore = (v) => (v === null || v === undefined ? null : Math.round(v * 10000) / 10000);

/** 반원 계기판: 바늘이 점수까지 움직이고, 기준 점수 위치에 주황 눈금 */
function Gauge({ value, pass, tentative = false }) {
  const angle = (score) => -90 + 180 * Math.min(1, Math.max(0, score / 100));
  const tick = ((pass == null ? -90 : angle(pass)) * Math.PI) / 180;
  const tx = 50 + 40 * Math.sin(tick);
  const ty = 50 - 40 * Math.cos(tick);
  return (
    <svg className="gauge" viewBox="0 0 100 56" aria-hidden="true">
      <path d="M8 50 A42 42 0 0 1 92 50" fill="none" stroke="var(--soft)" strokeWidth="8" strokeLinecap="round" />
      {pass != null && <line x1={tx} y1={ty} x2={50 + 48 * Math.sin(tick)} y2={50 - 48 * Math.cos(tick)} stroke="var(--accent)" strokeWidth="3" strokeLinecap="round" />}
      <line
        className="gauge-needle"
        x1="50" y1="50" x2="50" y2="14"
        stroke={value === null ? "var(--border-strong)" : tentative ? "var(--text-3)" : "var(--text)"}
        strokeWidth="3" strokeLinecap="round" strokeDasharray={tentative ? "4 3" : undefined}
        style={{ transform: `rotate(${value === null ? -90 : angle(value)}deg)` }}
      />
      <circle cx="50" cy="50" r="4.5" fill="var(--text)" />
    </svg>
  );
}

/** 서버 계산 결과 1항목 → 점수 배지 */
/** 아직 확정 안 된 묶음의 '지금까지 점수': 산정된 하위 항목만 더한다 (미산정은 0) */
function partialOf(item) {
  if (!item) return null;
  if (item.status === "산정") return item.score;
  if (!item.children?.length) return null;
  const parts = item.children.map(partialOf).filter((v) => v !== null);
  return parts.length ? parts.reduce((a, b) => a + b, 0) : null;
}

/**
 * 점수 표시. 폭을 고정해(score-slot) 배지 ↔ 숫자로 바뀔 때 옆 글자가 흔들리지 않게 한다.
 * range: [최소, 최대] 를 주면 진행 중 부분 점수를 그 범위로 잘라 보여 준다 (묶음 한도)
 */
function ScoreValue({ item, max, range }) {
  let body;
  if (!item) body = <span className="badge badge-muted">계산 전</span>;
  else if (item.status === "산정") {
    body = (
      <span className="score-val num">
        <strong>{fmtScore(item.score)}</strong>
        {max != null && <span className="muted"> / {max}점</span>}
      </span>
    );
  } else {
    let part = partialOf(item);
    if (part !== null && range) part = Math.min(range[1], Math.max(range[0], part));
    body = item.status === "산정 보류" ? (
      <span className="badge badge-danger">산정 보류</span>
    ) : part !== null ? (
      <span className="score-val num partial" title="아직 입력하지 않은 항목이 있어 확정되지 않은 점수입니다">
        <strong>{fmtScore(part)}</strong>
        <span className="muted xsmall"> 진행 중</span>
      </span>
    ) : (
      <span className="badge badge-warn">미산정</span>
    );
  }
  return <span className="score-slot">{body}</span>;
}

/** 묶음 점수의 표시 범위: 가점·감점은 parameters.min~max, 나머지는 0~배점 */
const rangeOf = (node) => {
  const p = node.parameters ?? {};
  if (node.category === "BONUS" && (p.min != null || p.max != null)) return [p.min ?? -Infinity, p.max ?? Infinity];
  return node.max_score != null && node.max_score >= 0 ? [0, node.max_score] : null;
};

/** '해당 없음'(0점)으로 채울 수 있는 칸 → [[키, 값], …]. 이미 입력한 칸은 건드리지 않는다 */
function noneValues(n) {
  const p = n.parameters ?? {};
  const k = n.key;
  const ik = n.input_key;
  if (ik === "bonus_grade") return [[k, p.input_type === "check" ? false : p.input_type === "count" ? 0 : null]];
  if (ik === "bonus_count") return [[k, 0]];
  if (ik === "tech_dev") return [[k, []]];
  if (ik === "bonus_ratio") return [[`${k}_total`, 1], ...p.terms.map((_, i) => [`${k}_${i}`, 0])];
  if (n.rule_type === "FIXED" && p.items) return [[k, []]];
  if (n.rule_type === "FIXED") return [[k, false]];
  if (n.rule_type === "FORMULA" && "per_unit" in p) return [[k, 0]];
  if (n.rule_type === "GROUP" && !n.children.length) return [[k, 0]];
  return [];
}
const leavesOf = (n) => (n.children.length ? n.children.flatMap(leavesOf) : [n]);

/* ───────── 입력 칸 ───────── */

/**
 * 숫자 입력칸. min·max를 넘는 값은 바로 범위 끝값으로 바꾸고, 칸 위에 작은 안내를 잠깐 띄운다.
 * (예: 10점 만점 항목에 14를 넣으면 10으로 바뀌고 "최대 10점까지입니다.")
 */
function NumberField({ value, onChange, unit, min, max, placeholder = "미입력" }) {
  const [tip, setTip] = useState(null);
  const timer = useRef(0);
  useEffect(() => () => clearTimeout(timer.current), []);
  const unitWord = unit ? unit.split(" ")[0] : "";
  const flash = (text) => {
    setTip(text);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setTip(null), 2200);
  };
  const change = (raw) => {
    if (raw === "") return onChange(undefined);
    let v = Number(raw);
    if (max != null && v > max) {
      v = max;
      flash(`최대 ${max}${unitWord}까지입니다.`);
    } else if (min != null && v < min) {
      v = min;
      flash(`최소 ${min}${unitWord}부터입니다.`);
    }
    onChange(v);
  };
  return (
    <span className="unit-wrap num-field">
      <input
        className="input num"
        type="number"
        inputMode="decimal"
        step="any"
        min={min}
        max={max}
        value={value ?? ""}
        placeholder={placeholder}
        onChange={(e) => change(e.target.value)}
      />
      {unit && <span className="money-unit">{unit}</span>}
      {tip && <span className="clamp-tip" role="status">{tip}</span>}
    </span>
  );
}

/** 해당 / 해당 없음 (다시 누르면 미입력) */
function YesNo({ name, value, onChange, yes = "해당", no = "해당 없음" }) {
  const opts = [[true, yes], [false, no]];
  return (
    <div className="yn" role="radiogroup" aria-label={name}>
      {opts.map(([v, l]) => (
        <label key={String(v)} className={`yn-opt${value === v ? " on" : ""}`}>
          <input type="radio" name={name} checked={value === v} onChange={() => onChange(v)} onClick={() => value === v && onChange(undefined)} />
          {l}
        </label>
      ))}
    </div>
  );
}

/**
 * 보기 고르기. 보기가 3개 이하이고 짧으면 한 줄 버튼(다시 누르면 미입력),
 * 많거나 길면 드롭다운으로 보여 줘 세로 길이를 줄인다. options: [{ value, label, score }]
 */
/** 긴 보기 이름 → 앞부분 … 끝부분 (끝의 '(3년 이상)', '(0.5점)' 같은 구분이 잘리지 않게) */
function shorten(text, head = 16, tail = 18) {
  const s = String(text);
  return s.length > head + tail + 3 ? `${s.slice(0, head).trim()} … ${s.slice(-tail).trim()}` : s;
}

function Options({ name, value, onChange, options }) {
  const long = options.length > 3 || options.some((o) => String(o.label).length > 14);
  const withScore = (o) => (o.score != null ? `${o.label} (${o.score}점)` : o.label);
  if (long) {
    // Select는 문자열 값만 다루므로 보기 순번으로 바꿔 넘긴다
    const idx = options.findIndex((o) => o.value === value);
    return (
      <Select
        size="sm"
        className="sel-wrap"
        ariaLabel={name}
        value={idx >= 0 ? String(idx) : ""}
        onChange={(v) => onChange(v === "" ? undefined : options[Number(v)].value)}
        placeholder="미입력"
        options={[{ value: "", label: "미입력" }, ...options.map((o, i) => ({ value: String(i), label: withScore(o), short: shorten(withScore(o)) }))]}
      />
    );
  }
  return (
    <div className="yn" role="radiogroup" aria-label={name}>
      {options.map((o) => {
        const on = value === o.value;
        return (
          <label key={String(o.value)} className={`yn-opt${on ? " on" : ""}`}>
            <input type="radio" name={name} checked={on} onChange={() => onChange(o.value)} onClick={() => on && onChange(undefined)} />
            {withScore(o)}
          </label>
        );
      })}
    </div>
  );
}

/** 개발실적(tech_dev): 건마다 종류·경과 연수·출원자 수 */
function TechDevField({ node, value, onChange }) {
  const p = node.parameters;
  const rows = Array.isArray(value) ? value : null;
  const update = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="techdev">
      {rows?.map((r, i) => (
        <div key={i} className="techdev-row">
          <Select size="sm" ariaLabel="종류" value={String(r.kind)} onChange={(v) => update(i, { kind: Number(v) })}
            options={p.kinds.map((k, ki) => ({ value: String(ki), label: `${k.name} (${k.per_unit}점)` }))} />
          <NumberField value={r.years} onChange={(v) => update(i, { years: v })} unit="년 경과" min={0} placeholder="연수" />
          {p.split_by_inventors && <NumberField value={r.inventors} onChange={(v) => update(i, { inventors: v })} unit="명 출원" min={1} placeholder="1" />}
          {p.non_field_ratio && (
            <label className="chip-check xsmall">
              <input type="checkbox" checked={Boolean(r.non_field)} onChange={(e) => update(i, { non_field: e.target.checked })} /> 배전분야 아님
            </label>
          )}
          <button type="button" className="btn btn-ghost btn-sm" onClick={() => onChange(rows.filter((_, j) => j !== i))}>삭제</button>
        </div>
      ))}
      <div className="techdev-actions">
        <button type="button" className="btn btn-sm" onClick={() => onChange([...(rows ?? []), { kind: 0, years: undefined, inventors: 1 }])}>+ 실적 추가</button>
        {(!rows || rows.length > 0) && <button type="button" className="btn btn-sm btn-ghost" onClick={() => onChange([])}>해당 없음 (0점)</button>}
        {rows && rows.length === 0 && <span className="xsmall muted">해당 없음으로 계산합니다.</span>}
      </div>
    </div>
  );
}

const rowsHint = (rows) => (rows ?? []).map((r) => `${r.label ?? r.grade ?? r.level ?? ""} ${r.score ?? ""}`.trim()).join(" · ");

/**
 * 규칙 1개 → [입력 칸, 설명]. backend/dev_page.html 의 field() 와 같은 분기.
 * get(key) / set(key, value): 입력값 읽기·쓰기 (undefined면 지움 = 미산정)
 */
function fieldFor(n, get, set) {
  const p = n.parameters ?? {};
  const k = n.key;
  const ik = n.input_key;
  const rt = n.rule_type;
  const bind = (key) => ({ value: get(key), onChange: (v) => set(key, v) });

  if (ik === "pq_score") return [<NumberField key="f" {...bind(k)} unit="점 (0~100)" min={0} max={p.full ?? 100} />, p["입력"]];
  if (ik === "performance_amount" || ik === "same_kind_amount") {
    return [
      <MoneyInput key="f" label={p.numerator ?? "실적 누계액"} value={get(k) ?? null} onChange={(v) => set(k, v ?? undefined)} />,
      p.base_amount ? `기준금액(${p.denominator}) ${won(p.base_amount)} · ${n.formula ?? ""}` : `${p.denominator ?? "기준금액"} 값이 없어 계산 보류`,
    ];
  }
  if (ik === "engineer_days") {
    const s = p.short_tenure;
    return [
      <div key="f" className="field-stack">
        <NumberField {...bind(k)} unit="일" min={0} />
        {s && <YesNo name={`${k}_short`} value={get(`${k}_short`)} onChange={(v) => set(`${k}_short`, v)} yes={`재직 ${s.days}일 미만`} no={`재직 ${s.days}일 이상`} />}
      </div>,
      n.formula,
    ];
  }
  if (ik === "bonus_count" && p.period) {
    return [<NumberField key="f" {...bind(k)} unit="개월" min={0} />, `${p.period}${p.period_unit ?? "개월"}마다 ${p.per_unit}점, 최대 ${Math.abs(p.limit)}점${p.round_up ? " (모자란 기간은 올림)" : ""}`];
  }
  if (ik === "bonus_count") return [<NumberField key="f" {...bind(k)} unit="건" min={0} />, `건당 ${p.per_unit}점, 최대 ${Math.abs(p.limit)}점 · ${p["설명"] ?? ""}`];
  if (ik === "bonus_ratio") {
    return [
      <div key="f" className="field-stack">
        <label className="inline-field"><span className="xsmall muted">{p.denominator}</span><NumberField {...bind(`${k}_total`)} unit="명" min={0} /></label>
        {p.terms.map((t, i) => (
          <label key={t.label} className="inline-field"><span className="xsmall muted">{t.label} {p.numerator}</span><NumberField {...bind(`${k}_${i}`)} unit="명" min={0} /></label>
        ))}
      </div>,
      `(${p.numerator} ÷ ${p.denominator}) × 점수의 합, 최대 ${p.limit}점`,
    ];
  }
  if (ik === "tech_dev") return [<TechDevField key="f" node={n} value={get(k)} onChange={(v) => set(k, v)} />, `${n.formula} · 경과 기간이 길수록 낮게 인정`];
  if (rt === "FORMULA" && "per_unit" in p) {
    const s = p.special;
    return [
      <div key="f" className="field-stack">
        <NumberField {...bind(k)} unit={s ? `명 (${s.name} 제외)` : "명"} min={0} />
        {s && <NumberField {...bind(`${k}_special`)} unit={`명 (${s.name})`} min={0} />}
      </div>,
      `1명당 ${p.per_unit}점${s ? `, ${s.name} 1명당 ${s.per_unit}점` : ""}, 최대 ${p.cap}점`,
    ];
  }
  if (rt === "TABLE" && ik === "office_location") {
    const options = [...(p.rows ?? []).map((r) => ({ value: r.level, label: r.level, score: r.score })), { value: "광역 밖", label: "광역지역 밖", score: p.outside_region_score ?? 0 }];
    return [<Options key="f" name={k} {...bind(k)} options={options} />, p.office ? `해당 사업소: ${p.office}${p.adjacent ? ` · 인접: ${[].concat(p.adjacent).join(", ")}` : ""}` : "본점이 어느 사업소 관할인지 고르세요."];
  }
  if (ik === "credit_grade") {
    return [
      <Select key="f" size="sm" ariaLabel="신용평가등급" value={get(k) ?? ""} onChange={(v) => set(k, v || undefined)} placeholder="등급 선택"
        options={[...CREDIT_GRADES.map((g) => ({ value: g, label: g })), { value: "없음", label: "유효한 등급 없음" }]} />,
      rowsHint((p.rows ?? []).map((r) => ({ label: r["회사채"], score: r.score }))),
    ];
  }
  if (RATIO_UNIT[ik]) {
    const unit = RATIO_UNIT[ik];
    const basis = p.basis ? ` · 기준 ${p.basis.toLocaleString()}${unit === "원" ? "원" : "%"} 대비 배수로 구간 판정` : p.value_unit === "기준치 배수" ? " · 기준치가 없어 계산 보류될 수 있음" : "";
    const input = unit === "원"
      ? <MoneyInput key="f" label="실적 합계" value={get(k) ?? null} onChange={(v) => set(k, v ?? undefined)} />
      : <NumberField key="f" {...bind(k)} unit={unit} />;
    return [input, `${p["입력"] ? `${p["입력"]} · ` : ""}${rowsHint(p.rows)}${basis}`];
  }
  if ((ik === "pq_grade" || ik === "bonus_grade") && p.input_type === "select") {
    const options = (p.rows ?? []).map((r, i) => ({ value: i, label: r.grade, score: r.score }));
    if (ik === "bonus_grade") options.push({ value: null, label: "해당 없음", score: 0 });
    return [<Options key="f" name={k} {...bind(k)} options={options} />, p.condition || p["참고"]];
  }
  if ((ik === "pq_grade" || ik === "bonus_grade") && p.input_type === "check") {
    return [<YesNo key="f" name={k} {...bind(k)} yes={`해당 (${p.rows[0].score}점)`} no="해당 없음 (0점)" />, p.rows[0]?.grade];
  }
  if (ik === "pq_grade" || ik === "bonus_grade") {
    return [<NumberField key="f" {...bind(k)} unit={p.unit ?? ""} />, `${p["입력"] || p.condition || ""}${p["입력"] || p.condition ? " · " : ""}${rowsHint(p.rows)}`];
  }
  if (rt === "FIXED" && p.items) {
    const owned = get(k);
    const toggle = (name, on) => set(k, on ? [...(owned ?? []), name] : (owned ?? []).filter((x) => x !== name));
    return [
      <div key="f" className="check-list">
        {p.items.map((i) => (
          <label key={i.name} className="chip-check">
            <input type="checkbox" checked={(owned ?? []).includes(i.name)} onChange={(e) => toggle(i.name, e.target.checked)} /> {i.name} ({i.score}점)
          </label>
        ))}
        {!owned && <button type="button" className="btn btn-sm btn-ghost" onClick={() => set(k, [])}>보유 장비 없음</button>}
      </div>,
      "보유한 장비를 모두 고르세요.",
    ];
  }
  if (rt === "FIXED") {
    const score = p.score ?? n.max_score;
    return [<YesNo key="f" name={k} {...bind(k)} yes={`해당 (${score}점)`} no="해당 없음 (0점)" />, p.condition];
  }
  if (rt === "GROUP") {
    const lo = p.min ?? (n.max_score >= 0 ? 0 : n.max_score);
    const hi = p.max ?? (n.max_score >= 0 ? n.max_score : 0);
    return [<NumberField key="f" {...bind(k)} unit="점" min={lo} max={hi} />, `세부 규칙이 없어 점수를 직접 입력합니다 (${lo} ~ ${hi}점).`];
  }
  return [<span key="f" className="small muted">자동 계산을 지원하지 않는 항목</span>, `${rt}/${ik ?? "-"} · 원문에서 직접 확인하세요.`];
}

/* ───────── 화면 ───────── */

/** 결과 트리 → key별 결과 */
function indexResults(items, map = new Map()) {
  for (const it of items ?? []) {
    map.set(it.key, it);
    indexResults(it.children, map);
  }
  return map;
}

/** tech_dev 처럼 목록 입력은 빈 줄을 빼고 보낸다 */
function cleanInputs(inputs) {
  const out = {};
  for (const [k, v] of Object.entries(inputs)) {
    if (v === undefined) continue;
    out[k] = Array.isArray(v) && v.some((x) => x && typeof x === "object") ? v.filter((x) => x.years !== undefined && x.years !== null) : v;
  }
  return out;
}

function Leaf({ node, get, set, results }) {
  const [input, hint] = fieldFor(node, get, set);
  const res = results.get(node.key);
  return (
    <div className="rule-row">
      <div className="rule-label">
        <span className="rule-name">
          {node.name} {node.max_score != null && <span className="muted xsmall num">({node.max_score}점)</span>}
          {node.verification_status === "REVIEW" && <span className="badge badge-warn" style={{ marginLeft: 6 }}>원문 확인</span>}
        </span>
        <ScoreValue item={res} max={node.max_score} />
      </div>
      <div className="rule-field">
        {input}
        {res?.note && <p className="xsmall rule-note">{res.note}</p>}
        {hint && (
          <details className="rule-hint">
            <summary className="xsmall" tabIndex={-1}>기준 보기</summary>
            <p className="xsmall muted">{hint}</p>
          </details>
        )}
      </div>
    </div>
  );
}

/** 하위 항목 중 계산이 끝난 칸 수 / 전체 칸 수 */
function progressOf(node, results) {
  if (!node.children.length) return [results.get(node.key)?.status === "산정" ? 1 : 0, 1];
  return node.children.reduce(([a, b], c) => {
    const [x, y] = progressOf(c, results);
    return [a + x, b + y];
  }, [0, 0]);
}

/** 접고 펴는 소분류 (참여감리원·신용도 …). 제목 줄에 진행도와 점수를 보여 준다 */
function Group({ node, get, set, results, defaultOpen }) {
  const [open, setOpen] = useState(defaultOpen);
  const [done, total] = progressOf(node, results);
  return (
    <div className={`rule-group${open ? " open" : ""}`}>
      <button type="button" className="rule-group-head" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="rule-caret" aria-hidden="true">{open ? "▾" : "▸"}</span>
        <span className="rule-group-name">
          {node.name} {node.max_score != null && <span className="muted xsmall num">({node.max_score}점)</span>}
        </span>
        <span className={`xsmall num rule-progress${done === total ? " full" : ""}`}>{done}/{total} 입력</span>
        <ScoreValue item={results.get(node.key)} max={node.max_score} range={rangeOf(node)} />
      </button>
      {open && (
        <div className="rule-group-body">
          <RuleBody node={node} get={get} set={set} results={results} />
        </div>
      )}
    </div>
  );
}

function RuleBody({ node, get, set, results }) {
  if (!node.children.length) return <Leaf node={node} get={get} set={set} results={results} />;
  let kids = node.children;
  let chooser = null;
  if (node.parameters?.choose_one) {
    const method = get("management_method");
    chooser = (
      <div className="seg-light" role="group" aria-label="경영상태 평가 방식">
        <button type="button" aria-pressed={method === "ratio"} onClick={() => set("management_method", "ratio")}>재무비율로 평가</button>
        <button type="button" aria-pressed={method === "credit"} onClick={() => set("management_method", "credit")}>신용평가등급으로 평가</button>
      </div>
    );
    const want = method === "ratio" ? "재무비율" : method === "credit" ? "신용평가등급" : null;
    kids = want ? kids.filter((c) => c.name === want).concat(kids.some((c) => c.name === want) ? [] : kids) : [];
  }
  // 가점·감점은 대부분 '해당 없음'이라, 남은 칸을 한 번에 0점으로 채우는 버튼을 둔다
  const unset = node.category === "BONUS" || node.category === "PENALTY"
    ? leavesOf(node).flatMap(noneValues).filter(([key]) => get(key) === undefined)
    : [];
  return (
    <>
      {unset.length > 0 && (
        <div className="none-rest">
          <button type="button" className="btn btn-sm" onClick={() => unset.forEach(([key, v]) => set(key, v))}>
            나머지 모두 해당 없음 (0점)
          </button>
          <span className="xsmall muted">해당하는 항목만 고르고 이 버튼을 누르면 바로 합계가 나옵니다.</span>
        </div>
      )}
      {chooser}
      {chooser && !kids.length && <p className="small muted">평가 방식을 먼저 고르세요. 업체가 둘 중 유리한 방식을 택해 제출합니다.</p>}
      {kids.map((c, i) =>
        c.children.length ? (
          <Group key={c.key} node={c} get={get} set={set} results={results} defaultOpen={i === kids.findIndex((x) => x.children.length)} />
        ) : (
          <Leaf key={c.key} node={c} get={get} set={set} results={results} />
        ),
      )}
    </>
  );
}

export default function ScoreTab({ notice }) {
  const { user, company: realCompany, testMode } = useAppState();
  // 테스트 업체는 서버에 없으므로 서버 호출에는 업체 ID를 넘기지 않는다
  const serverCompanyId = testMode ? undefined : realCompany?.id;
  const company = realCompany;
  const ev = useEvaluation(notice.id, serverCompanyId);
  const [inputs, setInputs] = useState({});
  const [result, setResult] = useState(null);
  const [calcError, setCalcError] = useState("");
  const [save, setSave] = useState({ state: "idle", at: null, error: "" });
  const dirty = useRef(false);

  // 이 업체가 전에 저장한 입력값을 불러온다
  useEffect(() => {
    if (!company) return undefined;
    if (testMode) {
      const d = load(`testDraft:${notice.id}`, null);
      if (d && !dirty.current) {
        setInputs(d.inputs);
        setSave({ state: "saved", at: d.updated_at, error: "" });
      }
      return undefined;
    }
    let alive = true;
    api.fetchDraft(company.id, notice.id).then((d) => {
      if (!alive || !d || dirty.current) return;
      setInputs(d.inputs ?? {});
      setSave({ state: "saved", at: d.updated_at, error: "" });
    }).catch(() => {});
    return () => {
      alive = false;
    };
  }, [company, notice.id, testMode]);

  const hasRules = ev.state === "done" && ev.evaluation.rules.length > 0;

  // 입력이 멈추면 서버에서 계산 (저장하지 않음)
  useEffect(() => {
    if (!hasRules) return undefined;
    let alive = true;
    const t = setTimeout(() => {
      api
        .calculateScore(notice.id, cleanInputs(inputs), serverCompanyId)
        .then((r) => {
          if (!alive) return;
          setResult(r);
          setCalcError("");
        })
        .catch((e) => alive && setCalcError(e.message));
    }, 350);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [hasRules, inputs, notice.id, serverCompanyId]);

  const results = useMemo(() => indexResults(result?.items), [result]);
  const get = (key) => inputs[key];
  const set = (key, value) => {
    dirty.current = true;
    setSave((s) => (s.state === "saved" ? { ...s, state: "changed" } : s));
    setInputs((all) => {
      const next = { ...all };
      if (value === undefined) delete next[key];
      else next[key] = value;
      return next;
    });
  };

  const doSave = async () => {
    setSave((s) => ({ ...s, state: "saving", error: "" }));
    try {
      if (testMode) {
        const at = new Date().toISOString();
        saveLocal(`testDraft:${notice.id}`, { inputs: cleanInputs(inputs), updated_at: at });
        setSave({ state: "saved", at, error: "" });
        return;
      }
      const r = await api.saveDraft(company.id, notice.id, cleanInputs(inputs));
      setResult(r);
      setSave({ state: "saved", at: new Date().toISOString(), error: "" });
    } catch (e) {
      setSave((s) => ({ ...s, state: "error", error: e.message }));
    }
  };

  if (ev.state === "loading") return <section className="card card-pad"><div className="skeleton" style={{ height: 200 }} /></section>;
  if (ev.state === "error") {
    return (
      <section className="card">
        <EmptyState title="배점을 불러오지 못했습니다" action={<button type="button" className="btn" onClick={ev.reload}>다시 시도</button>}>{ev.error.message}</EmptyState>
      </section>
    );
  }
  if (!hasRules) return <NoRules notice={notice} ev={ev} user={user} company={testMode ? null : company} testMode={testMode} />;

  const { evaluation } = ev;
  const pass = evaluation.summary?.pass_score ?? result?.pass_score ?? null;
  const top = evaluation.rules.filter((r) => r.rule_type !== "THRESHOLD");
  const price = top.find((r) => r.category === "PRICE");
  const others = top.filter((r) => r !== price);
  const total = result?.total ?? null;
  const partial = result
    ? fmtScore(top.reduce((sum, n) => {
        const it = result.items.find((i) => i.key === n.key);
        let v = partialOf(it) ?? 0;
        const r = rangeOf(n);
        if (r && it?.status !== "산정") v = Math.min(r[1], Math.max(r[0], v));
        return sum + v;
      }, 0))
    : null;

  return (
    <div className="score">
      <section className="card card-pad score-summary" aria-live="polite" aria-labelledby="score-sum-title">
        <div className="sum-main">
          <Gauge value={total ?? partial} tentative={total === null} pass={pass} />
          <div>
            <h2 id="score-sum-title" className="xsmall muted">{total !== null ? "예상 총점" : "현재까지 산정된 점수"}</h2>
            {total !== null ? (
              <p className="total num"><strong>{fmtScore(total)}</strong>점</p>
            ) : (
              <p className="total num tentative">
                <strong>{partial ?? "-"}</strong>점 <span className="badge badge-warn">미확정</span>
              </p>
            )}
          </div>
        </div>
        <div className="pass">
          <p className="xsmall muted"><Term>낙찰자 결정 기준 점수</Term></p>
          <p className="num"><strong>{pass ?? "-"}</strong>{pass != null && "점"}</p>
        </div>
        <div className="verdict">
          {total !== null && result.passed !== undefined ? (
            result.passed ? (
              <span className="badge badge-ok">기준보다 {fmtScore(result.gap)}점 높음</span>
            ) : (
              <span className="badge badge-danger">기준보다 {fmtScore(-result.gap)}점 부족</span>
            )
          ) : result?.pending?.length ? (
            <p className="small muted pending-line" title={result.pending.join(", ")}>
              <strong className="num">남은 항목 {result.pending.length}개</strong> · {result.pending.join(", ")}
            </p>
          ) : null}
        </div>
        <p className="xsmall muted span-all">
          업체가 입력한 정보와 기준서에서 자동 추출한 배점으로 계산한 <strong>참고용 시뮬레이션</strong>이며, 공식 심사 결과나 낙찰을 보장하지 않습니다.
          {evaluation.summary?.source === "업체 업로드" && " 우리 업체가 올린 기준서로 계산했습니다."}
        </p>
        <div className="span-all save-row">
          {user && company ? (
            <>
              <button type="button" className="btn btn-sm btn-primary" onClick={doSave} disabled={save.state === "saving"}>
                {save.state === "saving" ? "저장 중…" : "입력값 저장"}
              </button>
              <span className="xsmall muted">
                {save.state === "saved" && `저장됨 · ${save.at?.slice(0, 16).replace("T", " ")}`}
                {save.state === "changed" && "저장 후 바뀐 내용이 있습니다."}
                {save.state === "idle" && `${company.name} 기준으로 저장합니다. 다음에 열면 이어서 볼 수 있습니다.`}
              </span>
            </>
          ) : (
            <span className="xsmall muted">
              <Link to={user ? "/company" : "/login"}>{user ? "업체정보를 등록" : "로그인"}</Link>하면 입력값을 저장해 두고 이어서 계산할 수 있습니다.
            </span>
          )}
        </div>
        {save.error && <div className="span-all"><NoticeBox tone="danger" icon="!">{save.error}</NoticeBox></div>}
        {calcError && <div className="span-all"><NoticeBox tone="danger" icon="!">계산하지 못했습니다: {calcError}</NoticeBox></div>}
      </section>

      {evaluation.status === "확인 필요" && (
        <NoticeBox tone="warn" icon="!">{evaluation.reason}. &lsquo;원문 확인 필요&rsquo; 항목으로 낸 점수는 기준서 원문과 대조하세요.</NoticeBox>
      )}

      {price && <PriceSection node={price} notice={notice} get={get} set={set} item={results.get(price.key)} />}

      {others.map((node, i) => (
        <section key={node.key} className="card card-pad" aria-labelledby={`rule-${node.key}`} data-reveal>
          <div className="card-head">
            <h2 id={`rule-${node.key}`} className="section-title">
              {["②", "③", "④", "⑤", "⑥", "⑦", "⑧"][i] ?? "·"} {node.name}
              {node.max_score != null && <span className="muted small"> ({node.max_score}점)</span>}
            </h2>
            <ScoreValue item={results.get(node.key)} max={node.max_score} range={rangeOf(node)} />
          </div>
          {results.get(node.key)?.note && <p className="xsmall rule-note">{results.get(node.key).note}</p>}
          <RuleBody node={node} get={get} set={set} results={results} />
        </section>
      ))}

      <section className="card card-pad">
        <FeedbackReaction
          target="score"
          refId={notice.id}
          question="이 공고의 배점·산식과 계산 결과가 실제 기준서와 맞나요?"
          extra={{ total: result?.total ?? null, pass_score: pass }}
        />
      </section>
    </div>
  );
}

function PriceSection({ node, notice, get, set, item }) {
  const p = node.parameters ?? {};
  const usesA = p.subtract_a;
  return (
    <section className="card card-pad" aria-labelledby="price-title" data-reveal>
      <div className="card-head">
        <h2 id="price-title" className="section-title">① 입찰가격 <span className="muted small">({node.max_score}점)</span></h2>
        <ScoreValue item={item} max={node.max_score} />
      </div>
      <p className="section-desc">입찰가격과 예정가격을 입력하면 공고 기준서의 산식으로 계산합니다. 투찰가격을 추천하는 기능은 아닙니다.</p>

      <div className="price-grid">
        <MoneyInput label="입찰가격 (투찰 예정 금액)" required value={get("bid_price") ?? null} onChange={(v) => set("bid_price", v ?? undefined)} hint="써낼 금액을 입력하세요" />
        <MoneyInput label={<Term tabbable={false}>예정가격</Term>} required value={get("planned_price") ?? null} onChange={(v) => set("planned_price", v ?? undefined)} hint="개찰 때 정해지는 금액 (기초금액으로 대체하지 않음)" />
        {usesA && (
          <div className="field">
            <span className="field-label"><Term tabbable={false}>A값</Term> <span className="badge badge-brand">SRM 자동 수집</span></span>
            {notice.aAmount ? <p className="auto-val num">{won(notice.aAmount)}</p> : <p className="auto-val missing">{notice.aAmount === 0 ? "0원 (SRM 등록값) — 원문 확인" : "수집 안 됨 — 계산 보류"}</p>}
          </div>
        )}
      </div>

      {item?.status === "산정" && item.ratio != null && (
        <p className="small muted">
          산식 비율 <strong className="num">{(item.ratio * 100).toFixed(3)}%</strong>
          {p.vars?.["기준율"] != null && <> (기준 {p.vars["기준율"]}%와 {Math.abs(p.vars["기준율"] - item.ratio * 100).toFixed(3)}%p 차이)</>}
        </p>
      )}
      {item?.note && <NoticeBox tone={item.status === "산정" ? "warn" : "danger"} icon="!">{item.note}</NoticeBox>}

      <details className="formula">
        <summary tabIndex={-1}>적용 산식 보기</summary>
        <p className="num">{node.formula ?? "산식 없음"}</p>
        <p className="xsmall muted">
          {p.min_score != null && `최저 ${p.min_score}점 · `}
          {p.cap && `예정가격의 ${p.cap.ratio}% 이상이면 ${p.cap.score}점 · `}
          {node.rounding && `소수점 ${node.rounding.digits}자리 반올림 · `}
          출처: {node.source_file}
        </p>
      </details>
    </section>
  );
}

/** 배점이 없을 때: 이유 + (로그인·업체 있으면) 기준서 직접 올리기 */
function NoRules({ notice, ev, user, company, testMode }) {
  const [file, setFile] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const up = ev.uploaded;

  const upload = async () => {
    setBusy(true);
    setError("");
    try {
      await api.uploadCriteria(company.id, notice.id, file);
      setFile(null);
      ev.reload();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    setError("");
    try {
      await api.deleteUploadedCriteria(company.id, notice.id);
      ev.reload();
    } catch (e) {
      setError(e.message);
    }
  };

  return (
    <section className="card card-pad">
      <EmptyState title="배점을 확인하지 못해 점수를 계산할 수 없습니다">
        {ev.evaluation.reason}. 임의 기준으로 계산하지 않습니다.
      </EmptyState>
      {user && company ? (
        <div className="upload-box">
          <h3 className="card-title">적격심사기준 파일 직접 올리기</h3>
          <p className="small muted">SRM에서 받은 적격심사기준(hwp·hwpx·pdf, 20MB 이하)을 올리면 배점을 추출해 우리 업체 계산에만 씁니다.</p>
          {up && (
            <p className="small">
              올린 파일: <strong>{up.filename}</strong> · {up.read_status === "SUCCESS" ? (up.rule_extracted ? "배점 추출됨" : "읽었지만 배점 추출 실패") : "파일을 읽지 못함"}
              <button type="button" className="btn btn-ghost btn-sm" onClick={remove} style={{ marginLeft: 8 }}>지우기</button>
            </p>
          )}
          <div className="join-row">
            <input type="file" accept=".hwp,.hwpx,.pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            <button type="button" className="btn btn-primary btn-sm" onClick={upload} disabled={!file || busy}>{busy ? "올리는 중…" : "올리기"}</button>
          </div>
          {error && <NoticeBox tone="danger" icon="!">{error}</NoticeBox>}
        </div>
      ) : testMode ? (
        <p className="small muted" style={{ textAlign: "center" }}>테스트 모드에서는 기준서 직접 올리기를 쓸 수 없습니다 (서버에 업체가 있어야 함).</p>
      ) : (
        <p className="small muted" style={{ textAlign: "center" }}>
          <Link to={user ? "/company" : "/login"}>{user ? "업체정보를 등록" : "로그인"}</Link>하면 적격심사기준 파일을 직접 올려 계산할 수 있습니다.
        </p>
      )}
    </section>
  );
}
