(() => {
  const N = Notebook, {$, t, el} = N;
  const state = {shops: [], areas: [], categories: [], phase: 'loading', detail: null, detailPhase: 'loading', comments: [], commentError: false};
  let listVersion = 0, detailVersion = 0, trigger = null, triggerId = null;
  function filters() {
    N.fillSelect($('area'), t("全部地區", "すべてのエリア"), state.areas);
    N.fillSelect($('category'), t("全部分類", "すべてのジャンル"), state.categories);
  }
  function prices(shop) {
    const div = el('span', 'row-prices price');
    if (!shop.lunch_budget_text && !shop.dinner_budget_text) {
      div.append(el('span', 'muted', t("預算未記錄", "予算の記録なし")));
      return div;
    }
    [["午餐", "ランチ", shop.lunch_budget_text], ["晚餐", "ディナー", shop.dinner_budget_text]].forEach(([zh, ja, value]) => {
      const row = el('span'); row.append(el('span', 'muted', t(zh, ja)), el('span', '', value || t("未記錄", "記録なし"))); div.append(row);
    });
    return div;
  }
  function renderList() {
    filters();
    $('grid').replaceChildren();
    $('summary').textContent = state.phase === 'ready' ? t(`共 ${state.shops.length} 間店`, `${state.shops.length} 件のお店`) : '';
    if (state.phase === 'loading') return N.status($('listState'), t("正在翻開店家筆記…", "お店のノートを読み込み中…"));
    if (state.phase === 'error') return N.status($('listState'), t("店家清單讀取失敗，請確認連線。", "リストを読み込めませんでした。接続を確認してください。"), load);
    N.status($('listState'), state.shops.length ? '' : t("這一頁還沒有店家。試試其他關鍵字或清除篩選。", "該当するお店はありません。検索条件を変えてみてください。"));
    if (!state.shops.length) {
      const clear = el('button', '', t("清除搜尋條件", "検索条件をクリア")); clear.type = 'button';
      clear.addEventListener('click', () => { $('keyword').value = ''; $('area').value = ''; $('category').value = ''; load(); }); $('listState').append(clear);
    }
    state.shops.forEach(shop => {
      const button = el('button', 'shop-row'); button.type = 'button'; button.dataset.testid = 'shop-open'; button.dataset.shopId = shop.id;
      button.setAttribute('aria-haspopup', 'dialog');
      const thumb = el('span', 'shop-thumb'); const img = N.image(shop, '', true);
      if (img) thumb.append(img); else { thumb.textContent = t("未附照片", "写真なし"); thumb.setAttribute('aria-hidden', 'true'); }
      const info = el('span', 'shop-info'); info.append(el('span', 'shop-name', shop.name), el('span', 'meta', [shop.category, shop.area].filter(Boolean).join(" · ")));
      if (shop.recommended_by) info.append(el('span', 'meta', `${t("推薦", "推薦")} · ${shop.recommended_by}`));
      const notes = shop.comment_items || []; const note = notes.length ? notes[notes.length - 1].comment : shop.comments;
      if (note) info.append(el('span', 'row-note', note));
      button.append(thumb, info, prices(shop), el('span', 'open-label', t("看筆記", "詳細")));
      button.addEventListener('click', () => open(shop.id, button)); $('grid').append(button);
    });
  }
  async function load() {
    const version = ++listVersion; state.phase = 'loading'; renderList();
    const params = new URLSearchParams(); ['keyword','area','category'].forEach(key => { if ($(key).value.trim()) params.set(key, $(key).value.trim()); });
    try {
      const data = await N.request(`/api/restaurants?${params}`);
      if (version !== listVersion) return;
      state.shops = data.restaurants.sort((a,b) => a.id-b.id); state.areas = data.areas; state.categories = data.categories; state.phase = 'ready';
    } catch { if (version !== listVersion) return; state.phase = 'error'; }
    renderList();
  }
  function fact(list, label, value, wide = false) {
    const div = el('div', wide ? 'wide' : ''); div.append(el('dt', '', label), el('dd', 'preline', value || t("未記錄", "記録なし"))); list.append(div);
  }
  function renderDetail() {
    const content = $('detailContent'); content.replaceChildren();
    const title = el('h2', '', state.detail?.name || t("店家筆記", "お店のノート")); title.id = 'detailTitle'; content.append(title);
    if (state.detailPhase === 'loading') { const p = el('div', 'state'); p.setAttribute('role', 'status'); N.status(p, t("讀取完整筆記…", "ノートを読み込み中…")); content.append(p); return; }
    if (state.detailPhase === 'error') { const p = el('div', 'state'); p.setAttribute('role', 'alert'); N.status(p, t("找不到店家或連線失敗，請重試。", "お店が見つからないか、接続に失敗しました。"), () => open(triggerId, trigger)); content.append(p); return; }
    const shop = state.detail; const img = N.image(shop, 'detail-image', false); if (img) content.prepend(img);
    content.append(el('p', 'detail-meta meta', [shop.category, shop.area].filter(Boolean).join(" · ")), N.tags(shop));
    const facts = el('dl', 'detail-facts');
    if (!shop.lunch_budget_text && !shop.dinner_budget_text) fact(facts, t("預算", "予算"), null, true);
    else { fact(facts, t("午餐", "ランチ"), shop.lunch_budget_text); fact(facts, t("晚餐", "ディナー"), shop.dinner_budget_text); }
    fact(facts, t("營業時間", "営業時間"), shop.business_hours_text, true); fact(facts, t("推薦者", "推薦者"), shop.recommended_by, true); content.append(facts);
    const links = el('div', 'detail-links');
    if (N.safeUrl(shop.tabelog_url)) links.append(N.link("食べログ", N.safeUrl(shop.tabelog_url), true));
    links.append(N.link(t("在 Google Maps 開啟", "Google Maps で開く"), N.mapsUrl(shop), true), N.link(t("地圖與清單", "地図とリスト"), `/map?shop=${shop.id}`)); content.append(links);
    const section = el('section', 'detail-comments'); section.append(el('h3', '', t("朋友的筆記", "友だちのメモ")));
    const notes = el('div', 'comments-list'); notes.setAttribute('aria-live', 'polite');
    if (state.commentError) N.status(notes, t("評論讀取失敗。", "メモを読み込めませんでした。"), loadComments);
    else if (state.comments === null) N.status(notes, t("讀取評論…", "メモを読み込み中…"));
    else N.comments(notes, state.comments);
    section.append(notes); content.append(section);
    if (shop.comments) { const legacy = el('section', 'legacy-note'); legacy.append(el('h3', '', t("舊版筆記", "旧形式のメモ")), el('p', 'preline muted', shop.comments)); content.append(legacy); }
  }
  async function loadComments() {
    const version = detailVersion, id = triggerId; state.comments = null; state.commentError = false; renderDetail();
    try { const data = await N.request(`/api/restaurants/${id}/comments`); if (version !== detailVersion) return; state.comments = data.comments; }
    catch { if (version !== detailVersion) return; state.commentError = true; }
    if ($('shopDetail').open) renderDetail();
  }
  async function open(id, source) {
    triggerId = String(id); trigger = source || null; const version = ++detailVersion;
    state.detail = null; state.detailPhase = 'loading'; state.comments = null; state.commentError = false; renderDetail();
    if (!$('shopDetail').open) $('shopDetail').showModal();
    $('closeDetail').focus();
    const url = new URL(location.href); url.searchParams.set('shop', id); history.replaceState(null, '', url);
    try {
      const shop = await N.request(`/api/restaurants/${encodeURIComponent(id)}`); if (version !== detailVersion) return;
      state.detail = shop; state.detailPhase = 'ready'; renderDetail(); await loadComments();
    } catch { if (version !== detailVersion) return; state.detailPhase = 'error'; renderDetail(); }
  }
  $('closeDetail').addEventListener('click', () => $('shopDetail').close());
  $('shopDetail').addEventListener('close', () => {
    ++detailVersion;
    const url = new URL(location.href); url.searchParams.delete('shop'); history.replaceState(null, '', url);
    const row = Array.from(document.querySelectorAll('[data-testid="shop-open"]')).find(node => node.dataset.shopId === triggerId);
    (trigger?.isConnected ? trigger : row || $('keyword')).focus();
  });
  // Native dialog provides modal isolation and Esc; explicitly cycle Tab for consistent keyboard behavior.
  $('shopDetail').addEventListener('keydown', event => {
    if (event.key !== 'Tab') return;
    const nodes = Array.from($('shopDetail').querySelectorAll('button:not(:disabled),a[href],input,select,textarea,[tabindex="0"]')).filter(node => !node.hidden);
    const first = nodes[0], last = nodes[nodes.length-1];
    if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
    else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
  });
  document.addEventListener('languagechange', () => { renderList(); if ($('shopDetail').open) renderDetail(); });
  $('keyword').addEventListener('input', N.debounce(load)); ['area','category'].forEach(id => $(id).addEventListener('change', load));
  const initial = new URLSearchParams(location.search).get('shop'); load(); if (initial) open(initial);
})();
