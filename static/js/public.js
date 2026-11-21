
    function safeUrl(value) {
      const url = String(value || "").trim();
      if (!/^https?:\/\//i.test(url) || /[\s\x00-\x1f\x7f\\]/.test(url)) return "";
      try {
        const parsed = new URL(url);
        return ["http:", "https:"].includes(parsed.protocol) && parsed.hostname ? url : "";
      } catch { return ""; }
    }

    function safeImageUrl(value) {
      const url = String(value || "").trim();
      if (/^\/uploads\/restaurants\/[A-Za-z0-9_-][A-Za-z0-9_.-]*$/.test(url) && !url.includes("..")) return url;
      return safeUrl(url);
    }

    const translations = {
      zh: {
        title: "共享美食清單",
        subtitle: "Discord 頻道裡收集的餐廳口袋名單",
        introTitle: "今天想去哪裡吃？",
        introCopy: "用店名、地區或料理類型快速翻找大家存下來的店。",
        mapLink: "地圖",
        searchPlaceholder: "搜尋店名、分類、地區、評論",
        allAreas: "全部地區",
        allCategories: "全部分類",
        summary: (count) => `目前顯示 ${count} 間餐廳`,
        lunch: "午餐",
        dinner: "晚餐",
        recommendedBy: "推薦者",
        businessHours: "營業時間"
      },
      ja: {
        title: "共有グルメリスト",
        subtitle: "Discord チャンネルで集めたお店リスト",
        introTitle: "今日はどこで食べる？",
        introCopy: "店名・エリア・料理ジャンルから、保存したお店をすぐに探せます。",
        mapLink: "地図",
        searchPlaceholder: "店名・分類・エリア・コメントを検索",
        allAreas: "すべてのエリア",
        allCategories: "すべての分類",
        summary: (count) => `${count} 件のレストランを表示中`,
        lunch: "ランチ",
        dinner: "ディナー",
        recommendedBy: "推薦者",
        businessHours: "営業時間"
      }
    };
    const savedLanguage = localStorage.getItem("tabelogLanguage");
    const state = { restaurants: [], areas: [], categories: [], language: savedLanguage || "zh" };
    const $ = (id) => document.getElementById(id);
    const t = (key, ...args) => {
      const value = translations[state.language][key] || translations.zh[key] || key;
      return typeof value === "function" ? value(...args) : value;
    };

    function applyLanguage() {
      document.documentElement.lang = state.language === "ja" ? "ja" : "zh-Hant";
      document.querySelectorAll("[data-i18n]").forEach((element) => {
        element.textContent = t(element.dataset.i18n);
      });
      document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
        element.placeholder = t(element.dataset.i18nPlaceholder);
      });
      $("langZh").classList.toggle("active", state.language === "zh");
      $("langJa").classList.toggle("active", state.language === "ja");
      renderFilters();
      renderCards();
    }

    function setLanguage(language) {
      state.language = language;
      localStorage.setItem("tabelogLanguage", language);
      applyLanguage();
    }

    function params() {
      const values = new URLSearchParams();
      if ($("keyword").value.trim()) values.set("keyword", $("keyword").value.trim());
      if ($("area").value) values.set("area", $("area").value);
      if ($("category").value) values.set("category", $("category").value);
      return values.toString();
    }

    async function loadRestaurants() {
      const response = await fetch(`/api/restaurants?${params()}`);
      const data = await response.json();
      state.restaurants = data.restaurants.sort((a, b) => a.id - b.id);
      state.areas = data.areas;
      state.categories = data.categories;
      renderFilters();
      renderCards();
    }

    function renderFilters() {
      fillSelect($("area"), t("allAreas"), state.areas);
      fillSelect($("category"), t("allCategories"), state.categories);
    }

    function fillSelect(select, label, values) {
      const current = select.value;
      select.innerHTML = `<option value="">${label}</option>`;
      values.forEach((value) => {
        const option = document.createElement("option");
        option.value = value;
        option.textContent = value;
        select.appendChild(option);
      });
      if (values.includes(current)) select.value = current;
    }

    function renderCards() {
      $("summary").textContent = t("summary", state.restaurants.length);
      $("grid").innerHTML = "";
      state.restaurants.forEach((restaurant) => {
        const card = document.createElement("article");
        card.className = `card ${safeImageUrl(restaurant.image_url) ? "has-image" : ""}`;
        card.innerHTML = `
          ${safeImageUrl(restaurant.image_url) ? `<img class="food-image" src="${escapeAttr(safeImageUrl(restaurant.image_url))}" alt="${escapeAttr(restaurant.name)}" onerror="this.remove()">` : ""}
          <h2>${escapeHtml(restaurant.name)}</h2>
          <div class="meta">ID ${restaurant.id} / ${escapeHtml(restaurant.category)} ${escapeHtml(restaurant.area || "")}</div>
          ${restaurant.recommended_by ? `<div class="meta">${t("recommendedBy")}：${escapeHtml(restaurant.recommended_by)}</div>` : ""}
          <div class="meta">${priceText(restaurant)}</div>
          ${restaurant.business_hours_text ? `<div class="meta">${t("businessHours")}：${escapeHtml(shortText(restaurant.business_hours_text, 80))}</div>` : ""}
          <div class="tags">${tagHtml(restaurant.tags || restaurant.keywords || [])}</div>
          <div class="comments">${escapeHtml(shortText(displayCommentText(restaurant)))}</div>
          <div class="links">
            ${safeUrl(restaurant.google_maps_url) ? `<a target="_blank" rel="noreferrer" href="${escapeAttr(safeUrl(restaurant.google_maps_url))}">Google Maps</a>` : ""}
            ${safeUrl(restaurant.tabelog_url) ? `<a target="_blank" rel="noreferrer" href="${escapeAttr(safeUrl(restaurant.tabelog_url))}">食べログ</a>` : ""}
          </div>
        `;
        $("grid").appendChild(card);
      });
    }

    function shortText(value, limit = 120) {
      return value.length > limit ? `${value.slice(0, limit)}...` : value;
    }

    function displayCommentText(restaurant) {
      const items = restaurant.comment_items || [];
      if (items.length) {
        const latest = items[items.length - 1];
        return latest.comment || "";
      }
      return restaurant.comments || "";
    }

    function priceText(restaurant) {
      const parts = [];
      if (restaurant.lunch_budget_text) parts.push(`${t("lunch")} ${restaurant.lunch_budget_text}`);
      if (restaurant.dinner_budget_text) parts.push(`${t("dinner")} ${restaurant.dinner_budget_text}`);
      return escapeHtml(parts.join(" / "));
    }

    function tagHtml(tags) {
      return tags.slice(0, 6).map((tag) => `<span class="tag">${escapeHtml(tag)}</span>`).join("");
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
      }[char]));
    }

    function escapeAttr(value) {
      return escapeHtml(value);
    }

    function debounce(fn, delay) {
      let timer = null;
      return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
      };
    }

    $("keyword").addEventListener("input", debounce(loadRestaurants, 250));
    $("area").addEventListener("change", loadRestaurants);
    $("category").addEventListener("change", loadRestaurants);
    $("langZh").addEventListener("click", () => setLanguage("zh"));
    $("langJa").addEventListener("click", () => setLanguage("ja"));
    applyLanguage();
    loadRestaurants();
  