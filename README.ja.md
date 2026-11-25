[繁體中文](README.md) | 日本語

# Discord レストラン記憶 Bot

これは、チャンネルに投稿されたレストランの写真、食べログの URL、チャットでの感想を整理し、検索できるレストランデータベースにまとめる Discord bot のプロトタイプです。

## 機能

- 対象のメッセージを右クリックまたは長押しし、「アプリ」→「保存餐廳資訊」（レストラン情報を保存）を選択します。そのメッセージだけを読み取り、店名、カテゴリ、エリア、みんなのコメントを抽出します。
- 対象のメッセージがほかのメッセージへの返信である場合、bot は返信先の元のメッセージも読み取ります。
- 店名または食べログの情報が見つかれば SQLite に保存します。コメントは後から手動で追加できます。
- 対象のコメントを右クリックまたは長押しし、「アプリ」→「保存為餐廳評論」（レストランのコメントとして保存）を選択します。レストランを選ぶと、そのメッセージをそのままコメントとして追加します。
- `/add_comment`：レストランと開始・終了メッセージのリンクを指定し、その範囲の Discord の原文をレストランのコメントとして追加します。
- `/edit_restaurant`：レストラン ID を指定して、店名、カテゴリ、エリア、リンク、コメント、キーワードを編集します。
- `/delete_restaurant`：レストラン ID を指定して、誤って登録されたレストランを削除します。削除するには `confirm` を `True` に設定する必要があります。
- `/backup_db`：現在 VM 上にあるレストランデータベースを SQLite ファイルにバックアップし、Discord に送信します。
- `/enrich_prices`：登録済みの食べログの URL から、ランチ・ディナーの価格を少しずつ取得して補完します。
- `/find_restaurant 拉麵`：キーワードで検索すると、レストランの選択メニューが表示されます。
- レストランを選択すると、食べログの URL、Google Maps のリンク、当時の Discord のコメントが表示されます。
- `/export_map_csv`：Google My Maps や Google Sheets にインポートできる CSV をエクスポートします。
- `/list_restaurants`：現在保存されているレストランと ID を一覧表示します。
- メッセージに Google Maps のリンクがある場合、bot は URL から店名の解析を試み、その店名から食べログの店舗 URL を検索します。
- `@bot 網頁` または `/web`：公開レストランページの URL を表示します。


## 設定

1. `.env.example` を `.env` にコピーします。
2. 以下を入力します。

```env
DISCORD_TOKEN=あなたの Discord bot token
OPENAI_API_KEY=あなたの OpenAI API key
OPENAI_MODEL=gpt-6-luna
OPENAI_REASONING_EFFORT=low
DB_PATH=restaurants.sqlite3
GOOGLE_SHEETS_ID=あなたの Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SHEETS_WORKSHEET=restaurants
GOOGLE_MY_MAPS_URL=あなたの Google My Maps 共有 URL
ADMIN_PASSWORD=管理画面のパスワード
SESSION_SECRET=自分で生成して保存するランダムなキー
PUBLIC_WEB_URL=https://discord-bottaberogudb.org/
PUBLIC_MAP_URL=https://discord-bottaberogudb.org/map
AUTO_SAVE_RESTAURANT_LINKS=true
ALLOWED_TEXT_CHANNEL_NAMES=食べログ
ALLOWED_TEXT_CHANNEL_IDS=
```

デフォルトのモデルは `gpt-6-luna` で、引き続き Chat Completions を使用します。`OPENAI_REASONING_EFFORT` が未設定、空欄、または無効な値の場合は、デフォルトの `low` を使用します。無効な値の場合は警告を記録しますが、元の値は出力しません。`none`、`low`、`medium`、`high`、`xhigh`、`max` を選択でき、`none` も明示的に指定できます。推論を使用する場合は temperature を省略します。モデルがオプションのパラメータを受け付けない場合に、パラメータを一つずつ削除して再試行する仕組みと、モデルごとのキャッシュは維持しています。出力の上限には推論 token も含まれるため、空の応答が返る場合は `max_completion_tokens` を増やしてください。本番運用の前に、代表的なリクエストを使って品質、レイテンシ、コストを評価してください。

`ALLOWED_TEXT_CHANNEL_NAMES` は、通常のメッセージの読み取り、チャットの記憶、レストランの自動保存、右クリックによるレストラン・コメントの保存を、指定したテキストチャンネル内に制限します。デフォルトでは `#食べログ` でのみ有効です。チャンネル ID で制御する場合は、`ALLOWED_TEXT_CHANNEL_IDS` に入力できます。複数の値はカンマで区切ります。

3. パッケージをインストールします。

```bash
pip install -r requirements.txt
```

4. 起動します。

```bash
python bot.py
```

## Discord の権限

Discord Developer Portal で以下を有効にする必要があります。

- Message Content Intent
- bot の権限：Read Message History、Send Messages、Use Slash Commands、Embed Links

`/edit_restaurant`、`/delete_restaurant`、`/sync_google_sheet`、`/backup_db` はサーバー内でのみ使用でき、実行者には「サーバー管理」（Manage Server / `manage_guild`）または「管理者」の権限が必要です。Discord のデフォルトのコマンド権限に加え、プログラムも実行時に権限を確認します。レストラン関連のコマンドには、引き続き `ALLOWED_TEXT_CHANNEL_NAMES` / `ALLOWED_TEXT_CHANNEL_IDS` によるチャンネル制限が適用されます。

## コメントの手動追加

1. 開始メッセージを右クリックし、「メッセージリンクをコピー」を選択します。
2. 終了メッセージを右クリックし、「メッセージリンクをコピー」を選択します。
3. Discord で以下を入力します。

```text
/add_comment restaurant:レストランIDまたはキーワード start_message:開始メッセージのリンク end_message:終了メッセージのリンク
```

キーワードで複数のレストランが見つかった場合、bot はレストラン ID を一覧表示します。次回は `restaurant` をその ID に変更してください。

## レストランの編集と削除

レストランを編集する際は、変更したい項目だけを入力します。空欄の項目は元のデータを保持します。

```text
/edit_restaurant restaurant_id:1 area:秋葉原 category:燒肉
```

誤操作を防ぐため、レストランを削除するには `confirm` を `True` に設定する必要があります。

```text
/delete_restaurant restaurant_id:1 confirm:True
```

## データベースのバックアップ

VM 上の `restaurants.sqlite3` は、bot が実際に使用しているメインのデータベースです。Discord で定期的に以下を実行できます。

```text
/backup_db
```

bot は現在のデータベースを `.sqlite3` ファイルにバックアップし、コマンドを実行した人に送信します。重要な更新の後には、ダウンロードして保存しておくことをおすすめします。

## 食べログの価格情報の補完

レストランに食べログの URL が登録されている場合は、ランチ・ディナーの価格を少しずつ取得し、DB に補完できます。

```text
/enrich_prices limit:5
```

食べログに一度に大量のリクエストを送らないよう、1 回につき 5 店舗程度の補完をおすすめします。その後、新しいレストランを保存する際にも、食べログの URL があれば bot が価格の自動取得を試みます。

## 食べログの営業時間の補完

レストランに食べログの URL が登録されている場合は、管理画面で「営業時間を取得して補完」を押すと、営業時間を少しずつ取得し、DB に補完できます。

1 回につき 5 店舗程度の補完をおすすめします。食べログのページ変更やリクエストのブロックにより取得できないことがありますが、その場合は既存のデータを上書きしません。後で再試行するか、管理画面から手動で入力できます。

## Google Maps から食べログを検索

レストラン情報を保存する際、メッセージに食べログのリンクがなく、Google Maps のリンクがある場合、bot は以下を試みます。

1. Google Maps の URL から店名を解析します。
2. 店名とエリアで食べログを検索します。
3. 店舗ページと思われる検索結果が見つかったら、`tabelog_url` に保存します。

Google や食べログの公式 API を使用していないため、見つからない場合や誤った候補が見つかる場合があります。見つからなくてもレストランの基本情報は保存されます。後から `/edit_restaurant` または管理画面で、正しい食べログの URL を追加できます。

## 共有マップについて

現在、各データには Google Maps の検索リンクが自動生成されます。また、`/export_map_csv` でエクスポートし、Google My Maps にインポートすることもできます。Google My Maps に直接書き込むための安定した公式 API はないため、次のステップとして以下のいずれかをおすすめします。

- CSV をエクスポートし、Google My Maps にインポートします。
- Google Sheets に書き込み、My Maps で同じスプレッドシートを読み込みます。
- 自分で Leaflet/Mapbox を使った小さな Web マップを構築する方法に切り替えます。

このバージョンでは、まずデータ構造を整えています。Google Sheets との同期は後から追加できます。

## Google Sheets との同期

My Maps のデータソースには Google Sheets の使用をおすすめします。bot は `/sync_google_sheet` を受け取ると、現在 SQLite に保存されているすべてのレストランを Sheet に同期します。

1. Google Cloud で service account を作成し、JSON キーをダウンロードします。
2. JSON をプロジェクトフォルダに配置します。例：`service-account.json`。
3. Google Sheet を作成し、URL に含まれる Sheet ID をコピーします。
4. service account のメールアドレスをその Google Sheet の共有先に追加し、権限を編集者に設定します。
5. `.env` に以下を入力します。

```env
GOOGLE_SHEETS_ID=あなたの Google Sheet ID
GOOGLE_SERVICE_ACCOUNT_FILE=service-account.json
GOOGLE_SHEETS_WORKSHEET=restaurants
```

6. Discord で `/sync_google_sheet` を入力します。
7. Google My Maps でその Google Sheet をインポートし、`name`、`area`、または `google_maps_url` を位置情報として選択します。

## 管理画面

`admin_app.py` は同じ `restaurants.sqlite3` を読み込み、2 つの入口を提供します。

- `/`：公開の読み取り専用ページです。友人はレストランの閲覧と検索ができますが、データは変更できません。
- `/admin`：管理画面です。編集、削除、インポート、同期には `ADMIN_PASSWORD` が必要です。

公開の一覧とマップには管理画面へのリンクはありません。管理者は `/admin` を直接開いてください。未ログイン時にはログインページだけが表示され、ログインに成功すると編集画面全体が利用できるようになります。パスワードはブラウザに保存しません。管理画面では、有効期間が 12 時間の HMAC-SHA256 署名付き cookie（`HttpOnly`、`SameSite=Strict`）を使用し、「ログアウト」ボタンを提供します。HTTPS の場合（ローカルのリバースプロキシによる `X-Forwarded-Proto: https` を含みます）は `Secure` を設定します。従来の `X-Admin-Password` header はサポートしていません。

本番環境では、固定かつ秘匿した `SESSION_SECRET` を設定してください。例えば、`python -c "import secrets; print(secrets.token_hex(32))"` を実行して生成できます。未設定の場合は警告を出し、プロセスごとに一時キーを生成するため、再起動するとログインが無効になります。複数の worker は同じキーを共有する必要があります。ログアウトすると、ブラウザの cookie は即座に削除されます。`SESSION_SECRET` または `ADMIN_PASSWORD` を変更すると、それまでに発行したすべての session cookie が無効になります。

ログインに 5 回失敗すると、その IP を 15 分間ロックします。ログインに成功すると、その IP の失敗記録を消去します。ローカルのリバースプロキシからの `X-Forwarded-For` に限り、最後のアドレスを IP の判定に使用します。失敗記録は各プロセスのメモリに保存します。現在の単一 worker 構成では利用できますが、複数 worker の構成では別途、共有のレート制限を設ける必要があります。API ドキュメントのルート `/docs`、`/redoc`、`/openapi.json` は無効化しています。公開 API は Discord の送信元チャンネル、送信元メッセージ、作成者のフィールドを返しません。推薦者は引き続き公開表示されます。

できること：

- すべてのレストランの閲覧
- レストランの検索
- エリアとカテゴリによる絞り込み
- 店名、カテゴリ、エリア、リンク、キーワード、コメントの編集
- ランチ・ディナーの価格と価格帯の手動編集
- 誤って登録されたレストランの削除
- コメントの追加
- Google Sheet から SQLite へのレストランのインポート
- Google Sheet との同期

ローカルでの起動方法：

Windows では、以下を直接ダブルクリックできます。

```text
start_admin.bat
```

管理画面のサービスが起動し、ブラウザが自動で開きます。管理画面を使用している間は、その黒いウィンドウを開いたままにしてください。ウィンドウを閉じると管理画面のサービスも停止します。

または、手動で以下を入力します。

```bash
uvicorn admin_app:app --host 127.0.0.1 --port 8000
```

その後、以下を開きます。

```text
http://127.0.0.1:8000/admin
```

公開の読み取り専用ページ：

```text
http://127.0.0.1:8000
```

VM の本番環境では、Caddy を通じて HTTPS を提供する必要があります。admin service は引き続き `127.0.0.1:8000` で待ち受け、TCP 8000 を外部に公開しないでください。友人は https://discord-bottaberogudb.org/ と https://discord-bottaberogudb.org/map を使用し、管理者は https://discord-bottaberogudb.org/admin を使用します。上記のローカル URL は開発・テスト専用です。

新しい VM への完全な復元手順は、[GCP VM デプロイ手順書](deploy/README_GCP_VM.md) を参照してください。固定 IP、Cloudflare DNS、swap、Windows からのデータ移行、systemd、Caddy について説明しています。**git clone だけでは `.env`、`service-account.json`、`restaurants.sqlite3`、`uploads/` は復元されません**。これらのデータは別途移行する必要があります。更新時には git pull の実行後、`discord-tabelog-bot` と `discord-tabelog-admin` を再起動して、分離されたテンプレートと静的リソースも一緒に更新する必要があります。上記の git 管理外のデータは上書きしないでください。

同期の方向に注意してください。

- 「Google Sheet からインポート」：Google Sheet → SQLite。Sheet で大量のデータを手動追加した後の利用に適しています。
- 「DB を Sheet に同期」：SQLite → Google Sheet。現在のデータベースの内容で Sheet を上書きします。
