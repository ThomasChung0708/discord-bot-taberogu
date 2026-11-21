/* Shared paper, language and safe DOM primitives. No API content is parsed as HTML. */
window.Notebook = (() => {
  const $ = id => document.getElementById(id);
  let language = 'zh';
  try { language = localStorage.getItem('tabelogLanguage') === 'ja' ? 'ja' : 'zh'; } catch {}
  const t = (zh, ja) => language === 'ja' ? ja : zh;
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function applyLanguage() {
    document.documentElement.lang = language === 'ja' ? 'ja' : 'zh-Hant';
    const titles={public:['食事筆記','食事ノート'],map:['食事地圖','食事マップ'],login:['管理登入','管理ログイン'],admin:['筆記工作台','ノート編集室']};
    const page=document.body.dataset.page;
    if(titles[page])document.title=t(...titles[page])+' · TC';
    document.querySelectorAll('nav').forEach(node=>node.setAttribute('aria-label',t('主要導覽','メインナビゲーション')));
    document.querySelectorAll('.lang-switch').forEach(node=>node.setAttribute('aria-label',t('語言','言語')));
    document.querySelectorAll('[data-zh]').forEach(node => { node.textContent = node.dataset[language]; });
    document.querySelectorAll('[data-placeholder-zh]').forEach(node => {
      node.placeholder = language === 'ja' ? node.dataset.placeholderJa : node.dataset.placeholderZh;
    });
    document.querySelectorAll('[data-language]').forEach(node => node.setAttribute('aria-pressed', String(node.dataset.language === language)));
  }
  document.querySelectorAll('[data-language]').forEach(node => node.addEventListener('click', () => {
    language = node.dataset.language === 'ja' ? 'ja' : 'zh';
    try { localStorage.setItem('tabelogLanguage', language); } catch {}
    applyLanguage();
    document.dispatchEvent(new Event('languagechange'));
  }));
  function safeUrl(value) {
    const url = String(value || '').trim();
    if (!/^https?:\/\//i.test(url) || /[\s\x00-\x1f\x7f\\]/.test(url)) return '';
    try { const parsed = new URL(url); return ['http:', 'https:'].includes(parsed.protocol) && parsed.hostname ? url : ''; } catch { return ''; }
  }
  function safeImageUrl(value) {
    const url = String(value || '').trim();
    if (/^\/uploads\/restaurants\/[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(url) && !url.includes('..')) return url;
    return safeUrl(url);
  }
  function mapsUrl(shop) {
    return safeUrl(shop.google_maps_url) || `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent([shop.name, shop.area].filter(Boolean).join(' '))}`;
  }
  function link(label, url, external = false) {
    const href = url.startsWith('/?shop=') || url.startsWith('/map?shop=') ? url : safeUrl(url);
    if (!href) return el('span', '', label);
    const a = el('a', 'button-link', label);
    a.href = href;
    if (external) { a.target = '_blank'; a.rel = 'noopener noreferrer'; }
    return a;
  }
  function image(shop, className, lazy = true) {
    const src = safeImageUrl(shop.image_url);
    if (!src) return null;
    const img = el('img', className);
    img.src = src; img.alt = shop.name || t("店家照片", "お店の写真");
    img.width = 640; img.height = 480;
    if (lazy) img.loading = 'lazy';
    img.addEventListener('error', () => {
      const fallback=el('span', `${className} image-unavailable`, t('照片暫時無法顯示', '写真を表示できません'));
      fallback.setAttribute('role','img'); fallback.setAttribute('aria-label',t('照片無法載入','写真を読み込めません')); img.replaceWith(fallback);
    });
    return img;
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      const response = await fetch(url, {credentials: 'same-origin', ...options, signal: controller.signal});
      if (response.status === 401 && location.pathname === '/admin' && !url.endsWith('/login')) {
        location.replace('/admin'); throw new Error(t("登入已過期，請重新登入。", "ログインの有効期限が切れました。"));
      }
      const statusError = () => Object.assign(new Error(t(
        `伺服器回應異常，請稍後重試。 (${response.status})`,
        `サーバーの応答に問題があります。再試行してください。 (${response.status})`
      )), {status: response.status});
      if (!response.ok) {
        const data = await response.json().catch(() => null);
        const error = statusError();
        if (typeof data?.detail === 'string') error.message = data.detail;
        throw error;
      }
      try { return await response.json(); }
      catch (error) { if (error.name === 'SyntaxError') throw statusError(); throw error; }
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error(t("連線未完成，請確認網路後重試。", "接続できませんでした。通信環境を確認して再試行してください。"));
      throw error;
    } finally { clearTimeout(timer); }
  }
  function json(method, payload) { return {method, headers: {'Content-Type': 'application/json'}, ...(payload === undefined ? {} : {body: JSON.stringify(payload)})}; }
  function fillSelect(node, label, values) {
    const current = node.value;
    const first = el('option', '', label); first.value = '';
    node.replaceChildren(first);
    values.forEach(value => { const option = el('option', '', value); option.value = value; node.append(option); });
    if (values.includes(current)) node.value = current;
  }
  function status(node, message, retry) {
    node.replaceChildren(el('p', '', message));
    node.hidden = !message;
    if (retry) { const button = el('button', '', t("重試", "再試行")); button.type = 'button'; button.addEventListener('click', retry); node.append(button); }
  }
  function tags(shop) { const div = el('div', 'tags'); (shop.tags?.length ? shop.tags : shop.keywords || []).forEach(tag => div.append(el('span', 'tag', tag))); return div; }
  function comments(node, items, internal = false) {
    node.replaceChildren();
    if (!items.length) { node.append(el('p', 'muted', t("還沒有朋友留下筆記。", "まだメモはありません。"))); return; }
    items.forEach(item => {
      const row = el('article', 'comment');
      const meta = el('div', 'meta');
      if (internal) meta.append(el('strong', '', item.created_by || t('未署名', '名前なし')), document.createTextNode(" · "));
      const date = el('time', '', item.created_at || t("日期未記錄", "日付なし"));
      if (item.created_at) date.dateTime = item.created_at;
      meta.append(date); row.append(meta, el('p', '', item.comment || '')); node.append(row);
    });
  }
  const debounce = (fn, delay = 200) => { let timer; return (...args) => { clearTimeout(timer); timer = setTimeout(() => fn(...args), delay); }; };
  applyLanguage();
  return {$, t, el, applyLanguage, safeUrl, safeImageUrl, mapsUrl, link, image, request, json, fillSelect, status, tags, comments, debounce};
})();
