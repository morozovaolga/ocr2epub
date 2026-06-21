(() => {
  "use strict";

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const state = {
    queue: { meta: {}, items: [] },
    decisions: {},
    filter: "review",
    currentIndex: 0,
    filteredItems: [],
    suppressDirty: false,
    ollama: { enabled: false },
    ollamaSuggestion: null,
    spellErrors: [],
    spellBusy: false,
    spellReqId: 0,
    learnedPatterns: [],
  };

  const els = {
    stats: $("#stats"),
    filters: $("#filters"),
    itemList: $("#item-list"),
    navCounter: $("#nav-counter"),
    emptyState: $("#empty-state"),
    reviewCard: $("#review-card"),
    statusBadge: $("#status-badge"),
    paraLabel: $("#para-label"),
    simLabel: $("#sim-label"),
    pageLabel: $("#page-label"),
    pageNav: $("#page-nav"),
    pdfImage: $("#pdf-image"),
    pdfCaption: $("#pdf-caption"),
    oursEdit: $("#ours-edit"),
    oursHighlight: $("#ours-highlight"),
    sliceHint: $("#slice-hint"),
    spellHint: $("#spell-hint"),
    statusbar: $("#statusbar"),
    btnOllama: $("#btn-ollama"),
    ollamaPanel: $("#ollama-panel"),
    ollamaSuggestion: $("#ollama-suggestion"),
    ollamaEdits: $("#ollama-edits"),
    ollamaMeta: $("#ollama-meta"),
  };

  async function api(path, options = {}) {
    let res;
    try {
      res = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        ...options,
      });
    } catch (err) {
      const msg = String(err?.message || err);
      if (/failed to fetch|networkerror|load failed/i.test(msg)) {
        throw new Error(
          "Сервер не ответил (сеть или таймаут). Ollama может грузить модель 1–3 мин — подождите и повторите. " +
            "Если не помогает: меньшая модель (qwen2.5:3b) или ollama ps."
        );
      }
      throw err;
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText);
    return data;
  }

  function setStatus(msg) {
    els.statusbar.textContent = msg;
  }

  function buildReviewUnits(items) {
    return (items || []).map((item) => {
      const page = item.page ?? item.pages?.[0] ?? 1;
      return {
        item,
        slice: { page, our_text: item.our_text || "" },
        sliceIndex: item.slice_index ?? 0,
        unitKey: item.slice_key || `${item.paragraph_index}:${page}`,
        status: item.status,
        similarity: item.similarity,
      };
    });
  }

  function hasDecision(unit) {
    return Boolean(state.decisions[unit.unitKey]);
  }

  function matchesFilter(unit) {
    const f = state.filter;
    if (f === "all") return true;
    if (f === "pending") return !hasDecision(unit);
    return unit.status === f;
  }

  function rebuildFiltered() {
    const units = buildReviewUnits(state.queue.items);
    state.filteredItems = units.filter(matchesFilter);
    if (state.currentIndex >= state.filteredItems.length) {
      state.currentIndex = Math.max(0, state.filteredItems.length - 1);
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

    function renderStats() {
    const m = state.queue.meta || {};
    const decided = Object.keys(state.decisions).length;
    const isV3 = m.queue_layout === "one_item_per_block_v3";
    const slicesTotal = isV3
      ? (m.blocks_total ?? buildReviewUnits(state.queue.items).length)
      : (m.pages_total ?? buildReviewUnits(state.queue.items).length);
    const sliceReview = m.status_review_slices ?? m.status_review ?? "—";
    const build = m.queue_build ? ` · ${m.queue_build}` : "";
    const empty = m.empty_our_slices ? ` · пустых: ${m.empty_our_slices}` : "";
    const unitLabel = isV3 ? "Блоков" : "Абзацев";
    const secondLabel = isV3 ? "" : `<span class="stat-item">Листов PDF: <strong>${slicesTotal}</strong></span>`;
    els.stats.innerHTML = `
      <span class="stat-item">${unitLabel}: <strong>${m.paragraphs_total ?? slicesTotal ?? "—"}</strong></span>
      ${secondLabel}
      <span class="stat-item stat-review">Review: <strong>${sliceReview}</strong>${build}${empty}</span>
      <span class="stat-item">Решено: <strong>${decided}</strong></span>
    `;
  }

  function previewText(text, max = 56) {
    const t = (text || "").replace(/\s+/g, " ").trim();
    return t.length > max ? t.slice(0, max) + "…" : t;
  }

  function getSliceText(unit) {
    const dec = state.decisions[unit.unitKey];
    if (dec?.edited_text != null) return dec.edited_text;

    const pdec = state.decisions[String(unit.item.paragraph_index)];
    if (pdec?.slice_texts?.[unit.sliceIndex] != null) {
      return pdec.slice_texts[unit.sliceIndex];
    }
    if (pdec?.edited_text && unit.item.page_slices?.length) {
      const sl = unit.item.page_slices[unit.sliceIndex];
      const full = pdec.edited_text;
      if (full.length === (unit.item.our_text || "").length && sl) {
        return full.slice(sl.char_start ?? 0, sl.char_end ?? full.length);
      }
    }
    return unit.slice.our_text ?? unit.item.our_text ?? "";
  }

  function currentUnit() {
    return state.filteredItems[state.currentIndex];
  }

  function expectedText() {
    return getSliceText(currentUnit() || {});
  }

  function isDirty() {
    if (state.suppressDirty) return false;
    return els.oursEdit.value !== expectedText();
  }

  function decisionEntry(unit, action, editedText) {
    return {
      action,
      edited_text: editedText,
      paragraph_index: unit.item.paragraph_index,
      page: unit.slice.page,
      at: new Date().toISOString(),
    };
  }

  async function flushDraft() {
    if (!isDirty()) return false;
    const unit = currentUnit();
    if (!unit) return false;
    const entry = decisionEntry(unit, "edit", els.oursEdit.value);
    const payload = { decisions: { [unit.unitKey]: entry } };
    await api("/api/decisions", { method: "POST", body: JSON.stringify(payload) });
    state.decisions[unit.unitKey] = entry;
    return true;
  }

  async function navigate(fn) {
    await flushDraft();
    await fn();
    renderAll();
  }

  function findLearnedErrors(text, patterns) {
    if (!text || !patterns?.length) return [];
    const sorted = [...patterns].sort((a, b) => (b.from || "").length - (a.from || "").length);
    const used = [];
    const errors = [];
    for (const pair of sorted) {
      const frm = pair.from || "";
      const to = pair.to || "";
      if (!frm) continue;
      let start = 0;
      while (true) {
        const idx = text.indexOf(frm, start);
        if (idx === -1) break;
        const end = idx + frm.length;
        const overlaps = used.some(([a, b]) => idx < b && end > a);
        if (!overlaps) {
          errors.push({
            pos: idx,
            len: frm.length,
            word: text.slice(idx, end),
            suggestions: to ? [to] : [],
            kind: "learned",
          });
          used.push([idx, end]);
        }
        start = idx + Math.max(frm.length, 1);
      }
    }
    return errors.sort((a, b) => a.pos - b.pos);
  }

  function mergeHighlightErrors(learned, spell) {
    const drop = new Set();
    for (let i = 0; i < spell.length; i += 1) {
      const sp = spell[i];
      const spS = sp.pos;
      const spE = sp.pos + sp.len;
      for (const lr of learned) {
        const lrS = lr.pos;
        const lrE = lr.pos + lr.len;
        if (spS < lrE && spE > lrS) {
          drop.add(i);
          break;
        }
      }
    }
    const out = learned.map((e) => ({ ...e, kind: "learned" }));
    spell.forEach((sp, i) => {
      if (!drop.has(i)) out.push({ ...sp, kind: "spell" });
    });
    return out.sort((a, b) => a.pos - b.pos);
  }

  function buildSpellHighlightedHtml(text, errors) {
    if (!text) return "";
    if (!errors?.length) return escapeHtml(text);
    const sorted = [...errors].sort((a, b) => a.pos - b.pos);
    let html = "";
    let cursor = 0;
    for (const err of sorted) {
      const pos = Number(err.pos) || 0;
      const len = Number(err.len) || 0;
      if (pos < cursor) continue;
      if (pos > cursor) html += escapeHtml(text.slice(cursor, pos));
      const kind = err.kind === "learned" ? "learned" : "spell";
      const cls = kind === "learned" ? "err-learned" : "err-word";
      const label = kind === "learned" ? "из базы" : "орфография";
      const sugg = (err.suggestions || []).join(", ");
      const title = sugg ? `${label}: → ${sugg}` : label;
      html += `<mark class="${cls}" title="${escapeHtml(title)}">${escapeHtml(text.slice(pos, pos + len))}</mark>`;
      cursor = pos + len;
    }
    if (cursor < text.length) html += escapeHtml(text.slice(cursor));
    return html;
  }

  function updateHintCounts(learnedCount, spellCount) {
    if (!els.spellHint) return;
    const parts = [];
    if (learnedCount > 0) parts.push(`${learnedCount} база`);
    if (spellCount > 0) parts.push(`${spellCount} орф.`);
    els.spellHint.textContent = parts.length ? `· ${parts.join(" · ")}` : "· OK";
  }

  function refreshHighlights(text) {
    const learned = findLearnedErrors(text, state.learnedPatterns);
    const merged = mergeHighlightErrors(learned, state.spellErrors);
    updateHighlightBackdrop(text, merged);
    updateHintCounts(learned.length, state.spellErrors.length);
  }

  function syncHighlightScroll() {
    if (!els.oursHighlight || !els.oursEdit) return;
    els.oursHighlight.scrollTop = els.oursEdit.scrollTop;
    els.oursHighlight.scrollLeft = els.oursEdit.scrollLeft;
  }

  function updateHighlightBackdrop(text, errors) {
    if (!els.oursHighlight) return;
    els.oursHighlight.innerHTML = buildSpellHighlightedHtml(text, errors);
    syncHighlightScroll();
  }

  let spellDebounce = null;

  async function requestSpellCheck(text) {
    const reqId = ++state.spellReqId;
    state.spellBusy = true;
    try {
      const res = await api("/api/spellcheck", {
        method: "POST",
        body: JSON.stringify({ text }),
      });
      if (reqId !== state.spellReqId) return;
      if (!res.ok) {
        state.spellErrors = [];
        refreshHighlights(text);
        setStatus(`Орфография: недоступна (${res.error || "ошибка"})`);
        return;
      }
      state.spellErrors = res.spell_errors || [];
      refreshHighlights(text);
    } catch (err) {
      if (reqId !== state.spellReqId) return;
      state.spellErrors = [];
      refreshHighlights(text);
      setStatus(`Орфография: ${err.message}`);
    } finally {
      if (reqId === state.spellReqId) state.spellBusy = false;
    }
  }

  function scheduleSpellCheck(text) {
    clearTimeout(spellDebounce);
    spellDebounce = setTimeout(() => void requestSpellCheck(text), 350);
  }

  function renderList() {
    els.itemList.innerHTML = "";
    state.filteredItems.forEach((unit, idx) => {
      const li = document.createElement("li");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "item-btn" + (idx === state.currentIndex ? " active" : "");
      if (hasDecision(unit)) btn.classList.add("decided");

      const pi = unit.item.paragraph_index;
      const page = unit.slice.page;
      const text = getSliceText(unit);
      const isV3 = state.queue.meta?.queue_layout === "one_item_per_block_v3";
      const isPageFirst = state.queue.meta?.queue_layout === "one_item_per_pdf_page_v2";
      const blockId = unit.item.block_id || unit.unitKey;
      const role = unit.item.role ? ` · ${unit.item.role}` : "";
      const label = isV3
        ? `стр. ${page} · ${blockId}${role}`
        : isPageFirst
          ? `стр. ${page}`
          : `#${pi} · стр. ${page}`;
      btn.innerHTML = `
        <div class="line1">
          <span>${label} <span class="badge ${unit.status}">${unit.status}</span></span>
          <span class="muted">${(unit.similarity ?? 0).toFixed(2)}</span>
        </div>
        <div class="preview">${escapeHtml(previewText(text))}</div>
      `;
      btn.addEventListener("click", () => {
        void navigate(async () => {
          state.currentIndex = idx;
        });
      });
      li.appendChild(btn);
      els.itemList.appendChild(li);
    });
  }

  function hideOllamaPanel() {
    state.ollamaSuggestion = null;
    els.ollamaPanel.classList.add("hidden");
    els.ollamaSuggestion.textContent = "";
    els.ollamaEdits.innerHTML = "";
    els.ollamaEdits.classList.add("hidden");
    els.btnOllama.classList.remove("ollama-loading");
  }

  function formatEditLine(edit, rejected) {
    const arrow = `«${edit.old}» → «${edit.new}»`;
    if (rejected && edit.error) return `${arrow} — ${edit.error}`;
    const reason = edit.reason ? ` (${edit.reason})` : "";
    return arrow + reason;
  }

  function renderOllamaEdits(result) {
    const applied = result.edits_applied || [];
    const rejected = result.edits_rejected || [];
    if (!applied.length && !rejected.length) {
      els.ollamaEdits.innerHTML = "";
      els.ollamaEdits.classList.add("hidden");
      return;
    }
    const parts = [];
    for (const e of applied) {
      parts.push(`<li>${escapeHtml(formatEditLine(e, false))}</li>`);
    }
    for (const e of rejected) {
      parts.push(`<li class="rejected">${escapeHtml(formatEditLine(e, true))}</li>`);
    }
    els.ollamaEdits.innerHTML = parts.join("");
    els.ollamaEdits.classList.remove("hidden");
  }

  function showOllamaSuggestion(result) {
    state.ollamaSuggestion = result;
    els.ollamaPanel.classList.remove("hidden");
    els.ollamaSuggestion.textContent = result.suggested_text || "";
    renderOllamaEdits(result);
    const ch = result.changed ? "изменено" : "без изменений";
    const mode = result.mode === "find_apply" ? " · JSON-правки" : result.mode === "rewrite" ? " · rewrite" : "";
    const rev = result.reverted_to_original ? " · откат к исходному" : "";
    const warn = result.orthography_warning ? ` · ⚠ ${result.orthography_warning}` : "";
    const nEdits = (result.edits_applied || []).length;
    const editsNote = nEdits ? ` · ${nEdits} правок` : "";
    els.ollamaMeta.textContent = `· ${result.model || ""}${mode} · ${ch}${editsNote}${rev}${warn}`;
  }

  function applyOllamaToTextarea() {
    if (!state.ollamaSuggestion?.suggested_text) return;
    els.oursEdit.value = state.ollamaSuggestion.suggested_text;
    const unit = currentUnit();
    scheduleSpellCheck(els.oursEdit.value);
    els.oursEdit.focus();
    setStatus("Предложение Ollama подставлено — проверьте и сохраните");
  }

  async function requestOllama() {
    const unit = currentUnit();
    if (!unit) return;
    if (!state.ollama.enabled) {
      alert("Ollama отключён. Запустите сервер без --no-ollama и убедитесь, что ollama serve работает.");
      return;
    }
    els.btnOllama.classList.add("ollama-loading");
    setStatus("Ollama думает…");
    try {
      const res = await api("/api/ollama/suggest", {
        method: "POST",
        body: JSON.stringify({
          paragraph_index: unit.item.paragraph_index,
          page: unit.slice.page,
          our_text: els.oursEdit.value,
        }),
      });
      showOllamaSuggestion(res);
      setStatus(res.changed ? "Ollama предложил правки" : "Ollama: изменений нет");
    } catch (err) {
      hideOllamaPanel();
      setStatus("Ollama: " + err.message);
      alert(err.message);
    } finally {
      els.btnOllama.classList.remove("ollama-loading");
    }
  }

  async function acceptOllamaAndNext() {
    if (!state.ollamaSuggestion?.suggested_text) return;
    els.oursEdit.value = state.ollamaSuggestion.suggested_text;
    const unit = currentUnit();
    if (!unit) return;
    await saveDecision(unit, "accept_llm", els.oursEdit.value);
    hideOllamaPanel();
    await goNext();
  }

  function renderReview() {
    const unit = currentUnit();
    if (!unit) {
      els.emptyState.classList.remove("hidden");
      els.reviewCard.classList.add("hidden");
      els.emptyState.innerHTML = "<p>Нет фрагментов для выбранного фильтра.</p>";
      els.navCounter.textContent = "0 / 0";
      return;
    }

    els.emptyState.classList.add("hidden");
    els.reviewCard.classList.remove("hidden");
    els.pageNav.classList.add("hidden");

    const pi = unit.item.paragraph_index;
    const page = unit.slice.page;
    const pos = state.currentIndex + 1;
    const total = state.filteredItems.length;
    const sliceText = getSliceText(unit);
    const isV3 = state.queue.meta?.queue_layout === "one_item_per_block_v3";
    const blockId = unit.item.block_id || unit.unitKey;

    els.navCounter.textContent = `${pos} / ${total}`;
    els.statusBadge.textContent = unit.status;
    els.statusBadge.className = "badge " + unit.status;
    els.paraLabel.textContent = isV3 ? blockId : `Абзац #${pi}`;
    els.simLabel.textContent = `sim ${(unit.similarity ?? 0).toFixed(3)}`;
    els.pageLabel.textContent = `стр. ${page}`;

    state.suppressDirty = true;
    els.oursEdit.value = sliceText;
    refreshHighlights(sliceText);
    scheduleSpellCheck(sliceText);
    state.suppressDirty = false;

    els.sliceHint.textContent = isV3
      ? sliceText.trim()
        ? `crop bbox · ${sliceText.length} симв. · слева фрагмент PDF, справа наш текст`
        : `стр. ${page} · ${blockId} · ⚠ текст пуст`
      : sliceText.trim()
        ? `только стр. ${page} · ${sliceText.length} симв. · слева скан, справа наш текст (граница страницы не меняется)`
        : `стр. ${page} · ⚠ наш текст пуст — пересоберите очередь (Пересобрать) или откройте соседнюю страницу того же абзаца #${pi}`;

    els.pdfCaption.textContent = isV3 ? `PDF crop · ${blockId}` : `Страница PDF · ${page}`;
    els.pdfImage.src = `/api/page-image?paragraph_index=${pi}&page=${page}&zoom=2.2&_=${Date.now()}`;
    els.pdfImage.onerror = () => {
      els.pdfImage.removeAttribute("src");
    };

    hideOllamaPanel();

    const dec = state.decisions[unit.unitKey];
    const actionLabel = dec
      ? {
          keep_ours: "сохранено",
          edit: "правка сохранена",
          accept_llm: "принято от Ollama",
          skip: "пропуск",
        }[dec.action] || dec.action
      : "";
    const dirtyMark = isDirty() ? " · есть несохранённые изменения" : "";
    setStatus(
      (actionLabel ? `Решение: ${actionLabel}` : "Правки по странице сохраняются при переходе") + dirtyMark
    );
  }

  function renderAll() {
    renderStats();
    renderList();
    renderReview();
  }

  async function saveDecision(unit, action, editedText = null) {
    const text = editedText ?? (action === "keep_ours" ? getSliceText(unit) : null);
    const entry = decisionEntry(unit, action, text);
    const payload = { decisions: { [unit.unitKey]: entry } };
    await api("/api/decisions", { method: "POST", body: JSON.stringify(payload) });
    state.decisions[unit.unitKey] = entry;
    setStatus("Сохранено в corrector_decisions.json");
    renderAll();
  }

  async function act(action) {
    const unit = currentUnit();
    if (!unit) return;

    let edited = null;
    if (action === "save_edit" || isDirty()) {
      edited = els.oursEdit.value;
      action = "edit";
    }

    await saveDecision(unit, action, edited);
    await goNext();
  }

  async function goNext() {
    await flushDraft();
    if (state.currentIndex < state.filteredItems.length - 1) {
      state.currentIndex += 1;
      renderAll();
    } else {
      setStatus("Конец списка для текущего фильтра");
      renderAll();
    }
  }

  async function goPrev() {
    await flushDraft();
    if (state.currentIndex > 0) {
      state.currentIndex -= 1;
      renderAll();
    }
  }

  async function loadLearnedPatterns() {
    try {
      const res = await api("/api/learned-bad-forms");
      state.learnedPatterns = res.patterns || [];
    } catch {
      state.learnedPatterns = [];
    }
  }

  async function load() {
    const [queue, decisions, config] = await Promise.all([
      api("/api/queue"),
      api("/api/decisions"),
      api("/api/config"),
    ]);
    await loadLearnedPatterns();
    state.queue = queue;
    state.decisions = decisions.decisions || {};
    state.ollama = config.ollama || { enabled: false };
    rebuildFiltered();
    renderAll();
    const o = state.ollama;
    let status = "Очередь загружена";
    if (o.enabled) {
      status += o.ok
        ? ` · Ollama ${o.model} (${o.mode || "find_apply"})${o.model_available ? "" : " (модель не найдена — ollama pull)"}`
        : ` · Ollama недоступен`;
    }
    if (!queue.meta?.queue_layout && !queue.items?.[0]?.page) {
      status += " · нажмите «Пересобрать» для разбивки по страницам";
    }
    if (queue.meta?.page_snapshots_dir) {
      status += ` · снимки: ${queue.meta.page_snapshots_dir}`;
    }
    setStatus(status);
    if (!o.enabled || !o.ok) {
      els.btnOllama.disabled = true;
      els.btnOllama.title = "Ollama недоступен";
    }
  }

  function bindEvents() {
    els.filters.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-filter]");
      if (!btn) return;
      void navigate(async () => {
        $$(".chip").forEach((c) => c.classList.remove("active"));
        btn.classList.add("active");
        state.filter = btn.dataset.filter;
        state.currentIndex = 0;
      });
    });

    $("#btn-prev").addEventListener("click", () => void goPrev());
    $("#btn-next").addEventListener("click", () => void goNext());
    $("#btn-keep").addEventListener("click", () => void act("keep_ours"));
    $("#btn-save-edit").addEventListener("click", () => void act("save_edit"));
    $("#btn-skip").addEventListener("click", () => void act("skip"));
    $("#btn-ollama").addEventListener("click", () => void requestOllama());
    $("#btn-ollama-apply").addEventListener("click", applyOllamaToTextarea);
    $("#btn-ollama-accept").addEventListener("click", () => void acceptOllamaAndNext());
    $("#btn-ollama-dismiss").addEventListener("click", hideOllamaPanel);

    els.oursEdit.addEventListener("input", () => {
      refreshHighlights(els.oursEdit.value);
      scheduleSpellCheck(els.oursEdit.value);
    });
    els.oursEdit.addEventListener("scroll", syncHighlightScroll);

    $("#btn-rebuild").addEventListener("click", async () => {
      if (!confirm("Пересобрать corrector_queue.json? Границы страниц задаются заново.")) return;
      try {
        setStatus("Пересборка…");
        const res = await api("/api/rebuild", { method: "POST", body: "{}" });
        await load();
        const m = res.meta || {};
        const empty = res.empty_our_slices ?? m.empty_our_slices ?? 0;
        const layout = res.queue_layout ?? m.queue_layout ?? "";
        setStatus(
          `Пересобрано: ${res.items_count ?? "?"} листов · review ${m.status_review_slices ?? "?"}`
          + (layout ? ` · ${layout}` : "")
          + (empty ? ` · ⚠ пустых ${empty}` : "")
        );
      } catch (err) {
        setStatus("Ошибка: " + err.message);
      }
    });

    $("#btn-apply").addEventListener("click", async () => {
      if (!confirm(
        "Применить все правки?\n\n" +
        "• final_corrected.txt + final_better.txt\n" +
        "• rules_learned.jsonl\n" +
        "• пересборка corrector_queue.json"
      )) return;
      try {
        await flushDraft();
        setStatus("Применение…");
        const res = await api("/api/apply", { method: "POST", body: "{}" });
        await load();
        const m = res.queue_meta || {};
        setStatus(`Готово: +${res.rules_added ?? 0} правил · review ${m.status_review_slices ?? m.status_review ?? "?"}`);
        alert(
          `Сохранено:\n${res.corrected}\n\n` +
          `Правил (книга): +${res.rules_added ?? 0}\n` +
          `Правил (общая база): +${res.global_rules_added ?? 0}\n` +
          `Очередь: review ${m.status_review_slices ?? m.status_review ?? "?"}`
        );
      } catch (err) {
        setStatus("Ошибка: " + err.message);
        alert(err.message);
      }
    });

    document.addEventListener("keydown", (e) => {
      if (e.target === els.oursEdit) {
        if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          void act("save_edit");
        }
        return;
      }
      const key = e.key.toLowerCase();
      if (key === "j" || key === "arrowright") { e.preventDefault(); void goNext(); }
      if (key === "k" || key === "arrowleft") { e.preventDefault(); void goPrev(); }
      if (key === "1" || key === "o") { e.preventDefault(); void act("keep_ours"); }
      if (key === "2" || key === "l") { e.preventDefault(); void requestOllama(); }
      if (key === "3" || key === "s") { e.preventDefault(); void act("skip"); }
      if (key === "enter") { e.preventDefault(); void act("save_edit"); }
    });
  }

  bindEvents();
  load().catch((err) => {
    els.emptyState.classList.remove("hidden");
    els.emptyState.innerHTML = `<p>Ошибка загрузки: ${escapeHtml(err.message)}</p>`;
    setStatus("Ошибка загрузки");
  });
})();
