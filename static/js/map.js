(() => {
  const N=Notebook, {$,t,el}=N;
  let selected=new URLSearchParams(location.search).get('shop');
  let shops=[], phase='loading', scrolled=false;
  const embed=$('myMapsFrame').getAttribute('src');
  if(!embed){$('mapContainer').hidden=true;$('emptyMap').hidden=false;}
  function mapHelp(){
    $('mapHelp').replaceChildren();
    if(embed){
      $('mapHelp').append(el('p','muted',t('地圖由 Google 載入。若無法顯示，可直接開啟共享地圖；店家位置請用各店的 Google Maps 連結確認。','地図は Google が読み込みます。表示されない場合は共有地図を直接開いてください。お店の場所は各店の Google Maps リンクで確認できます。')),
        N.link(t('直接開啟共享地圖','共有地図を直接開く'),embed,true));
    }
  }
  function choose(id){
    selected=String(id);scrolled=true;const url=new URL(location.href);url.searchParams.set('shop',selected);history.replaceState(null,'',url);render();
    $('selectedShop').focus();$('selectedShop').scrollIntoView({block:'nearest'});
  }
  function render(){
    mapHelp();$('mapList').replaceChildren();$('selectedShop').replaceChildren();$('selectedShop').hidden=true;
    if(phase==='loading')return N.status($('mapState'),t('讀取店家索引…','お店の索引を読み込み中…'));
    if(phase==='error')return N.status($('mapState'),t('清單讀取失敗，請重試。','リストを読み込めませんでした。'),load);
    const query=$('mapKeyword').value.trim().toLocaleLowerCase();
    const matches=shops.filter(shop=>[shop.name,shop.area,shop.category].join(' ').toLocaleLowerCase().includes(query));
    N.status($('mapState'),matches.length?t(`共 ${matches.length} 間店`,`${matches.length} 件のお店`):t('沒有符合的店家，請修改搜尋。','該当するお店はありません。検索条件を変更してください。'));
    if(!matches.length){const clear=el('button','',t('清除搜尋','検索をクリア'));clear.type='button';clear.addEventListener('click',()=>{$('mapKeyword').value='';render();$('mapKeyword').focus();});$('mapState').append(clear);}
    let current;
    matches.forEach(shop=>{
      const entry=el('div','map-entry');
      const row=el('a','map-row');row.href=`/?shop=${shop.id}`;row.append(el('strong','',shop.name),el('span','meta',[shop.area,shop.category].filter(Boolean).join(' · ')));
      const button=el('button','map-select',t('選取','選択'));button.type='button';button.setAttribute('aria-label',t(`選取 ${shop.name}`,`${shop.name} を選択`));button.setAttribute('aria-pressed',String(String(shop.id)===selected));button.addEventListener('click',()=>choose(shop.id));
      if(String(shop.id)===selected){row.setAttribute('aria-current','true');row.append(el('span','meta',t('已選取 · 查看筆記','選択中 · ノートを見る')));current=entry;}
      entry.append(row,button);$('mapList').append(entry);
    });
    const shop=shops.find(item=>String(item.id)===selected);
    if(shop){
      $('selectedShop').hidden=false;$('selectedShop').append(el('p','meta',t('已選取的店家','選択中のお店')),el('h3','',shop.name));
      const actions=el('div','actions');actions.append(N.link(t('在 Google Maps 開啟','Google Maps で開く'),N.mapsUrl(shop),true),N.link(t('筆記／詳情','ノート／詳細'),`/?shop=${shop.id}`));$('selectedShop').append(actions);
    }else if(selected){$('selectedShop').hidden=false;$('selectedShop').append(el('p','',t('這間店已不在清單中，請從索引選擇其他店家。','このお店はリストにありません。索引から別のお店を選んでください。')));}
    if(current&&!scrolled){requestAnimationFrame(()=>current.scrollIntoView({block:'nearest'}));scrolled=true;}
  }
  async function load(){phase='loading';render();try{shops=(await N.request('/api/restaurants')).restaurants.sort((a,b)=>a.id-b.id);phase='ready';}catch{phase='error';}render();}
  $('mapKeyword').addEventListener('input',N.debounce(render));document.addEventListener('languagechange',render);load();
})();
