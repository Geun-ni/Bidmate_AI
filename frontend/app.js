// 한전 입찰 공고 화면: /api/notices (수집기 DB bidmate.sqlite3) 를 읽어 목록·상세를 보여 준다.
const won = new Intl.NumberFormat("ko-KR");
const UNKNOWN = "미확인";
const noticeButtons = new Map();
const element = (id) => document.getElementById(id);

function create(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

const money = (value) => (value === null || value === undefined ? UNKNOWN : `${won.format(value)}원`);
const datetime = (value) => (value ? value.replace("T", " ").slice(0, 16) : UNKNOWN);
const period = (begin, end) => `${datetime(begin)} ~ ${datetime(end)}`;
const noticeDate = (value) => (/^\d{8}$/.test(value || "") ? `${value.slice(0, 4)}-${value.slice(4, 6)}-${value.slice(6)}` : value || UNKNOWN);

function rows(target, fields) {
  element(target).replaceChildren(...fields.map(([label, value]) => {
    const row = create("div", undefined, "summary-row");
    row.append(create("dt", label), create("dd", value ?? UNKNOWN));
    return row;
  }));
}

function showConditions(target, condition) {
  const box = element(target);
  if (!condition || condition.status === "확인 필요") {
    box.replaceChildren(create("p", "확인 필요", "need-check"),
      create("p", condition?.reason || "아직 조회하지 않음", "meta"));
    return;
  }
  const auto = condition.auto_eval ? ` · 자동심사 ${condition.auto_eval}` : "";
  if (!condition.items.length) {
    box.replaceChildren(create("p", `제한 없음${auto}`));
    return;
  }
  const list = create("ul", undefined, "reasons");
  list.append(...condition.items.map(({ code, name }) => create("li", `${name || UNKNOWN} (${code || UNKNOWN})`)));
  box.replaceChildren(list, create("p", `SRM 조회 결과${auto}`, "meta"));
}

async function showNotice(noticeNo) {
  for (const [id, button] of noticeButtons) button.setAttribute("aria-pressed", String(id === noticeNo));
  const response = await fetch(`/api/notices/${encodeURIComponent(noticeNo)}`);
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  const notice = await response.json();
  element("notice-title").textContent = notice.title || UNKNOWN;
  element("notice-meta").textContent = `${notice.notice_no} · ${notice.place_name || UNKNOWN}`;
  rows("basic", [
    ["공고번호", notice.notice_no],
    ["공고일", noticeDate(notice.notice_date)],
    ["참가신청", period(notice.attend_req_begin_at, notice.attend_req_close_at)],
    ["투찰", period(notice.bid_begin_at, notice.bid_end_at)],
    ["추정가격", money(notice.presumed_price)],
    ["기초금액", money(notice.estimated_price_basic_amount)],
  ]);
  showConditions("license", notice.license);
  showConditions("qualification", notice.qualification);
  element("detail-status").textContent = notice.detail_status;
  rows("srm", [
    ["A금액", money(notice.a_amount)],
    ["경비", money(notice.overhead_cost_amount)],
    ["개찰", datetime(notice.open_bid_at)],
    ["마지막 수집", datetime(notice.last_collected_at)],
  ]);
}

function showLastRun(run) {
  if (!run) {
    element("last-run").textContent = "수집 실행 기록 없음";
    return;
  }
  const counts = run.completed_count === undefined || run.completed_count === null
    ? `처리 ${run.processed_count ?? 0}건`
    : `신규 ${run.new_notice_count ?? 0}건 · 갱신 ${(run.updated_count ?? 0) + (run.corrected_count ?? 0)}건 · `
      + `상세 완료 ${run.completed_count}건 · 실패 ${run.failed_count}건 · 미처리 ${run.deferred_count}건`;
  const mode = run.mode === "full" ? "최초 적재" : "수집";
  const when = run.finished_at ? datetime(run.finished_at) : `${datetime(run.started_at)} 시작(진행 중이거나 중단됨)`;
  element("last-run").textContent = `마지막 ${mode} ${when} · ${counts}`;
}

async function loadNotices() {
  const status = element("status");
  status.hidden = false;
  status.textContent = "공고를 불러오는 중입니다.";
  element("workspace").hidden = true;
  noticeButtons.clear();
  try {
    const query = element("include-closed").checked ? "?include_closed=1" : "";
    const response = await fetch(`/api/notices${query}`);
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
    showLastRun(data.last_run);
    if (!data.notices.length) {
      status.textContent = "저장된 공고가 없습니다. 수집을 먼저 실행해 주세요.";
      return;
    }
    const items = data.notices.map((notice) => {
      const item = create("li");
      const button = create("button", undefined, "notice-button");
      button.type = "button";
      button.setAttribute("aria-controls", "detail");
      button.append(
        create("span", notice.title || UNKNOWN, "notice-name"),
        create("span", notice.notice_no, "meta"),
        create("span", `신청 ${period(notice.attend_req_begin_at, notice.attend_req_close_at)}`, "meta"),
        create("span", `투찰 ${period(notice.bid_begin_at, notice.bid_end_at)}`, "meta"),
        create("span", `추정가격 ${money(notice.presumed_price)}`, "notice-budget"),
      );
      if (notice.detail_status !== "완료") button.append(create("span", `SRM ${notice.detail_status}`, "tag"));
      button.addEventListener("click", () => showNotice(notice.notice_no).catch(showError));
      noticeButtons.set(notice.notice_no, button);
      item.append(button);
      return item;
    });
    element("notices").replaceChildren(...items);
    element("notice-count").textContent = `${data.count}건`;
    await showNotice(data.notices[0].notice_no);
    element("workspace").hidden = false;
    status.hidden = true;
  } catch (error) {
    showError(error);
  }
}

function showError(error) {
  console.error(error);
  const status = element("status");
  status.hidden = false;
  status.textContent = `공고를 불러오지 못했습니다 (${error.message}). 서버와 DB 파일을 확인한 뒤 새로고침해 주세요.`;
}

element("include-closed").addEventListener("change", loadNotices);
loadNotices();
