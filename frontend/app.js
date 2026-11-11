const won = new Intl.NumberFormat("ko-KR");
const checklistStates = new Map();
const noticeButtons = new Map();
const element = (id) => document.getElementById(id);

function create(tag, text, className) {
  const node = document.createElement(tag);
  if (text !== undefined) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function showProfile(profile) {
  element("company-title").textContent = profile.name;
  const fields = [
    ["보유 면허", profile.licenses.join(", ")],
    ["소재 지역", profile.region],
    ["희망 금액", `${won.format(profile.min_budget)}원 ~ ${won.format(profile.max_budget)}원`],
  ];
  element("company-criteria").replaceChildren(...fields.map(([label, value]) => {
    const field = create("div");
    field.append(create("dt", label), create("dd", value));
    return field;
  }));
  element("company").hidden = false;
}

function showSummary(summary) {
  const rows = summary.map(({ label, value, source }) => {
    const row = create("div", undefined, "summary-row");
    const description = create("dd", value);
    if (source) {
      const details = create("details");
      details.append(create("summary", "원문 예시"), create("blockquote", source));
      description.append(details);
    }
    row.append(create("dt", label), description);
    return row;
  });
  element("summary").replaceChildren(...rows);
}

function showChecklist(notice, profile) {
  if (!checklistStates.has(notice.id)) {
    const held = notice.documents
      .filter(({ name }) => profile.held_documents.includes(name))
      .map(({ name }) => name);
    checklistStates.set(notice.id, new Set(held));
  }
  const checked = checklistStates.get(notice.id);
  const updateProgress = () => {
    element("progress").textContent = `${checked.size}/${notice.documents.length}개 준비`;
  };
  const rows = notice.documents.map(({ name, source }) => {
    const row = create("li");
    const label = create("label");
    const checkbox = create("input");
    checkbox.type = "checkbox";
    checkbox.checked = checked.has(name);
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) checked.add(name);
      else checked.delete(name);
      updateProgress();
    });
    label.append(checkbox, create("span", name));
    row.append(label, create("small", source || "원문 근거 확인 필요"));
    return row;
  });
  element("documents").replaceChildren(...rows);
  if (rows.length === 0) {
    element("documents").append(create("li", "필요 서류를 원문에서 확인하세요."));
  }
  updateProgress();
}

function showNotice(notice, profile) {
  for (const [id, button] of noticeButtons) {
    button.setAttribute("aria-pressed", String(id === notice.id));
  }
  element("notice-title").textContent = notice.title;
  element("notice-meta").textContent = `${notice.issuer} · ${notice.id}`;
  element("reasons").replaceChildren(...notice.reasons.map((reason) => create("li", reason)));
  showSummary(notice.summary);
  showChecklist(notice, profile);
}

async function loadDemo() {
  const status = element("status");
  try {
    const response = await fetch("/api/demo");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const { profile, recommendations } = await response.json();
    showProfile(profile);
    if (!recommendations.length) {
      status.textContent = "업체 조건에 맞는 가상 공고가 없습니다.";
      return;
    }

    for (const notice of recommendations) {
      const item = create("li");
      const button = create("button", undefined, "notice-button");
      button.type = "button";
      button.setAttribute("aria-controls", "detail");
      button.append(
        create("span", notice.title, "notice-name"),
        create("span", `${won.format(notice.budget)}원`, "notice-budget"),
        create("span", `마감 ${notice.deadline}`, "meta"),
      );
      button.addEventListener("click", () => showNotice(notice, profile));
      noticeButtons.set(notice.id, button);
      item.append(button);
      element("notices").append(item);
    }
    element("notice-count").textContent = `${recommendations.length}건`;
    showNotice(recommendations[0], profile);
    element("workspace").hidden = false;
    status.hidden = true;
  } catch (error) {
    console.error(error);
    status.textContent = "공고를 불러오지 못했습니다. 서버를 확인한 뒤 새로고침해 주세요.";
  }
}

loadDemo();
