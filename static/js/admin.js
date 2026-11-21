
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
        adminTitle: "食べログ Bot 管理後台",
        logout: "登出",
        publicPage: "公開頁",
        mapPage: "地圖",
        importSheet: "從 Google Sheet 匯入",
        syncSheet: "DB 同步到 Sheet",
        reload: "重新整理",
        createBackup: "立即備份 DB",
        cleanupTaxonomy: "整理地區/分類",
        enrichPrices: "補抓價格",
        enrichHours: "補抓營業時間",
        searchPlaceholder: "搜尋店名、分類、地區、評論",
        allAreas: "全部地區",
        allCategories: "全部分類",
        emptyEditor: "從左邊選一間餐廳開始編輯。",
        saveChanges: "儲存修改",
        deleteRestaurant: "刪除餐廳",
        name: "店名",
        category: "分類",
        area: "地區",
        recommendedBy: "推薦者",
        keywords: "關鍵字，用逗號分隔",
        lunchBudget: "午餐價格",
        dinnerBudget: "晚餐價格",
        lunchBudgetPlaceholder: "例：￥1,000～￥1,999",
        dinnerBudgetPlaceholder: "例：￥2,000～￥2,999",
        lunchMin: "午餐最低",
        lunchMax: "午餐最高",
        dinnerMin: "晚餐最低",
        dinnerMax: "晚餐最高",
        businessHours: "營業時間",
        imageUrl: "圖片 URL",
        uploadImage: "上傳圖片",
        uploadImageButton: "上傳圖片",
        uploadingImage: "上傳中...",
        comments: "評論",
        appendCommentTitle: "追加評論",
        appendCommentPlaceholder: "輸入要追加的評論，不會覆蓋原本內容",
        appendComment: "追加評論",
        count: (total) => `目前顯示 ${total} 間餐廳，ID 由小到大排列`,
        prev: "上一頁",
        next: "下一頁",
        noComments: "目前沒有獨立評論紀錄。",
        saveFailed: "儲存失敗，請確認欄位內容。",
        saved: "已儲存修改。",
        appendFailed: "追加評論失敗。",
        appended: "已追加評論。",
        chooseImage: "請先選擇圖片。",
        imageUploaded: "已上傳圖片。",
        imageUploadFailed: "圖片上傳失敗。",
        cleanupConfirm: "要把既有資料的地區與分類整理成統一格式嗎？",
        cleaning: "整理中...",
        cleanupFailed: "整理失敗",
        cleanupDone: (count) => `已整理 ${count} 筆餐廳`,
        enrichConfirm: "要從食べログ慢慢補抓最多 5 筆缺少的價格嗎？",
        enriching: "補抓中...",
        enrichFailed: "補抓價格失敗",
        enrichDone: (updated, checked) => `已檢查 ${checked} 筆，更新 ${updated} 筆價格。`,
        enrichHoursConfirm: "要從食べログ慢慢補抓最多 5 筆缺少的營業時間嗎？",
        enrichingHours: "補抓中...",
        enrichHoursFailed: "補抓營業時間失敗",
        enrichHoursDone: (updated, checked) => `已檢查 ${checked} 筆，更新 ${updated} 筆營業時間。`,
        backupConfirm: "要立即建立一份 SQLite 備份嗎？",
        backingUp: "備份中...",
        backupFailed: "備份失敗",
        backupDone: (filename) => `已建立備份：${filename}`,
        deleteConfirm: "確定要刪除這間餐廳嗎？這個動作不能復原。",
        deleteFailed: "刪除失敗。",
        importConfirm: "要把 Google Sheet 的資料匯入到本機資料庫嗎？同 ID 的餐廳會被 Sheet 內容更新。",
        importing: "匯入中...",
        importFailed: "匯入失敗",
        importDone: (imported, skipped) => `已從 Google Sheet 匯入 ${imported} 筆，略過 ${skipped} 筆。`,
        syncConfirm: "這會用目前資料庫內容覆蓋 Google Sheet。確定要繼續嗎？",
        syncing: "同步中...",
        syncFailed: "同步失敗",
        syncDone: (count, verified, maxId, worksheet) =>
          `已同步 ${count} 筆餐廳到 Google Sheet。\nSheet 分頁：${worksheet}\n讀回確認：${verified} 筆 / 最大 ID：${maxId ?? "-"}`,
      },
      ja: {
        adminTitle: "食べログ Bot 管理画面",
        logout: "ログアウト",
        publicPage: "公開ページ",
        mapPage: "地図",
        importSheet: "Google Sheet から取り込み",
        syncSheet: "DB を Sheet に同期",
        reload: "再読み込み",
        createBackup: "DB を今すぐバックアップ",
        cleanupTaxonomy: "エリア/分類を整理",
        enrichPrices: "価格を補完",
        enrichHours: "営業時間を補完",
        searchPlaceholder: "店名・分類・エリア・コメントを検索",
        allAreas: "すべてのエリア",
        allCategories: "すべての分類",
        emptyEditor: "左側からレストランを選択して編集を開始します。",
        saveChanges: "変更を保存",
        deleteRestaurant: "レストランを削除",
        name: "店名",
        category: "分類",
        area: "エリア",
        recommendedBy: "推薦者",
        keywords: "キーワード（カンマ区切り）",
        lunchBudget: "ランチ価格",
        dinnerBudget: "ディナー価格",
        lunchBudgetPlaceholder: "例：￥1,000～￥1,999",
        dinnerBudgetPlaceholder: "例：￥2,000～￥2,999",
        lunchMin: "ランチ最低額",
        lunchMax: "ランチ最高額",
        dinnerMin: "ディナー最低額",
        dinnerMax: "ディナー最高額",
        businessHours: "営業時間",
        imageUrl: "画像 URL",
        uploadImage: "画像をアップロード",
        uploadImageButton: "画像をアップロード",
        uploadingImage: "アップロード中...",
        comments: "コメント",
        appendCommentTitle: "コメントを追加",
        appendCommentPlaceholder: "追加するコメントを入力します。既存の内容は上書きしません",
        appendComment: "コメントを追加",
        count: (total) => `${total} 件のレストランを表示中（ID 昇順）`,
        prev: "前へ",
        next: "次へ",
        noComments: "独立したコメント記録はまだありません。",
        saveFailed: "保存に失敗しました。入力内容を確認してください。",
        saved: "変更を保存しました。",
        appendFailed: "コメントの追加に失敗しました。",
        appended: "コメントを追加しました。",
        chooseImage: "先に画像を選択してください。",
        imageUploaded: "画像をアップロードしました。",
        imageUploadFailed: "画像のアップロードに失敗しました。",
        cleanupConfirm: "既存データのエリアと分類を統一形式に整理しますか？",
        cleaning: "整理中...",
        cleanupFailed: "整理に失敗しました",
        cleanupDone: (count) => `${count} 件のレストランを整理しました`,
        enrichConfirm: "食べログから価格未入力の店を最大 5 件補完しますか？",
        enriching: "補完中...",
        enrichFailed: "価格補完に失敗しました",
        enrichDone: (updated, checked) => `${checked} 件を確認し、${updated} 件の価格を更新しました。`,
        enrichHoursConfirm: "食べログから営業時間未入力の店を最大 5 件補完しますか？",
        enrichingHours: "補完中...",
        enrichHoursFailed: "営業時間の補完に失敗しました",
        enrichHoursDone: (updated, checked) => `${checked} 件を確認し、${updated} 件の営業時間を更新しました。`,
        backupConfirm: "SQLite バックアップを今すぐ作成しますか？",
        backingUp: "バックアップ中...",
        backupFailed: "バックアップに失敗しました",
        backupDone: (filename) => `バックアップを作成しました：${filename}`,
        deleteConfirm: "このレストランを削除しますか？この操作は元に戻せません。",
        deleteFailed: "削除に失敗しました。",
        importConfirm: "Google Sheet のデータをローカル DB に取り込みますか？同じ ID のレストランは Sheet の内容で更新されます。",
        importing: "取り込み中...",
        importFailed: "取り込みに失敗しました",
        importDone: (imported, skipped) => `Google Sheet から ${imported} 件取り込み、${skipped} 件スキップしました。`,
        syncConfirm: "現在の DB 内容で Google Sheet を上書きします。続行しますか？",
        syncing: "同期中...",
        syncFailed: "同期に失敗しました",
        syncDone: (count, verified, maxId, worksheet) =>
          `${count} 件のレストランを Google Sheet に同期しました。\nシート：${worksheet}\n読み戻し確認：${verified} 件 / 最大 ID：${maxId ?? "-"}`,
      }
    };
    const savedLanguage = localStorage.getItem("tabelogLanguage");
    const state = {
      restaurants: [],
      selectedId: null,
      areas: [],
      categories: [],
      page: 1,
      pageSize: 10,
      language: savedLanguage || "zh"
    };

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
      $("adminLangZh").classList.toggle("active", state.language === "zh");
      $("adminLangJa").classList.toggle("active", state.language === "ja");
      backupButton.textContent = t("createBackup");
      cleanupButton.textContent = t("cleanupTaxonomy");
      enrichButton.textContent = t("enrichPrices");
      enrichHoursButton.textContent = t("enrichHours");
      renderFilters();
      renderList();
      if (!$("editorForm").hidden && state.selectedId) {
        setMessage($("message").textContent);
      }
    }

    function setLanguage(language) {
      state.language = language;
      localStorage.setItem("tabelogLanguage", language);
      applyLanguage();
    }

    const backupButton = document.createElement("button");
    backupButton.id = "createBackup";
    backupButton.type = "button";
    backupButton.textContent = t("createBackup");
    $("reload").insertAdjacentElement("beforebegin", backupButton);
    const cleanupButton = document.createElement("button");
    cleanupButton.id = "cleanupTaxonomy";
    cleanupButton.type = "button";
    cleanupButton.textContent = t("cleanupTaxonomy");
    backupButton.insertAdjacentElement("beforebegin", cleanupButton);
    const enrichButton = document.createElement("button");
    enrichButton.id = "enrichPrices";
    enrichButton.type = "button";
    enrichButton.textContent = t("enrichPrices");
    cleanupButton.insertAdjacentElement("beforebegin", enrichButton);
    const enrichHoursButton = document.createElement("button");
    enrichHoursButton.id = "enrichHours";
    enrichHoursButton.type = "button";
    enrichHoursButton.textContent = t("enrichHours");
    enrichButton.insertAdjacentElement("beforebegin", enrichHoursButton);
    const commentItems = document.createElement("div");
    commentItems.id = "commentItems";
    commentItems.className = "status";
    $("comments").closest("label").insertAdjacentElement("afterend", commentItems);

    function setMessage(text, isError = false) {
      $("message").textContent = text;
      $("message").style.color = isError ? "var(--danger)" : "var(--accent-strong)";
    }

    function adminHeaders() {
      return {"Content-Type": "application/json"};
    }

    async function adminFetch(url, options = {}) {
      const response = await fetch(url, {...options, credentials: "same-origin"});
      if (response.status === 401) {
        window.location.replace("/admin");
        throw new Error("Session expired");
      }
      return response;
    }

    function keywordParams() {
      const params = new URLSearchParams();
      if ($("keyword").value.trim()) params.set("keyword", $("keyword").value.trim());
      if ($("area").value) params.set("area", $("area").value);
      if ($("category").value) params.set("category", $("category").value);
      return params.toString();
    }

    async function loadRestaurants() {
      const response = await adminFetch(`/api/admin/restaurants?${keywordParams()}`);
      const data = await response.json();
      state.restaurants = data.restaurants.sort((a, b) => a.id - b.id);
      state.areas = data.areas;
      state.categories = data.categories;
      const maxPage = Math.max(1, Math.ceil(state.restaurants.length / state.pageSize));
      if (state.page > maxPage) state.page = maxPage;
      renderFilters();
      renderList();
      if (state.selectedId && !state.restaurants.some((item) => item.id === state.selectedId)) {
        clearEditor();
      }
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

    function renderList() {
      const total = state.restaurants.length;
      const maxPage = Math.max(1, Math.ceil(total / state.pageSize));
      const start = (state.page - 1) * state.pageSize;
      const pageItems = state.restaurants.slice(start, start + state.pageSize);

      $("count").innerHTML = `
        <span>${escapeHtml(t("count", total))}</span>
        <span class="pager">
          <button id="prevListPage" type="button" ${state.page <= 1 ? "disabled" : ""}>${escapeHtml(t("prev"))}</button>
          <span>${state.page} / ${maxPage}</span>
          <button id="nextListPage" type="button" ${state.page >= maxPage ? "disabled" : ""}>${escapeHtml(t("next"))}</button>
        </span>
      `;
      $("prevListPage").addEventListener("click", () => {
        state.page = Math.max(1, state.page - 1);
        renderList();
      });
      $("nextListPage").addEventListener("click", () => {
        state.page = Math.min(maxPage, state.page + 1);
        renderList();
      });

      $("restaurantList").innerHTML = "";
      pageItems.forEach((restaurant) => {
        const row = document.createElement("button");
        row.type = "button";
        row.className = `row ${restaurant.id === state.selectedId ? "active" : ""}`;
        row.innerHTML = `
          <div class="row-title">${escapeHtml(restaurant.name)}</div>
          <div class="row-meta">ID ${restaurant.id} / ${escapeHtml(restaurant.category)} ${escapeHtml(restaurant.area || "")}</div>
        `;
        row.addEventListener("click", () => selectRestaurant(restaurant.id));
        $("restaurantList").appendChild(row);
      });
    }

    async function selectRestaurant(id) {
      const response = await adminFetch(`/api/admin/restaurants/${id}`);
      const restaurant = await response.json();
      state.selectedId = restaurant.id;
      $("empty").hidden = true;
      $("editorForm").hidden = false;
      $("name").value = restaurant.name || "";
      $("categoryInput").value = restaurant.category || "";
      $("areaInput").value = restaurant.area || "";
      $("recommendedBy").value = restaurant.recommended_by || "";
      $("keywordsInput").value = (restaurant.tags || restaurant.keywords || []).join(", ");
      $("lunchBudget").value = restaurant.lunch_budget_text || "";
      $("dinnerBudget").value = restaurant.dinner_budget_text || "";
      $("lunchMin").value = restaurant.lunch_budget_min || "";
      $("lunchMax").value = restaurant.lunch_budget_max || "";
      $("dinnerMin").value = restaurant.dinner_budget_min || "";
      $("dinnerMax").value = restaurant.dinner_budget_max || "";
      $("businessHours").value = restaurant.business_hours_text || "";
      $("tabelogUrl").value = restaurant.tabelog_url || "";
      $("googleMapsUrl").value = restaurant.google_maps_url || "";
      $("imageUrl").value = restaurant.image_url || "";
      $("imageFile").value = "";
      renderImagePreview(restaurant.image_url || "");
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage("");
      renderList();
    }

    function renderImagePreview(url) {
      url = safeImageUrl(url);
      if (!url) {
        $("imagePreview").hidden = true;
        $("imagePreview").removeAttribute("src");
        return;
      }
      $("imagePreview").src = url;
      $("imagePreview").hidden = false;
    }

    function clearEditor() {
      state.selectedId = null;
      $("empty").hidden = false;
      $("editorForm").hidden = true;
      $("imageFile").value = "";
      renderImagePreview("");
      renderList();
    }

    function editorPayload() {
      return {
        name: $("name").value.trim(),
        category: $("categoryInput").value.trim(),
        area: $("areaInput").value.trim() || null,
        tabelog_url: $("tabelogUrl").value.trim() || null,
        google_maps_url: $("googleMapsUrl").value.trim() || null,
        image_url: $("imageUrl").value.trim() || null,
        recommended_by: $("recommendedBy").value.trim() || null,
        comments: $("comments").value.trim(),
        keywords: $("keywordsInput").value.split(",").map((item) => item.trim()).filter(Boolean),
        tags: $("keywordsInput").value.split(",").map((item) => item.trim()).filter(Boolean),
        lunch_budget_text: $("lunchBudget").value.trim() || null,
        lunch_budget_min: numberOrNull($("lunchMin").value),
        lunch_budget_max: numberOrNull($("lunchMax").value),
        dinner_budget_text: $("dinnerBudget").value.trim() || null,
        dinner_budget_min: numberOrNull($("dinnerMin").value),
        dinner_budget_max: numberOrNull($("dinnerMax").value),
        business_hours_text: $("businessHours").value.trim() || null
      };
    }

    function numberOrNull(value) {
      const text = String(value).trim();
      return text ? Number(text) : null;
    }

    function renderCommentItems(items) {
      if (!items.length) {
        $("commentItems").innerHTML = escapeHtml(t("noComments"));
        return;
      }
      $("commentItems").innerHTML = items.map((item) => `
        <div style="border:1px solid var(--line); border-radius:6px; padding:8px; margin-bottom:6px; background:#fff;">
          <strong>${escapeHtml(item.created_by || "Unknown")}</strong>
          <span style="color:var(--muted);"> ${escapeHtml(item.created_at || "")}</span>
          <div>${escapeHtml(item.comment || "")}</div>
        </div>
      `).join("");
    }

    $("editorForm").addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!state.selectedId) return;
      const response = await adminFetch(`/api/restaurants/${state.selectedId}`, {
        method: "PUT",
        headers: adminHeaders(),
        body: JSON.stringify(editorPayload())
      });
      if (!response.ok) {
        setMessage(t("saveFailed"), true);
        return;
      }
      setMessage(t("saved"));
      await loadRestaurants();
    });

    $("appendComment").addEventListener("click", async () => {
      if (!state.selectedId || !$("newComment").value.trim()) return;
      const response = await adminFetch(`/api/restaurants/${state.selectedId}/comments`, {
        method: "POST",
        headers: adminHeaders(),
        body: JSON.stringify({comment: $("newComment").value.trim(), created_by: "Admin"})
      });
      if (!response.ok) {
        setMessage(t("appendFailed"), true);
        return;
      }
      const restaurant = await response.json();
      $("comments").value = restaurant.comments || "";
      renderCommentItems(restaurant.comment_items || []);
      $("newComment").value = "";
      setMessage(t("appended"));
    });

    $("imageUrl").addEventListener("input", () => {
      renderImagePreview($("imageUrl").value.trim());
    });

    $("uploadImage").addEventListener("click", async () => {
      if (!state.selectedId) return;
      const file = $("imageFile").files[0];
      if (!file) {
        setMessage(t("chooseImage"), true);
        return;
      }
      const button = $("uploadImage");
      button.disabled = true;
      button.textContent = t("uploadingImage");
      try {
        const data = await file.arrayBuffer();
        const response = await adminFetch(`/api/restaurants/${state.selectedId}/image`, {
          method: "POST",
          headers: {
            "Content-Type": file.type
          },
          body: data
        });
        const restaurant = await response.json();
        if (!response.ok) throw new Error(restaurant.detail || t("imageUploadFailed"));
        $("imageUrl").value = restaurant.image_url || "";
        $("imageFile").value = "";
        renderImagePreview(restaurant.image_url || "");
        setMessage(t("imageUploaded"));
        await loadRestaurants();
      } catch (error) {
        setMessage(error.message, true);
      } finally {
        button.disabled = false;
        button.textContent = t("uploadImageButton");
      }
    });

    cleanupButton.addEventListener("click", async () => {
      if (!confirm(t("cleanupConfirm"))) return;
      cleanupButton.disabled = true;
      cleanupButton.textContent = t("cleaning");
      try {
        const response = await adminFetch("/api/cleanup-taxonomy", {
          method: "POST",
          headers: adminHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("cleanupFailed"));
        alert(t("cleanupDone", data.changed));
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        cleanupButton.disabled = false;
        cleanupButton.textContent = t("cleanupTaxonomy");
      }
    });

    enrichButton.addEventListener("click", async () => {
      if (!confirm(t("enrichConfirm"))) return;
      enrichButton.disabled = true;
      enrichButton.textContent = t("enriching");
      try {
        const response = await adminFetch("/api/enrich-prices", {
          method: "POST",
          headers: adminHeaders(),
          body: JSON.stringify({limit: 5})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("enrichFailed"));
        alert(t("enrichDone", data.updated, data.checked));
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        enrichButton.disabled = false;
        enrichButton.textContent = t("enrichPrices");
      }
    });

    enrichHoursButton.addEventListener("click", async () => {
      if (!confirm(t("enrichHoursConfirm"))) return;
      enrichHoursButton.disabled = true;
      enrichHoursButton.textContent = t("enrichingHours");
      try {
        const response = await adminFetch("/api/enrich-hours", {
          method: "POST",
          headers: adminHeaders(),
          body: JSON.stringify({limit: 5})
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("enrichHoursFailed"));
        alert(t("enrichHoursDone", data.updated, data.checked));
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        enrichHoursButton.disabled = false;
        enrichHoursButton.textContent = t("enrichHours");
      }
    });

    backupButton.addEventListener("click", async () => {
      if (!confirm(t("backupConfirm"))) return;
      backupButton.disabled = true;
      backupButton.textContent = t("backingUp");
      try {
        const response = await adminFetch("/api/backups", {
          method: "POST",
          headers: adminHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("backupFailed"));
        alert(t("backupDone", data.filename));
      } catch (error) {
        alert(error.message);
      } finally {
        backupButton.disabled = false;
        backupButton.textContent = t("createBackup");
      }
    });

    $("deleteRestaurant").addEventListener("click", async () => {
      if (!state.selectedId) return;
      if (!confirm(t("deleteConfirm"))) return;
      const response = await adminFetch(`/api/restaurants/${state.selectedId}`, {
        method: "DELETE",
        headers: adminHeaders()
      });
      if (!response.ok) {
        setMessage(t("deleteFailed"), true);
        return;
      }
      clearEditor();
      await loadRestaurants();
    });

    $("importSheet").addEventListener("click", async () => {
      if (!confirm(t("importConfirm"))) return;
      const button = $("importSheet");
      button.disabled = true;
      button.textContent = t("importing");
      try {
        const response = await adminFetch("/api/import-sheet", {
          method: "POST",
          headers: adminHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("importFailed"));
        alert(t("importDone", data.imported, data.skipped));
        clearEditor();
        await loadRestaurants();
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = t("importSheet");
      }
    });

    $("syncSheet").addEventListener("click", async () => {
      if (!confirm(t("syncConfirm"))) return;
      const button = $("syncSheet");
      button.disabled = true;
      button.textContent = t("syncing");
      try {
        const response = await adminFetch("/api/sync-sheet", {
          method: "POST",
          headers: adminHeaders()
        });
        const data = await response.json();
        if (!response.ok) throw new Error(data.detail || t("syncFailed"));
        alert(t("syncDone", data.count, data.verified_count, data.max_id, data.worksheet_name));
        if (safeUrl(data.worksheet_url)) {
          window.open(safeUrl(data.worksheet_url), "_blank", "noopener,noreferrer");
        }
      } catch (error) {
        alert(error.message);
      } finally {
        button.disabled = false;
        button.textContent = t("syncSheet");
      }
    });

    $("reload").addEventListener("click", loadRestaurants);
    $("logoutButton").addEventListener("click", async () => {
      const response = await adminFetch("/api/admin/logout", {method: "POST"});
      if (response.ok) window.location.replace("/admin");
    });

    $("keyword").addEventListener("input", debounce(() => {
      state.page = 1;
      loadRestaurants();
    }, 250));
    $("area").addEventListener("change", () => {
      state.page = 1;
      loadRestaurants();
    });
    $("category").addEventListener("change", () => {
      state.page = 1;
      loadRestaurants();
    });
    $("adminLangZh").addEventListener("click", () => setLanguage("zh"));
    $("adminLangJa").addEventListener("click", () => setLanguage("ja"));

    function debounce(fn, delay) {
      let timer = null;
      return (...args) => {
        clearTimeout(timer);
        timer = setTimeout(() => fn(...args), delay);
      };
    }

    function escapeHtml(value) {
      return String(value).replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#039;"
      }[char]));
    }

    applyLanguage();
    loadRestaurants();
  