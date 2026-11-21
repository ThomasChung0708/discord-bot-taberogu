(() => {
  const N=Notebook, {$,t}=N;
  let busy=false, failure=null;
  function feedback(){
    $('loginMessage').textContent=busy?t('登入中…','ログイン中…'):!failure?'':failure.status===401?t('密碼不正確，請重新輸入。','パスワードが違います。もう一度入力してください。'):failure.status===429?t('嘗試次數過多，請稍候再登入。','試行回数が多すぎます。しばらくしてからログインしてください。'):t('登入未完成，請確認連線後再試。','ログインできませんでした。接続を確認して再試行してください。');
  }
  $('loginForm').addEventListener('submit',async event=>{
    event.preventDefault();const button=event.submitter||$('loginForm').querySelector('button');busy=true;failure=null;button.disabled=true;$('password').disabled=true;feedback();
    try{await N.request('/api/admin/login',N.json('POST',{password:$('password').value}));$('password').value='';location.replace('/admin');}
    catch(error){failure=error;$('password').setAttribute('aria-invalid',String(error.status===401));}
    finally{busy=false;button.disabled=false;$('password').disabled=false;feedback();if(failure?.status===401)$('password').focus();}
  });
  $('password').addEventListener('input',()=>{$('password').removeAttribute('aria-invalid');});
  document.addEventListener('languagechange',feedback);
})();
