(() => {
  const N = Notebook, {$,t,el} = N;
  const state = {shops:[], areas:[], categories:[], selected:null, dirty:false, phase:'loading', busy:false};
  let listVersion=0, selectionVersion=0;
  const fields = {
    name:'name', categoryInput:'category', areaInput:'area', recommendedBy:'recommended_by',
    lunchBudget:'lunch_budget_text', dinnerBudget:'dinner_budget_text', lunchMin:'lunch_budget_min', lunchMax:'lunch_budget_max',
    dinnerMin:'dinner_budget_min', dinnerMax:'dinner_budget_max', businessHours:'business_hours_text',
    tabelogUrl:'tabelog_url', googleMapsUrl:'google_maps_url', imageUrl:'image_url'
  };
  const numeric = new Set(['lunchMin','lunchMax','dinnerMin','dinnerMax']);
  const workbench = document.querySelector('.workbench');
  function message(text, error=false, node=$('message')) { node.textContent=text; node.classList.toggle('error',error); }
  function hasDrafts() { return state.dirty || Boolean($('newComment').value.trim()) || Boolean($('imageFile').files.length); }
  function discard() { return !hasDrafts() || confirm(t('有尚未儲存的修改。放棄這些修改並繼續？','未保存の変更があります。変更を破棄して続行しますか？')); }
  async function run(button, task, node=$('message')) {
    if (state.busy) return;
    state.busy=true;
    const controls=Array.from($('editorForm').elements).map(control=>[control,control.disabled]);
    controls.forEach(([control])=>{control.disabled=true;});
    button.disabled=true; $('editor').setAttribute('aria-busy','true');
    message(t('處理中，請稍候…','処理中です…'),false,node);
    try { await task(); } catch(error) { message(error.message,true,node); }
    finally { state.busy=false; controls.forEach(([control,disabled])=>{control.disabled=disabled;}); button.disabled=false; $('editor').setAttribute('aria-busy','false'); }
  }
  function renderList() {
    N.fillSelect($('area'),t('全部地區','すべてのエリア'),state.areas);
    N.fillSelect($('category'),t('全部分類','すべてのジャンル'),state.categories);
    $('restaurantList').replaceChildren();
    $('count').textContent=state.phase==='ready'?t(`共 ${state.shops.length} 間店`,`${state.shops.length} 件のお店`):'';
    if (state.phase==='loading') return N.status($('adminListState'),t('讀取店家清單…','リストを読み込み中…'));
    if (state.phase==='error') return N.status($('adminListState'),t('清單讀取失敗，請重試。','リストを読み込めませんでした。'),load);
    N.status($('adminListState'),state.shops.length?'':t('沒有符合的店家，請修改搜尋條件。','該当するお店はありません。検索条件を変更してください。'));
    state.shops.forEach(shop=>{
      const row=el('button','admin-row'); row.type='button'; row.dataset.testid='admin-open'; row.dataset.shopId=shop.id;
      row.setAttribute('aria-pressed',String(shop.id===state.selected?.id));
      row.append(el('strong','',shop.name),el('span','meta',[shop.area,shop.category].filter(Boolean).join(' · ')));
      row.addEventListener('click',()=>select(shop.id)); $('restaurantList').append(row);
    });
  }
  async function load() {
    const version=++listVersion; state.phase='loading'; renderList();
    const params=new URLSearchParams(); ['keyword','area','category'].forEach(key=>{if($(key).value.trim()) params.set(key,$(key).value.trim());});
    try {
      const data=await N.request(`/api/admin/restaurants?${params}`); if(version!==listVersion)return;
      state.shops=data.restaurants.sort((a,b)=>a.id-b.id); state.areas=data.areas; state.categories=data.categories; state.phase='ready';
    } catch { if(version!==listVersion)return; state.phase='error'; }
    renderList();
  }
  function preview() {
    const src=N.safeImageUrl($('imageUrl').value); $('imagePreview').hidden=!src;
    if(src) {$('imagePreview').src=src; $('imagePreview').alt=state.selected?.name||t('店家照片','お店の写真');}
    else $('imagePreview').removeAttribute('src');
  }
  function renderNotes() {
    if(!state.selected)return;
    N.comments($('commentItems'),state.selected.comment_items||[],true);
    $('legacySection').hidden=!state.selected.comments;
    $('comments').value=state.selected.comments ?? '';
  }
  function fillEditor(shop, preserveDrafts=false) {
    state.selected=shop; state.dirty=false; $('lunchMax').setCustomValidity(''); $('dinnerMax').setCustomValidity('');
    Object.entries(fields).forEach(([id,key])=>{$(id).value=shop[key]??'';});
    $('keywordsInput').value=(shop.tags?.length?shop.tags:shop.keywords||[]).join(', ');
    if(!preserveDrafts){$('newComment').value=''; $('imageFile').value='';}
    $('editorForm').hidden=false; $('empty').hidden=true;
    $('editorTitle').textContent=shop.name; workbench.classList.add('is-editing'); renderNotes(); preview(); renderList();
  }
  async function select(id) {
    // busy blocks overlapping loads/saves; selectionVersion discards a load invalidated by clear().
    if(state.busy || !discard())return;
    const version=++selectionVersion; state.busy=true;
    workbench.classList.add('is-editing'); $('editorForm').hidden=true; $('empty').hidden=true;
    message(t('讀取店家筆記…','お店のノートを読み込み中…'));
    try { const shop=await N.request(`/api/admin/restaurants/${id}`); if(version!==selectionVersion)return; fillEditor(shop); message(''); $('editorTitle').focus(); }
    catch(error){ message(error.message,true); $('empty').hidden=false; }
    finally { state.busy=false; }
  }
  function clear() { ++selectionVersion; state.selected=null; state.dirty=false; $('lunchMax').setCustomValidity(''); $('dinnerMax').setCustomValidity(''); $('newComment').value=''; $('imageFile').value=''; $('editorForm').hidden=true; $('empty').hidden=false; workbench.classList.remove('is-editing'); $('editorTitle').textContent=t('選擇店家','お店を選択'); message(''); renderList(); }
  function editorPayload() {
    // Omit legacy comments so saving cannot overwrite notes appended since this shop was loaded.
    const payload={keywords:[],tags:[]};
    Object.entries(fields).forEach(([id,key])=>{
      const value=$(id).value.trim();
      payload[key]=numeric.has(id)?(value===''?null:Number(value)):(value||null);
    });
    payload.keywords=$('keywordsInput').value.split(',').map(value=>value.trim()).filter(Boolean);
    payload.tags=payload.keywords;
    return payload;
  }
  $('editorForm').addEventListener('input',event=>{
    if(numeric.has(event.target.id)){$('lunchMax').setCustomValidity('');$('dinnerMax').setCustomValidity('');}
    if(event.target.id!=='newComment' && event.target.id!=='imageFile')state.dirty=true;
  });
  $('editorForm').addEventListener('submit',event=>{
    event.preventDefault(); if(!state.selected || state.busy)return;
    for(const [min,max] of [['lunchMin','lunchMax'],['dinnerMin','dinnerMax']]){
      $(max).setCustomValidity('');
      if($(min).value!=='' && $(max).value!=='' && Number($(min).value)>Number($(max).value)){
        $(max).setCustomValidity(t('最高預算不能低於最低預算。','上限は下限以上にしてください。'));
        $(max).reportValidity(); return;
      }
    }
    run(event.submitter || $('editorForm').querySelector('button[type=submit]'),async()=>{
      const shop=await N.request(`/api/restaurants/${state.selected.id}`,N.json('PUT',editorPayload()));
      fillEditor(shop,true); await load(); message(t('修改已儲存。','変更を保存しました。'));
    });
  });
  $('appendComment').addEventListener('click',()=>{
    if(!state.selected)return;
    if(!$('newComment').value.trim()){message(t('請先填寫要追加的筆記。','追加するメモを入力してください。'),true);$('newComment').focus();return;}
    run($('appendComment'),async()=>{
      const shop=await N.request(`/api/restaurants/${state.selected.id}/comments`,N.json('POST',{comment:$('newComment').value.trim(),created_by:'Admin'}));
      state.selected.comments=shop.comments; state.selected.comment_items=shop.comment_items; renderNotes(); $('newComment').value='';
      await load(); message(t('筆記已追加。其他欄位如有修改，請按儲存。','メモを追加しました。他の変更は保存ボタンで保存してください。'));
    });
  });
  $('imageUrl').addEventListener('input',preview);
  $('imagePreview').addEventListener('error',()=>{$('imagePreview').hidden=true;message(t('照片無法載入，請確認網址。','画像を読み込めません。URL を確認してください。'),true);});
  $('removeImage').addEventListener('click',()=>{
    if(!state.selected||state.busy)return;
    if(!confirm(t('清除照片欄位？按「儲存修改」後會移除此店家的照片，已上傳的本機圖片也會刪除。','写真欄をクリアしますか？「変更を保存」で写真を削除し、アップロード済みのローカル画像も削除します。')))return;
    $('imageUrl').value='';$('imageFile').value='';state.dirty=true;preview();message(t('照片待移除，請儲存修改。','写真を削除するには変更を保存してください。'));
  });
  $('uploadImage').addEventListener('click',()=>{
    if(!state.selected)return; const file=$('imageFile').files[0];
    if(!file){message(t('請先選擇照片。','写真を選択してください。'),true);return;}
    if(file.size>5*1024*1024){message(t('照片超過 5 MB，請縮小後重試。','画像は 5 MB 以下にしてください。'),true);return;}
    if(state.selected.image_url&&!confirm(t('立即上傳並替換已儲存的照片？原本的本機圖片會被刪除。','保存済みの写真をすぐに置き換えますか？元のローカル画像は削除されます。')))return;
    run($('uploadImage'),async()=>{
      const shop=await N.request(`/api/restaurants/${state.selected.id}/image`,{method:'POST',headers:{'Content-Type':file.type},body:await file.arrayBuffer()});
      state.selected.image_url=shop.image_url; $('imageUrl').value=shop.image_url||''; $('imageFile').value=''; preview();
      await load(); message(t('照片已上傳並儲存。','写真をアップロードして保存しました。'));
    });
  });
  $('deleteRestaurant').addEventListener('click',()=>{
    if(!state.selected||state.busy)return;
    if(!confirm(t(`從本機資料庫永久刪除「${state.selected.name}」及其評論與本機照片？無法復原；Google Sheet 不會同步刪除。`,`「${state.selected.name}」とメモ・ローカル写真を DB から完全に削除しますか？元に戻せません。Google Sheet は変更しません。`)))return;
    run($('deleteRestaurant'),async()=>{await N.request(`/api/restaurants/${state.selected.id}`,N.json('DELETE'));clear();await load();message(t('店家已刪除。','お店を削除しました。'),false,$('maintenanceStatus'));});
  });
  const operations=[
    ['importSheet','/api/import-sheet',()=>t('Google Sheet → 本機資料庫：匯入會以 Sheet 內容更新相同 ID 的店家並新增其他店家。現有欄位可能被覆寫；請先建立備份。繼續？','Google Sheet → ローカル DB：同じ ID のお店を更新し、他のお店を追加します。既存の項目が上書きされる場合があります。バックアップ後に続行しますか？'),null],
    ['syncSheet','/api/sync-sheet',()=>t('本機資料庫 → Google Sheet：將以目前資料庫覆寫遠端工作表內容。遠端手動修改可能遺失。繼續？','ローカル DB → Google Sheet：現在の DB でリモートシートを上書きします。シートでの手動変更は失われる場合があります。続行しますか？'),null],
    ['cleanupTaxonomy','/api/cleanup-taxonomy',()=>t('整理本機資料庫所有店家的地區與分類？現有文字會更新為統一格式，建議先備份。','ローカル DB 全体のエリア・分類を統一しますか？既存の表記が変更されます。先にバックアップしてください。'),null],
    ['enrichPrices','/api/enrich-prices',()=>t('Tabelog → 本機資料庫：最多檢查 5 間店，將查得的價格寫入缺少價格的紀錄。繼續？','Tabelog → ローカル DB：最大 5 件を確認して、価格のない記録を補完します。続行しますか？'),{limit:5}],
    ['enrichHours','/api/enrich-hours',()=>t('Tabelog → 本機資料庫：最多檢查 5 間店，將查得的營業時間寫入缺少時間的紀錄。繼續？','Tabelog → ローカル DB：最大 5 件を確認して、営業時間のない記録を補完します。続行しますか？'),{limit:5}]
  ];
  operations.forEach(([id,url,confirmation,payload])=>$(id).addEventListener('click',()=>{
    if(state.busy || !discard() || !confirm(confirmation()))return;
    run($(id),async()=>{
      const data=await N.request(url,N.json('POST',payload===null?undefined:payload));
      clear(); await load();
      let result=t('操作完成。','操作が完了しました。');
      if(id==='importSheet')result=t(`匯入 ${data.imported} 間，略過 ${data.skipped} 間。`,`${data.imported} 件取込、${data.skipped} 件スキップ。`);
      if(id==='syncSheet')result=t(`已同步 ${data.count} 間至 Google Sheet；讀回確認 ${data.verified_count} 間。`,`${data.count} 件を Google Sheet に同期。${data.verified_count} 件を読み戻して確認。`);
      if(id==='cleanupTaxonomy')result=t(`已整理 ${data.changed} 間店。`,`${data.changed} 件を整理しました。`);
      if(id==='enrichPrices'||id==='enrichHours')result=t(`已檢查 ${data.checked} 間，更新 ${data.updated} 間。`,`${data.checked} 件確認、${data.updated} 件更新。`);
      message(result,false,$('maintenanceStatus'));
      if(N.safeUrl(data.worksheet_url))$('maintenanceStatus').append(document.createTextNode(' '),N.link(t('開啟 Google Sheet','Google Sheet を開く'),data.worksheet_url,true));
    },$('maintenanceStatus'));
  }));
  async function backups() {
    N.status($('backupList'),t('讀取備份清單…','バックアップ一覧を読み込み中…'));
    try {
      const data=await N.request('/api/backups'); $('backupList').replaceChildren();
      if(!data.backups.length)N.status($('backupList'),t('還沒有備份。可先建立一份。','バックアップはありません。作成しておきましょう。'));
      data.backups.forEach(filename=>{
        const row=el('div','backup-link'); const a=el('a','button-link',t('下載','ダウンロード'));
        a.href=`/api/backups/${encodeURIComponent(filename)}`; a.download=filename; a.setAttribute('aria-label',t(`下載 ${filename}`,`${filename} をダウンロード`));
        row.append(el('span','',filename),a);$('backupList').append(row);
      });
    }catch(error){N.status($('backupList'),error.message,backups);}
  }
  $('listBackups').addEventListener('click',backups);
  $('createBackup').addEventListener('click',()=>{
    if(state.busy || !confirm(t('資料庫 → 本機備份：建立目前已儲存資料的備份，超過保留數量的最舊備份將依既有設定輪替移除。繼續？','DB → ローカルバックアップ：保存済みのデータをバックアップします。保存数を超える最古のバックアップは設定に従って削除されます。続行しますか？')))return;
    run($('createBackup'),async()=>{const data=await N.request('/api/backups',N.json('POST'));await backups();message(t(`已建立備份：${data.filename}`,`バックアップを作成：${data.filename}`),false,$('maintenanceStatus'));},$('maintenanceStatus'));
  });
  $('backToList').addEventListener('click',()=>{if(state.busy||!discard())return;const id=state.selected?.id;clear();const row=Array.from(document.querySelectorAll('[data-testid="admin-open"]')).find(node=>node.dataset.shopId===String(id));(row||$('keyword')).focus();});
  $('reload').addEventListener('click',()=>{if(state.busy||!discard())return;clear();load();});
  $('logoutButton').addEventListener('click',()=>{if(!discard())return;run($('logoutButton'),async()=>{await N.request('/api/admin/logout',N.json('POST'));location.replace('/admin');},$('maintenanceStatus'));});
  $('keyword').addEventListener('input',N.debounce(load)); ['area','category'].forEach(id=>$(id).addEventListener('change',load));
  document.addEventListener('languagechange',()=>{renderList();renderNotes();if(!state.selected)$('editorTitle').textContent=t('選擇店家','お店を選択');});
  window.addEventListener('beforeunload',event=>{if(hasDrafts()||state.busy){event.preventDefault();event.returnValue='';}});
  load();
})();
