/**
 * AI 질문 패널의 '규칙 기반' 답변 (LLM 없이, 화면이 이미 받은 API 데이터로 답한다).
 * 질문에서 핵심 단어를 찾아 유형을 정하고, 정해진 문장 틀에 데이터를 넣는다.
 * 백엔드 POST /api/qna 가 생기면 그쪽(LLM) 답을 먼저 쓰고, 없을 때만 이 답을 쓴다 (QnaPanel).
 *
 * answer(question, ctx) → { text, sources: [{ label, notice_no? }] }
 * ctx: { notice(질문 기준 공고, 판정 포함) | null, notices(전체 목록), company, loadEvaluation(no), loadSchedule(no) }
 */
import { GLOSSARY } from "./glossary.js";
import { fullDateTime, shortDateTime, shortKoreanWon, timeLeft, won } from "./format.js";
import { nextDeadline } from "./notice.js";

const JUDGE = { MATCH: "참가 가능(1차)", REVIEW: "입찰참가자격 확인 필요", MISMATCH: "조건 불일치", NOT_APPLICABLE: "해당 없음" };
const ITEM = { MATCH: "충족", REVIEW: "확인 필요", MISMATCH: "불일치", NOT_APPLICABLE: "제한 없음" };
const AREAS = [
  ["서울", ["S"]], ["인천", ["H"]], ["경기", ["G"]], ["강원", ["F"]], ["충북", ["C"]], ["대전", ["D"]], ["세종", ["L"]],
  ["충남", ["R"]], ["전북", ["J"]], ["광주", ["I"]], ["전남", ["I"]], ["대구", ["T"]], ["경북", ["K"]], ["부산", ["B"]],
  ["울산", ["Z"]], ["경남", ["M"]], ["제주", ["E"]], ["수도권", ["S", "H", "G"]], ["충청", ["C", "D", "L", "R"]],
  ["호남", ["J", "I"]], ["영남", ["T", "K", "B", "Z", "M"]],
];
const TERM_ALIASES = { a값: "A값", 에이값: "A값", 기준점수: "낙찰자 결정 기준 점수", 통과점수: "낙찰자 결정 기준 점수", 참가신청: "입찰참가신청", 정정: "정정공고" };

const price = (v) => (v ? `${won(v)} (${shortKoreanWon(v)})` : "정보 없음");
const isActive = (n) => n.status !== "CLOSED" && n.status !== "CANCELLED";
const noticeLine = (n) => {
  const d = nextDeadline(n);
  return `· ${n.title}\n   ${d.label} ${shortDateTime(d.iso)} · 추정가격 ${n.estimatedPrice ? shortKoreanWon(n.estimatedPrice) : "-"}${n.judgment?.overall ? ` · ${JUDGE[n.judgment.overall]}` : ""}`;
};
const src = (n, label) => ({ label: label ?? n.title, notice_no: n.id });

/* ───────── 용어 ───────── */
function findTerm(q) {
  const lower = q.toLowerCase().replace(/\s+/g, "");
  for (const [alias, term] of Object.entries(TERM_ALIASES)) if (lower.includes(alias)) return term;
  return Object.keys(GLOSSARY)
    .sort((a, b) => b.length - a.length)
    .find((k) => lower.includes(k.toLowerCase().replace(/\s+/g, "")));
}

/* ───────── 공고 1건 ───────── */
function judgmentAnswer(n, company) {
  if (!company) return { text: "업체정보를 등록하면 이 공고의 참가 자격을 우리 업체와 비교해 드립니다. 업체정보 메뉴에서 등록해 주세요.", sources: [] };
  const j = n.judgment;
  const lines = [`1차 판정: ${JUDGE[j.overall] ?? "확인 필요"}`];
  for (const it of j.items) lines.push(`· ${it.label}: ${ITEM[it.status] ?? "-"} — ${it.reason}`);
  if (j.overall === "REVIEW") lines.push("\n'확인 필요' 항목은 공고문의 입찰참가자격에서 직접 확인해 주세요.");
  if (j.overall === "MATCH") lines.push("\nSRM 조건 기준 1차 검토 결과이며, 공고문 추가 조건(실적·공동수급 등)은 따로 확인이 필요합니다.");
  return { text: lines.join("\n"), sources: [src(n, "공고 상세 · 판정표")] };
}

function conditionAnswer(n, key, company) {
  const it = n.judgment.items.find((i) => i.key === key);
  if (!it) return null;
  const lines = [`${it.label} 조건: ${it.required}`];
  if (company && it.status) lines.push(`우리 업체: ${it.company} → ${ITEM[it.status]}`, it.reason);
  else if (!company) lines.push("업체정보를 등록하면 우리 업체와 비교해 드립니다.");
  if (it.mode === "MANUAL") lines.push("이 조건은 발주처 담당자가 직접 심사하는 수동심사 항목입니다.");
  return { text: lines.join("\n"), sources: [src(n, "공고 상세 · 판정표")] };
}

function scheduleAnswer(n) {
  const s = n.schedule;
  const row = (label, iso) => `· ${label}: ${iso ? fullDateTime(iso) : "미정"}${iso && timeLeft(iso) ? ` (${timeLeft(iso)})` : iso ? " (지남)" : ""}`;
  const lines = [
    row("참가신청 시작", s.applyStart),
    row("참가신청 마감", s.applyEnd),
    row("투찰 마감", s.bidEnd),
    row("개찰", s.opening),
  ];
  return { text: `${n.title} 일정입니다.\n${lines.join("\n")}`, sources: [src(n, "공고 상세 · 일정")] };
}

function amountAnswer(n, q) {
  const lines = [];
  const wantA = /a값|에이값/i.test(q);
  if (!wantA || /추정|기초/.test(q)) lines.push(`· 추정가격: ${price(n.estimatedPrice)}`, `· 기초금액: ${price(n.basePrice)}`);
  if (n.aAmount === 0) lines.push("· A값: 해당 없음 (SRM에 0으로 등록 — 주로 A값을 쓰지 않는 용역 공고)");
  else lines.push(`· A값: ${n.aAmount == null ? "수집 안 됨" : price(n.aAmount)}`);
  return { text: lines.join("\n"), sources: [src(n, "공고 상세 · 금액")] };
}

async function evaluationAnswer(n, ctx) {
  const ev = await ctx.loadEvaluation(n.id);
  if (!ev.rules?.length) return { text: `이 공고는 배점을 아직 확인하지 못했습니다 (${ev.reason ?? "사유 없음"}). 점수 계산 탭에서 적격심사기준을 직접 올릴 수 있습니다.`, sources: [src(n, "점수 계산 탭")] };
  const pass = ev.summary?.pass_score;
  const lines = [];
  if (pass != null) lines.push(`낙찰자 결정 기준 점수는 ${pass}점 이상입니다.`);
  const priceRule = ev.rules.find((r) => r.category === "PRICE");
  if (priceRule) lines.push(`· 입찰가격 ${priceRule.max_score}점: ${priceRule.formula ?? "산식 없음"}${priceRule.parameters?.subtract_a ? " (A값을 뺀 금액으로 계산)" : ""}`);
  for (const r of ev.rules.filter((x) => !["PASS_SCORE", "PRICE"].includes(x.category))) {
    lines.push(`· ${r.name} ${r.max_score == null ? "(가감점)" : `${r.max_score}점`}${r.children?.length ? ` — ${r.children.map((c) => c.name).slice(0, 4).join(", ")}${r.children.length > 4 ? " 등" : ""}` : ""}`);
  }
  if (ev.status === "확인 필요") lines.push(`\n${ev.reason}. 원문과 대조가 필요합니다.`);
  lines.push("\n예상 점수는 '점수 계산' 탭에서 직접 계산해 볼 수 있습니다.");
  return { text: lines.join("\n"), sources: [src(n, "점수 계산 탭 · 배점")] };
}

async function docDueAnswer(n, ctx) {
  const r = await ctx.loadSchedule(n.id);
  const due = r.events?.find((e) => e.event_type === "QUALIFICATION_DOC_DUE");
  if (!due) return { text: "이 공고의 기준서에서 적격심사 서류 제출기한 문구를 찾지 못했습니다. 공고문과 적격심사기준 원문을 확인해 주세요.", sources: [src(n, "원본 문서")] };
  const days = due.note?.match(/(\d+)\s*일\s*이상/)?.[1];
  return {
    text: [
      "적격심사 서류 제출기한은 미리 정해진 날짜가 없습니다. 개찰 후 적격심사 대상자로 통보를 받으면 그때 정해집니다.",
      days ? `기준서상 통보일로부터 ${days}일 이상의 기한이 주어집니다.` : null,
      `\n기준서 원문: "${due.note}"`,
    ].filter(Boolean).join("\n"),
    sources: [src(n, "공고 상세 · 서류 기한")],
  };
}

function summaryAnswer(n) {
  const d = nextDeadline(n);
  return {
    text: [
      `${n.title}`,
      `· ${n.issuer} · ${n.category}`,
      `· 추정가격 ${price(n.estimatedPrice)}`,
      `· ${d.label} ${fullDateTime(d.iso)}${timeLeft(d.iso) ? ` (${timeLeft(d.iso)})` : ""}`,
      `· 지역: ${n.regionLabel}`,
      n.judgment?.overall ? `· 1차 판정: ${JUDGE[n.judgment.overall]}` : null,
    ].filter(Boolean).join("\n"),
    sources: [src(n, "공고 상세")],
  };
}

/* ───────── 목록 질문 ───────── */
function listAnswer(q, ctx) {
  const { notices, company } = ctx;
  let list = notices.filter(isActive);
  const conds = [];
  // 업체가 있으면 기본은 추천(조건 불일치 제외)
  if (company && !/전체|모든|불일치/.test(q)) {
    list = list.filter((n) => n.judgment?.overall !== "MISMATCH");
    conds.push("추천 공고");
  }
  if (/참가\s*가능/.test(q)) {
    list = list.filter((n) => n.judgment?.overall === "MATCH");
    conds.push("참가 가능");
  } else if (/확인\s*필요/.test(q)) {
    list = list.filter((n) => n.judgment?.overall === "REVIEW");
    conds.push("확인 필요");
  }
  if (/용역/.test(q)) {
    list = list.filter((n) => n.type === "SERVICE");
    conds.push("용역");
  } else if (/공사\s*(공고|만|는|가)/.test(q) || /^공사/.test(q)) {
    list = list.filter((n) => n.type === "CONSTRUCTION");
    conds.push("공사");
  }
  const area = AREAS.find(([name]) => q.includes(name));
  if (area) {
    list = list.filter((n) => n.areas?.some((c) => area[1].includes(c)));
    conds.push(`${area[0]} 지역(지역 제한 또는 발주 본부 기준)`);
  }
  const now = Date.now();
  const within = (days) => (n) => new Date(nextDeadline(n).iso).getTime() - now <= days * 86400000;
  if (/오늘/.test(q)) {
    const end = new Date(); end.setHours(23, 59, 59, 999);
    list = list.filter((n) => new Date(nextDeadline(n).iso) <= end);
    conds.push("오늘 마감");
  } else if (/내일/.test(q)) {
    const end = new Date(); end.setDate(end.getDate() + 1); end.setHours(23, 59, 59, 999);
    list = list.filter((n) => new Date(nextDeadline(n).iso) <= end);
    conds.push("내일까지 마감");
  } else if (/이번\s*주|일주일|7일/.test(q)) {
    list = list.filter(within(7));
    conds.push("7일 안에 마감");
  }
  let sortLabel = "마감 임박순";
  if (/(금액|가격).*(큰|높|많)|(큰|높은|비싼)\s*(공고|금액)/.test(q)) {
    list = [...list].sort((a, b) => (b.estimatedPrice ?? 0) - (a.estimatedPrice ?? 0));
    sortLabel = "금액 높은순";
  } else if (/(금액|가격).*(작|낮|적)|(작은|낮은|싼)\s*(공고|금액)/.test(q)) {
    list = [...list].sort((a, b) => (a.estimatedPrice ?? Infinity) - (b.estimatedPrice ?? Infinity));
    sortLabel = "금액 낮은순";
  } else {
    list = [...list].sort((a, b) => new Date(nextDeadline(a).iso) - new Date(nextDeadline(b).iso));
  }
  const head = `${conds.length ? conds.join(" · ") : "진행 중 공고"} ${list.length}건입니다.`;
  if (!list.length) return { text: `${head}\n조건을 바꿔 다시 물어보거나, 추천 공고 목록의 필터를 이용해 보세요.`, sources: [] };
  const top = list.slice(0, 5);
  return {
    text: `${head}${list.length > 5 ? ` (${sortLabel} 5건)` : ""}\n${top.map(noticeLine).join("\n")}`,
    sources: top.map((n) => src(n)),
  };
}

/* ───────── 도움말 ───────── */
const HELP = (hasNotice) =>
  [
    "아직 이 질문은 이해하지 못했어요. 지금은 이런 질문에 답할 수 있습니다.",
    hasNotice ? "· 이 공고: 참가할 수 있어? / 면허·지역 조건은? / 마감 언제야? / 추정가격·A값 얼마야? / 기준점수는? / 적격심사 서류 언제까지?" : "· 공고를 열고 질문하면 그 공고의 조건·일정·금액·배점을 알려 드립니다.",
    "· 공고 찾기: 이번 주 마감 공고 / 참가 가능한 공고 / 경기 지역 공고 / 용역 공고 / 금액 큰 공고",
    "· 용어: A값이 뭐야? / 적격심사가 뭐야?",
  ].join("\n");

/** 질문 → 답 (비동기: 배점·일정은 필요할 때만 서버에서 받는다) */
export async function answer(question, ctx) {
  const q = question.trim();
  const n = ctx.notice;
  const asksMeaning = /(뭐야|뭔가요|뭐예요|무엇|뜻|의미|이란|란\?|설명해)/.test(q);
  const term = findTerm(q);

  // 1) 용어 설명 ("A값이 뭐야?") — 단, 공고 기준에서 '얼마'를 물으면 금액으로
  if (term && asksMeaning && !(n && /얼마|금액은|몇/.test(q))) {
    return { text: `${term}: ${GLOSSARY[term]}`, sources: [{ label: "입찰 용어 사전" }] };
  }

  // 2) 공고 1건에 대한 질문
  const listy = /(공고|목록).*(알려|보여|찾아|있어|몇)|몇\s*(개|건)|이번\s*주|오늘|내일|일주일|7일/.test(q) && !/이\s*공고/.test(q);
  if (n && !listy) {
    if (/서류/.test(q) && /(기한|언제|마감|제출|까지)/.test(q)) return docDueAnswer(n, ctx);
    if (/면허/.test(q)) return conditionAnswer(n, "license", ctx.company) ?? judgmentAnswer(n, ctx.company);
    if (/지역/.test(q)) return conditionAnswer(n, "region", ctx.company) ?? judgmentAnswer(n, ctx.company);
    if (/주력\s*분야/.test(q)) return conditionAnswer(n, "licenseMain", ctx.company) ?? judgmentAnswer(n, ctx.company);
    if (/제한경쟁|자격\s*조건/.test(q)) return conditionAnswer(n, "qualification", ctx.company) ?? judgmentAnswer(n, ctx.company);
    if (/(기준\s*점수|통과|커트|배점|산식|점수|적격\s*심사)/.test(q)) return evaluationAnswer(n, ctx);
    if (/(추정\s*가격|기초\s*금액|a값|에이값|금액|얼마|가격)/i.test(q)) return amountAnswer(n, q);
    if (/(마감|언제|일정|개찰|투찰|남은|시간|며칠)/.test(q)) return scheduleAnswer(n);
    if (/(참가|참여|입찰할\s*수|자격|가능|될까|되나|돼\?)/.test(q)) return judgmentAnswer(n, ctx.company);
    if (/(어디|발주|부서|요약|정리|알려|뭐야|무슨)/.test(q)) return summaryAnswer(n);
  }

  // 3) 공고 찾기·개수
  if (listy || /(공고|목록|추천)/.test(q)) return listAnswer(q, ctx);

  // 4) 용어 (물음 표현 없이 용어만: "A값?")
  if (term) return { text: `${term}: ${GLOSSARY[term]}`, sources: [{ label: "입찰 용어 사전" }] };

  return { text: HELP(Boolean(n)), sources: [], help: true };
}
