const API_BASE = window.location.origin;
const apiUrl = (path) => `${API_BASE}${path}`;

if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/static/sw.js").catch(() => {});
  });
}

let currentMeds = [];
let currentMedDetails = [];
let selectedCurrentMedIds = new Set();
let lastAnalyze = null;
let selectedImageState = { current: [], new: [] };
let autoActionTimers = { current: null, new: null };
let isAutoRegisteringCurrent = false;
let isAutoAnalyzingNew = false;

window.onload = function () {
  const savedNick = localStorage.getItem("safepill_nickname");
  if (savedNick) {
    showMainScreen(savedNick);
  }
};

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function buildPreviewText(text, maxLen = 180) {
  const value = String(text || "").trim();
  if (!value) return "-";
  return value.length > maxLen ? `${value.slice(0, maxLen)}...` : value;
}

function formatConfidence(value) {
  const conf = Number(value || 0);
  const confPct = conf <= 1 ? conf * 100 : conf;
  return `${confPct.toFixed(1)}%`;
}

function normalizeList(value) {
  return Array.isArray(value) ? value.filter(Boolean) : [];
}

function getSelectedCurrentDetails() {
  return currentMedDetails.filter((item) =>
    selectedCurrentMedIds.has(String(item.id)),
  );
}

function updateSelectedMedSummary() {
  const summaryEl = document.getElementById("selected-med-summary");
  const toolbarStatus = document.getElementById("med-toolbar-status");
  const librarySummary = document.getElementById("current-med-library-summary");
  const total = currentMedDetails.length;
  const selected = getSelectedCurrentDetails();
  if (!total) {
    summaryEl.innerText = "저장된 약이 없습니다.";
    toolbarStatus.innerText = "비교 대상 0개 선택";
    librarySummary.innerText = "현재 복용 약 목록 펼치기";
    return;
  }
  summaryEl.innerText = `저장 ${total}개 중 비교 대상 ${selected.length}개 선택됨`;
  toolbarStatus.innerText = `비교 대상 ${selected.length}개 선택`;
  librarySummary.innerText = `현재 복용 약 목록 (${selected.length}/${total})`;
}

function toggleAllCurrentMeds(checked) {
  selectedCurrentMedIds = checked
    ? new Set(currentMedDetails.map((item) => String(item.id)).filter(Boolean))
    : new Set();
  renderMeds();
}

async function handleLogin() {
  const nick = document.getElementById("nickname").value.trim();
  const pin = document.getElementById("pin").value.trim();

  if (!nick || !pin) {
    alert("정보를 모두 입력해주세요!");
    return;
  }

  if (pin.length !== 4) {
    alert("PIN 번호는 반드시 4자리를 입력해주세요!");
    return;
  }

  try {
    const response = await fetch(apiUrl("/login"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: nick, pin: pin }),
    });

    const result = await response.json();
    if (result.status === "success") {
      localStorage.setItem("safepill_nickname", nick);
      alert(result.message);
      showMainScreen(nick);
    } else {
      alert(result.message);
    }
  } catch (error) {
    alert("서버 통신 오류! 백엔드가 켜져있는지 확인하세요.");
  }
}

function handleLogout() {
  localStorage.removeItem("safepill_nickname");
  location.reload();
}

function showMainScreen(nickname) {
  document.getElementById("login-section").style.display = "none";
  document.getElementById("main-section").style.display = "block";
  document.getElementById("user-greeting").innerText = nickname;
  loadMeds(nickname);
}

async function loadMeds(nickname) {
  try {
    const response = await fetch(apiUrl(`/meds/${nickname}`));
    const data = await response.json();
    currentMedDetails = Array.isArray(data.items)
      ? data.items
      : (data.meds || []).map((name, index) => ({
          id: index + 1,
          name,
          active_ingredients: [],
          source_type: "manual",
        }));
    currentMeds = currentMedDetails.map((item) => item.name).filter(Boolean);

    const allIds = currentMedDetails
      .map((item) => String(item.id))
      .filter(Boolean);
    const kept = allIds.filter((id) => selectedCurrentMedIds.has(id));
    if (allIds.length === 0) {
      selectedCurrentMedIds = new Set();
    } else if (kept.length > 0) {
      selectedCurrentMedIds = new Set(kept);
    } else {
      selectedCurrentMedIds = new Set(allIds);
    }
    renderMeds();
  } catch (error) {
    console.error("약통 불러오기 실패", error);
  }
}

async function saveMedName(name, options = {}) {
  const nick = localStorage.getItem("safepill_nickname");
  const medicineName = (name || "").trim();
  if (!nick || !medicineName) return null;

  const response = await fetch(apiUrl("/add_med"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nickname: nick, medicine_name: medicineName }),
  });
  const result = await response.json();
  await loadMeds(nick);

  if (options.showAlert) {
    alert(`${result.saved_name || medicineName} 약이 약통에 저장됐습니다.`);
  }
  return result;
}

async function saveMedDetail(payload, options = {}) {
  const nick = localStorage.getItem("safepill_nickname");
  if (!nick) return null;
  const response = await fetch(apiUrl("/add_med_detail"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      nickname: nick,
      medicine_name: (payload.medicine_name || "").trim(),
      active_ingredients: normalizeList(payload.active_ingredients),
      source_type: payload.source_type || "manual",
      ocr_text: payload.ocr_text || "",
    }),
  });
  const result = await response.json();
  if (!response.ok || result.status === "fail") {
    throw new Error(result.message || "server error");
  }
  await loadMeds(nick);
  if (options.showAlert) {
    alert(`${result.saved_name || "약"}이(가) 약통에 저장됐습니다.`);
  }
  return result;
}

async function addMed() {
  const input = document.getElementById("med-input");
  const val = input.value.trim();
  if (!val) return;

  try {
    await saveMedName(val);
    input.value = "";
  } catch (error) {
    alert("약 추가 실패!");
  }
}

async function removeMed(medName) {
  const nick = localStorage.getItem("safepill_nickname");
  try {
    await fetch(apiUrl("/delete_med"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nickname: nick, medicine_name: medName }),
    });
    await loadMeds(nick);
  } catch (error) {
    alert("약 삭제 실패!");
  }
}

function renderMeds() {
  const ul = document.getElementById("med-list");
  ul.innerHTML = "";
  if (currentMedDetails.length === 0) {
    ul.innerHTML = `<li style="color:#777;font-size:13px;">저장된 약이 없습니다.</li>`;
    updateSelectedMedSummary();
    return;
  }

  currentMedDetails.forEach((item) => {
    const li = document.createElement("li");
    li.className = "med-row";

    const top = document.createElement("div");
    top.className = "med-row-top";

    const selectWrap = document.createElement("div");
    selectWrap.className = "med-select-wrap";

    const check = document.createElement("input");
    check.type = "checkbox";
    check.className = "med-checkbox";
    check.checked = selectedCurrentMedIds.has(String(item.id));
    check.onchange = () => {
      const key = String(item.id);
      if (check.checked) {
        selectedCurrentMedIds.add(key);
      } else {
        selectedCurrentMedIds.delete(key);
      }
      updateSelectedMedSummary();
    };
    selectWrap.appendChild(check);

    const nameWrap = document.createElement("div");
    nameWrap.style.flex = "1";

    const nameSpan = document.createElement("div");
    nameSpan.textContent = item.name || "-";
    nameSpan.style.cssText = "font-weight:700;word-break:break-word;";
    nameWrap.appendChild(nameSpan);

    const sub = document.createElement("div");
    sub.className = "muted";
    if (
      Array.isArray(item.active_ingredients) &&
      item.active_ingredients.length > 0
    ) {
      sub.textContent = `유효성분 ${item.active_ingredients.length}개 저장됨`;
    } else {
      sub.textContent =
        item.source_type === "manual" ? "직접 입력 저장" : "유효성분 정보 없음";
    }
    nameWrap.appendChild(sub);
    selectWrap.appendChild(nameWrap);

    const deleteBtn = document.createElement("button");
    deleteBtn.textContent = "삭제";
    deleteBtn.style.cssText =
      "border:none;background:#f3f4f6;border-radius:8px;padding:6px 10px;cursor:pointer;";
    deleteBtn.onclick = () => removeMed(item.name);

    top.appendChild(selectWrap);
    top.appendChild(deleteBtn);
    li.appendChild(top);

    if (
      Array.isArray(item.active_ingredients) &&
      item.active_ingredients.length > 0
    ) {
      const details = document.createElement("details");
      details.className = "compact-details";
      const summary = document.createElement("summary");
      summary.textContent = "유효성분 보기";
      const body = document.createElement("div");
      body.className = "compact-details-body";
      body.textContent = item.active_ingredients.join(", ");
      details.appendChild(summary);
      details.appendChild(body);
      li.appendChild(details);
    }

    ul.appendChild(li);
  });

  updateSelectedMedSummary();
}

function openHiddenPicker(inputId) {
  const input = document.getElementById(inputId);
  if (input) input.click();
}

function getPreviewContainerIdByKey(key) {
  return key === "current"
    ? "preview-current-container"
    : "preview-new-container";
}

function getAutoStatusIdByKey(key) {
  return key === "current" ? "current-auto-status" : "new-auto-status";
}

function getSelectedImageFiles(key) {
  return Array.isArray(selectedImageState[key]) ? selectedImageState[key] : [];
}

function addFilesToState(key, files) {
  if (!Array.isArray(selectedImageState[key])) {
    selectedImageState[key] = [];
  }
  const current = selectedImageState[key];
  Array.from(files || []).forEach((file) => {
    const exists = current.some(
      (saved) =>
        saved.name === file.name &&
        saved.size === file.size &&
        saved.lastModified === file.lastModified &&
        saved.type === file.type,
    );
    if (!exists) current.push(file);
  });
}

function renderPreviewState(key, containerId) {
  const container = document.getElementById(
    containerId || getPreviewContainerIdByKey(key),
  );
  if (!container) return;
  container.innerHTML = "";
  const files = getSelectedImageFiles(key);
  files.forEach((file) => {
    const reader = new FileReader();
    reader.onload = function (e) {
      const img = document.createElement("img");
      img.src = e.target.result;
      img.className = "preview-img";
      container.appendChild(img);
    };
    reader.readAsDataURL(file);
  });
}

function clearImageSelection(key) {
  selectedImageState[key] = [];
  if (autoActionTimers[key]) {
    clearTimeout(autoActionTimers[key]);
    autoActionTimers[key] = null;
  }
  renderPreviewState(key, getPreviewContainerIdByKey(key));
  const status = document.getElementById(getAutoStatusIdByKey(key));
  if (status) {
    status.style.display = "none";
    status.innerText = "";
  }
}

function scheduleAutoAction(key) {
  const status = document.getElementById(getAutoStatusIdByKey(key));
  if (status) {
    status.style.display = "block";
    status.innerText =
      key === "current"
        ? "사진 선택 완료 · 아래 버튼으로 약통 등록을 진행하세요."
        : "사진 선택 완료 · 아래 분석 버튼을 눌러 주세요.";
  }
  if (autoActionTimers[key]) {
    clearTimeout(autoActionTimers[key]);
    autoActionTimers[key] = null;
  }
}

function handleMultiplePreviews(event, containerId, stateKey) {
  const key =
    stateKey ||
    (containerId === "preview-current-container" ? "current" : "new");
  const files = event.target.files;
  if (files && files.length) {
    addFilesToState(key, files);
    renderPreviewState(key, containerId);
    scheduleAutoAction(key);
  }
  event.target.value = "";
}

function renderOcrPanel(text) {
  const previewEl = document.getElementById("panel-ocr-preview");
  const fullEl = document.getElementById("panel-ocr-full");
  const toggleBtn = document.getElementById("panel-ocr-toggle");
  const value = String(text || "").trim();

  if (!value) {
    previewEl.innerText = "-";
    fullEl.style.display = "none";
    fullEl.innerText = "";
    toggleBtn.style.display = "none";
    return;
  }

  previewEl.innerText = buildPreviewText(value, 180);
  fullEl.innerText = value;
  fullEl.style.display = "none";
  toggleBtn.style.display = value.length > 180 ? "inline-block" : "none";
  toggleBtn.innerText = "원문 펼치기";
  toggleBtn.onclick = () => {
    const opened = fullEl.style.display !== "none";
    fullEl.style.display = opened ? "none" : "block";
    toggleBtn.innerText = opened ? "원문 펼치기" : "원문 접기";
  };
}

function renderCurrentMedPanel(result) {
  const summaryEl = document.getElementById("panel-current-meds-summary");
  const detailsEl = document.getElementById("panel-current-meds-details");
  const bodyEl = document.getElementById("panel-current-meds");
  const sources = normalizeList(result.current_ingredient_sources);
  const usedNames = normalizeList(
    result.selected_current_labels && result.selected_current_labels.length
      ? result.selected_current_labels
      : result.current_meds_used,
  );
  const currentIngredients = normalizeList(result.current_active_ingredients);

  if (!usedNames.length && !currentIngredients.length) {
    summaryEl.innerText = "-";
    detailsEl.style.display = "none";
    bodyEl.innerHTML = "";
    return;
  }

  summaryEl.innerText = `현재 약 ${usedNames.length}개 · 비교한 유효성분 ${currentIngredients.length}개`;
  detailsEl.style.display = "block";

  const sourceLabels = new Set();
  const html = [];
  sources.forEach((src) => {
    const label = src.label || "현재 복용 약";
    sourceLabels.add(label);
    html.push(`
            <div class="source-item">
              <div class="source-title">${escapeHtml(label)}</div>
              <div class="source-text">${escapeHtml((src.ingredients || []).join(", ") || "유효성분 정보 없음")}</div>
            </div>
          `);
  });

  usedNames.forEach((name) => {
    if (sourceLabels.has(name)) return;
    html.push(`
            <div class="source-item">
              <div class="source-title">${escapeHtml(name)}</div>
              <div class="source-text">유효성분 정보가 아직 저장되지 않았습니다.</div>
            </div>
          `);
  });

  bodyEl.innerHTML = html.join("");
}

function fillIngredientEvidence(result) {
  const selectedLabelEl = document.getElementById("evidence-current-labels");
  const currentEl = document.getElementById("evidence-current-ingredients");
  const newEl = document.getElementById("evidence-new-ingredients");
  const overlapEl = document.getElementById("evidence-overlap-ingredients");
  const ruleEl = document.getElementById("evidence-rule-matches");
  const basisEl = document.getElementById("evidence-compare-basis");

  const selectedLabels = normalizeList(result.selected_current_labels);
  const currentIngredients = normalizeList(result.current_active_ingredients);
  const newIngredients = normalizeList(
    result.active_ingredients && result.active_ingredients.length
      ? result.active_ingredients
      : result.new_active_ingredients,
  );
  const overlapIngredients = normalizeList(result.overlap_active_ingredients);
  const ruleMatches = normalizeList(result.ingredient_rule_matches);
  const compareBasis = normalizeList(result.compare_basis);

  selectedLabelEl.innerText = selectedLabels.length
    ? selectedLabels.join(", ")
    : "선택한 현재 복용 약 없음";
  currentEl.innerText = currentIngredients.length
    ? currentIngredients.join(", ")
    : "현재 복용 약에서 유효성분을 아직 확보하지 못했습니다.";
  newEl.innerText = newIngredients.length
    ? newIngredients.join(", ")
    : "새 약 사진에서 유효성분을 아직 확보하지 못했습니다.";
  overlapEl.innerText = overlapIngredients.length
    ? overlapIngredients.join(", ")
    : "중복 성분은 아직 감지되지 않았습니다.";
  ruleEl.innerText = ruleMatches.length
    ? ruleMatches
        .map(
          (item) =>
            `${item.current_ingredient} ↔ ${item.new_ingredient} (${item.risk})`,
        )
        .join("\n")
    : "추가 성분 주의 조합은 아직 감지되지 않았습니다.";
  basisEl.innerText = compareBasis.length ? compareBasis.join(" · ") : "-";

  const summaryNewName = document.getElementById("summary-new-name");
  const summaryCurrentNames = document.getElementById("summary-current-names");
  const summaryNewIngredients = document.getElementById(
    "summary-new-ingredients",
  );
  const summaryOverlap = document.getElementById("summary-overlap");

  if (summaryNewName) {
    summaryNewName.innerText =
      result.public_name || "제품명 확인 필요 · 성분 기준 분석";
  }
  if (summaryCurrentNames) {
    summaryCurrentNames.innerText = selectedLabels.length
      ? selectedLabels.join(", ")
      : "선택한 현재 복용 약 없음";
  }
  if (summaryNewIngredients) {
    summaryNewIngredients.innerText = newIngredients.length
      ? newIngredients.join(", ")
      : "유효성분을 아직 확보하지 못했습니다.";
  }
  if (summaryOverlap) {
    summaryOverlap.innerText = overlapIngredients.length
      ? overlapIngredients.join(", ")
      : "중복 성분 없음";
  }
}

function decorateSavedImportCard(card, badge, name) {
  badge.className = "pill-badge ok";
  badge.textContent = `저장 완료: ${name}`;
  card.querySelectorAll("button").forEach((btn) => (btn.disabled = true));
  card.querySelectorAll("input").forEach((input) => (input.disabled = true));
}

function renderImportReview(items) {
  const wrap = document.getElementById("current-import-review");
  wrap.innerHTML = "";
  if (!Array.isArray(items) || items.length === 0) return;

  items.forEach((item) => {
    const card = document.createElement("div");
    card.className = "import-card";

    const title = document.createElement("div");
    title.className = "import-card-title";
    title.textContent = `${item.index || "-"}. ${item.file_name || "이미지"}`;
    card.appendChild(title);

    const badge = document.createElement("div");
    badge.className = `pill-badge ${item.status === "saved" ? "ok" : "warn"}`;
    badge.textContent =
      item.status === "saved"
        ? `저장 완료: ${item.saved_name}`
        : "직접 저장 필요";
    card.appendChild(badge);

    if (
      Array.isArray(item.active_ingredients) &&
      item.active_ingredients.length > 0
    ) {
      const ing = document.createElement("div");
      ing.className = "import-card-meta";
      ing.textContent = `유효성분: ${item.active_ingredients.join(", ")}`;
      card.appendChild(ing);
    }

    if (item.status !== "saved") {
      const manualInput = document.createElement("input");
      manualInput.type = "text";
      manualInput.className = "inline-input";
      manualInput.placeholder = "직접 약 이름 입력 후 저장";
      manualInput.value = normalizeList(item.active_ingredients)[0] || "";
      card.appendChild(manualInput);

      const actionRow = document.createElement("div");
      actionRow.className = "action-row";

      const saveNamedBtn = document.createElement("button");
      saveNamedBtn.type = "button";
      saveNamedBtn.className = "btn-mini";
      saveNamedBtn.textContent = "직접 이름으로 저장";
      saveNamedBtn.onclick = async () => {
        const value = manualInput.value.trim();
        if (!value) {
          alert("저장할 이름을 입력해 주세요.");
          return;
        }
        try {
          saveNamedBtn.disabled = true;
          const saved = await saveMedDetail({
            medicine_name: value,
            active_ingredients: item.active_ingredients || [],
            source_type: "image",
            ocr_text: item.ocr_text || "",
          });
          decorateSavedImportCard(card, badge, saved.saved_name || value);
        } catch (error) {
          alert("직접 저장 실패!");
          saveNamedBtn.disabled = false;
        }
      };
      actionRow.appendChild(saveNamedBtn);

      const ingredientOnlyBtn = document.createElement("button");
      ingredientOnlyBtn.type = "button";
      ingredientOnlyBtn.className = "btn-mini";
      ingredientOnlyBtn.textContent = "이름 없이 성분만 저장";
      ingredientOnlyBtn.disabled = !normalizeList(item.active_ingredients)
        .length;
      ingredientOnlyBtn.onclick = async () => {
        try {
          ingredientOnlyBtn.disabled = true;
          const saved = await saveMedDetail({
            medicine_name: "",
            active_ingredients: item.active_ingredients || [],
            source_type: "image",
            ocr_text: item.ocr_text || "",
          });
          decorateSavedImportCard(
            card,
            badge,
            saved.saved_name || "성분 기반 등록",
          );
        } catch (error) {
          alert("성분만 저장 실패!");
          ingredientOnlyBtn.disabled = false;
        }
      };
      actionRow.appendChild(ingredientOnlyBtn);

      card.appendChild(actionRow);
    }

    wrap.appendChild(card);
  });
}

async function registerCurrentMedsFromImages(options = {}) {
  const nick = localStorage.getItem("safepill_nickname");
  const files = getSelectedImageFiles("current");
  const statusBox = document.getElementById("current-import-status");
  const autoStatusBox = document.getElementById("current-auto-status");
  const btn = document.getElementById("current-import-btn");
  const autoTriggered = !!options.autoTriggered;

  if (!nick) {
    if (!autoTriggered) alert("먼저 로그인해 주세요.");
    return;
  }
  if (!files || files.length === 0) {
    if (!autoTriggered) alert("현재 복용 약 사진을 먼저 선택해 주세요!");
    return;
  }

  const formData = new FormData();
  formData.append("nickname", nick);
  Array.from(files).forEach((file) => formData.append("images", file));

  btn.disabled = true;
  const oldText = btn.innerText;
  btn.innerText = autoTriggered ? "자동 등록 중..." : "약통 등록 중...";
  if (autoStatusBox) {
    autoStatusBox.style.display = "block";
    autoStatusBox.innerText = autoTriggered
      ? "촬영/선택 직후 자동 등록을 진행 중입니다..."
      : "";
  }

  try {
    const response = await fetch(apiUrl("/add_med_images"), {
      method: "POST",
      body: formData,
    });
    const result = await response.json();

    if (!response.ok || result.status === "fail") {
      throw new Error(result.message || "server error");
    }

    statusBox.style.display = "block";
    statusBox.innerText = "";
    renderImportReview(result.items || []);
    await loadMeds(nick);
    if (autoStatusBox) {
      autoStatusBox.style.display = "block";
      autoStatusBox.innerText =
        "사진 인식이 완료되어 현재 복용 약 목록을 업데이트했습니다.";
    }
  } catch (error) {
    statusBox.style.display = "block";
    statusBox.innerText = "약통 이미지 등록 중 오류가 발생했습니다.";
    if (autoStatusBox && autoTriggered) {
      autoStatusBox.style.display = "block";
      autoStatusBox.innerText =
        "자동 등록에 실패해 아래 버튼으로 다시 시도할 수 있습니다.";
    }
  } finally {
    btn.disabled = false;
    btn.innerText = oldText;
  }
}

async function confirmStep(options = {}) {
  const newPillFiles = getSelectedImageFiles("new");
  const currentPillFiles = getSelectedImageFiles("current");
  const autoTriggered = !!options.autoTriggered;
  const autoStatusBox = document.getElementById("new-auto-status");
  if (newPillFiles.length === 0) {
    if (!autoTriggered) alert("새로 먹을 약 사진을 최소 1장 첨부해주세요!");
    return;
  }

  const formData = new FormData();
  const nick = localStorage.getItem("safepill_nickname") || "";
  const selectedItems = getSelectedCurrentDetails();
  if (selectedItems.length === 0) {
    const library = document.getElementById("current-med-library");
    const librarySummary = document.getElementById(
      "current-med-library-summary",
    );
    if (library) library.open = true;
    if (librarySummary)
      librarySummary.innerText = `현재 복용 약 목록 (${selectedCurrentMedIds.size}/${currentMedDetails.length})`;
    const message =
      currentMedDetails.length > 0
        ? "현재 복용 약 목록에서 비교할 약을 먼저 선택해 주세요."
        : "현재 복용 약이 없습니다. 먼저 현재 복용 약을 등록해 주세요.";
    if (!autoTriggered) alert(message);
    const summaryEl = document.getElementById("selected-med-summary");
    if (summaryEl) summaryEl.innerText = message;
    const sectionTitle = document.querySelector(".section-subtitle");
    if (sectionTitle)
      sectionTitle.scrollIntoView({
        behavior: "smooth",
        block: "center",
      });
    return;
  }
  formData.append("nickname", nick);
  Array.from(newPillFiles).forEach((file) => formData.append("image", file));
  selectedItems.forEach((item) => {
    formData.append("selected_current_ids", String(item.id));
    formData.append("current_drugs", item.name);
  });
  Array.from(currentPillFiles || []).forEach((file) =>
    formData.append("current_pill_images", file),
  );

  const btn = document.getElementById("analyze-btn");
  const originalBtnText = btn.innerText;
  btn.innerText = autoTriggered ? "자동 분석 중... ⏳" : "분석 중... ⏳";
  btn.disabled = true;
  if (autoStatusBox) {
    autoStatusBox.style.display = "block";
    autoStatusBox.innerText = autoTriggered
      ? "촬영/선택 직후 자동 분석을 진행 중입니다..."
      : "";
  }

  try {
    const response = await fetch(apiUrl("/analyze"), {
      method: "POST",
      body: formData,
    });

    if (!response.ok) throw new Error("서버 에러");
    const result = await response.json();

    document.getElementById("result-container").style.display = "block";
    lastAnalyze = result;
    updateResultUI(result);
    if (autoStatusBox) {
      autoStatusBox.style.display = "block";
      autoStatusBox.innerText =
        "사진 인식이 완료되어 결과를 자동으로 표시했습니다.";
    }
  } catch (error) {
    if (autoTriggered) {
      if (autoStatusBox) {
        autoStatusBox.style.display = "block";
        autoStatusBox.innerText =
          "자동 분석에 실패해 아래 버튼으로 다시 시도할 수 있습니다.";
      }
    } else {
      alert("서버 연결 실패!");
    }
  } finally {
    btn.innerText = originalBtnText;
    btn.disabled = false;
  }
}

function updateResultUI(result) {
  const warningBox = document.querySelector(".warning-box");
  const statusText = document.querySelector(".status-text");
  const trafficLight = document.querySelector(".traffic-light");
  const reasonText = document.getElementById("reason-text");
  const descText = document.getElementById("personalized-desc");
  const summaryLineList = document.getElementById("summary-line-list");
  const actionList = document.getElementById("action-list");
  const ingredientExplainBox = document.getElementById(
    "ingredient-explain-box",
  );
  const ingredientExplainList = document.getElementById(
    "ingredient-explain-list",
  );

  const panelCorrected = document.getElementById("panel-corrected");
  const panelRule = document.getElementById("panel-rule");
  const panelConf = document.getElementById("panel-confidence");
  const panelCand = document.getElementById("panel-candidates");
  const panelIng = document.getElementById("panel-ingredients");
  const panelCurrentIngredients = document.getElementById(
    "panel-current-ingredients",
  );
  const panelOverlapIngredients = document.getElementById(
    "panel-overlap-ingredients",
  );
  const panelCurrentNote = document.getElementById("panel-current-note");
  const panelIngredientRules = document.getElementById(
    "panel-ingredient-rules",
  );
  const panelCompareBasis = document.getElementById("panel-compare-basis");

  panelCorrected.innerText =
    result.public_name || "제품명 확인 필요 · 성분 기준 분석";
  panelRule.innerText = result.rule_corrected || "-";
  renderOcrPanel(result.ocr_text || "");

  panelConf.innerText = `신뢰도: ${formatConfidence(result.match_confidence)}`;
  document.getElementById("panel-note").innerText = result.match_note || "";

  const newIngredients = normalizeList(
    result.active_ingredients && result.active_ingredients.length
      ? result.active_ingredients
      : result.new_active_ingredients,
  );
  const currentIngredients = normalizeList(result.current_active_ingredients);
  const overlapIngredients = normalizeList(result.overlap_active_ingredients);
  const ruleMatches = normalizeList(result.ingredient_rule_matches);
  const compareBasis = normalizeList(result.compare_basis);

  panelIng.innerText = newIngredients.length ? newIngredients.join(", ") : "-";
  panelCurrentIngredients.innerText = currentIngredients.length
    ? currentIngredients.join(", ")
    : "-";
  panelOverlapIngredients.innerText = overlapIngredients.length
    ? overlapIngredients.join(", ")
    : "중복 성분 없음";
  panelIngredientRules.innerText = ruleMatches.length
    ? ruleMatches
        .map(
          (item) =>
            `${item.current_ingredient} ↔ ${item.new_ingredient} (${item.risk})`,
        )
        .join("\n")
    : "추가 주의 조합 없음";
  panelCompareBasis.innerText = compareBasis.length
    ? compareBasis.join(" · ")
    : "-";
  renderCurrentMedPanel(result);
  fillIngredientEvidence(result);

  if ((result.current_meds_unresolved_count || 0) > 0) {
    panelCurrentNote.style.display = "block";
    panelCurrentNote.innerText = `현재 약 사진 ${result.current_meds_unresolved_count}장은 자동 확정되지 않아 비교에서 제외됐습니다.`;
  } else {
    panelCurrentNote.style.display = "none";
    panelCurrentNote.innerText = "";
  }

  const btnWrap = document.getElementById("panel-candidate-buttons");
  const hint = document.getElementById("panel-candidate-hint");
  btnWrap.innerHTML = "";

  const cands = normalizeList(result.match_candidates).slice(0, 8);
  panelCand.innerText = cands.length ? cands.join(", ") : "-";

  const needs = !!result.needs_confirm;
  if (needs && cands.length) {
    btnWrap.style.display = "flex";
    hint.style.display = "block";
    cands.forEach((name) => {
      const b = document.createElement("button");
      b.className = "chip";
      b.type = "button";
      b.innerText = name;
      b.onclick = () => applyCandidate(name);
      btnWrap.appendChild(b);
    });
  } else {
    btnWrap.style.display = "none";
    hint.style.display = "none";
  }

  if (result.risk === "위험") {
    warningBox.style.borderColor = "#e74c3c";
    warningBox.style.backgroundColor = "#fff5f5";
    statusText.style.color = "#e74c3c";
    statusText.innerText = `병용 위험!`;
    trafficLight.innerText = "🔴";
  } else if (result.risk === "주의") {
    warningBox.style.borderColor = "#f1c40f";
    warningBox.style.backgroundColor = "#fffdf0";
    statusText.style.color = "#d4ac0d";
    statusText.innerText = `주의 필요!`;
    trafficLight.innerText = "🟡";
  } else {
    warningBox.style.borderColor = "#2ecc71";
    warningBox.style.backgroundColor = "#f0fdf4";
    statusText.style.color = "#2ecc71";
    statusText.innerText = `특이사항 없음`;
    trafficLight.innerText = "🔵";
  }

  reasonText.innerText = result.reason || "";
  descText.innerText = result.friendly_summary || result.explanation || "";

  const explanationLines = normalizeList(result.explanation_lines);
  summaryLineList.innerHTML = "";
  explanationLines.forEach((line) => {
    const li = document.createElement("li");
    li.innerText = String(line || "").replace(/^[\-•]\s*/, "");
    summaryLineList.appendChild(li);
  });

  const overlapForAction = overlapIngredients;
  const fallbackActions = overlapForAction.length
    ? [
        `겹치는 성분(${overlapForAction.join(", ")})이 있으면 같은 날 중복 복용을 피하기`,
        "같은 증상약이라도 성분이 겹치는지 한 번 더 확인하기",
        "하루 총 복용량과 복용 간격을 임의로 늘리지 않기",
        "며칠 이상 계속 먹거나 기존 질환·항응고제 복용 중이면 약사와 상담하기",
      ]
    : result.risk === "위험"
      ? [
          "실제 복약 전 반드시 약사나 의사에게 먼저 확인하기",
          "확인 전까지는 새 약을 임의로 함께 복용하지 않기",
        ]
      : result.risk === "주의"
        ? [
            "증상이 비슷해도 성분이 겹치는지 한 번 더 확인하기",
            "복용량과 복용 간격을 임의로 늘리지 않기",
          ]
        : ["특이사항이 없어 보여도 실제 복약 전 성분표를 한 번 더 확인하기"];

  const actionItems = normalizeList(
    result.action_items && result.action_items.length
      ? result.action_items
      : fallbackActions,
  ).slice(0, 1);
  actionList.innerHTML = "";
  actionItems.forEach((line) => {
    const li = document.createElement("li");
    li.innerText = String(line || "").replace(/^[\-•]\s*/, "");
    actionList.appendChild(li);
  });

  const ingredientExplanations = Array.isArray(result.ingredient_explanations)
    ? result.ingredient_explanations
    : [];
  ingredientExplainList.innerHTML = "";
  ingredientExplainBox.style.display = "block";
  if (ingredientExplanations.length > 0) {
    ingredientExplanations.forEach((item) => {
      const wrap = document.createElement("div");
      wrap.className = "ingredient-proof-item";

      const label = document.createElement("div");
      label.className = "ingredient-proof-label";
      const roleText = item && item.role ? ` · ${item.role}` : "";
      label.innerText = `${item.ingredient || "유효성분"}${roleText}`;
      wrap.appendChild(label);

      const body = document.createElement("div");
      body.className = "ingredient-proof-text";
      const parts = [];
      if (item && item.summary) parts.push(item.summary);
      if (item && item.caution) parts.push(`주의: ${item.caution}`);
      body.innerText = parts.join("\n");
      wrap.appendChild(body);

      ingredientExplainList.appendChild(wrap);
    });
  } else {
    const wrap = document.createElement("div");
    wrap.className = "ingredient-proof-item";

    const label = document.createElement("div");
    label.className = "ingredient-proof-label";
    label.innerText = "확정된 유효성분 없음";
    wrap.appendChild(label);

    const body = document.createElement("div");
    body.className = "ingredient-proof-text";
    body.innerText =
      "이번 사진에서는 유효성분을 충분히 확인하지 못해 쉬운 해석을 만들지 못했습니다. 성분표가 보이도록 다시 촬영하거나 제품명을 직접 확인해 주세요.";
    wrap.appendChild(body);

    ingredientExplainList.appendChild(wrap);
  }
}

async function applyCandidate(name) {
  const btnWrap = document.getElementById("panel-candidate-buttons");
  Array.from(btnWrap.querySelectorAll("button")).forEach(
    (b) => (b.disabled = true),
  );

  try {
    const selectedItems = getSelectedCurrentDetails();
    const currentDrugsForRetry =
      Array.isArray(lastAnalyze?.current_meds_used) &&
      lastAnalyze.current_meds_used.length > 0
        ? lastAnalyze.current_meds_used
        : selectedItems.map((item) => item.name);
    const currentIngredientsForRetry = normalizeList(
      lastAnalyze?.current_active_ingredients,
    );
    const newIngredientsForRetry = normalizeList(
      lastAnalyze?.active_ingredients && lastAnalyze.active_ingredients.length
        ? lastAnalyze.active_ingredients
        : lastAnalyze?.new_active_ingredients,
    );
    const selectedLabels = normalizeList(
      lastAnalyze?.selected_current_labels &&
        lastAnalyze.selected_current_labels.length
        ? lastAnalyze.selected_current_labels
        : selectedItems.map((item) => item.name),
    );

    const res = await fetch(apiUrl("/analyze_select"), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        selected_name: name,
        current_drugs: currentDrugsForRetry,
        current_active_ingredients: currentIngredientsForRetry,
        new_active_ingredients: newIngredientsForRetry,
        selected_current_labels: selectedLabels,
      }),
    });
    const data = await res.json();
    if (!res.ok || data.status === "fail") {
      alert("후보 적용 실패: " + (data.message || "server error"));
      return;
    }

    const merged = {
      ...(lastAnalyze || {}),
      corrected_name: data.corrected_name,
      public_name: data.public_name || data.corrected_name || name,
      risk: data.risk,
      reason: data.reason,
      explanation: data.explanation,
      match_note: data.match_note,
      needs_confirm: false,
      current_meds_used: data.current_meds_used || currentDrugsForRetry,
      current_active_ingredients:
        data.current_active_ingredients || currentIngredientsForRetry,
      new_active_ingredients:
        data.new_active_ingredients || newIngredientsForRetry,
      overlap_active_ingredients: data.overlap_active_ingredients || [],
      ingredient_rule_matches: data.ingredient_rule_matches || [],
      compare_basis: data.compare_basis || [],
      selected_current_labels: data.selected_current_labels || selectedLabels,
    };
    lastAnalyze = merged;
    updateResultUI(merged);
  } catch (e) {
    alert("서버 연결 실패!");
  } finally {
    Array.from(btnWrap.querySelectorAll("button")).forEach(
      (b) => (b.disabled = false),
    );
  }
}
