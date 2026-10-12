# ローカルAI版（0.5、試作）

Premiere不要。ローカル動画 → faster-whisper文字起こし → ローカルLLMの候補 → 人の確認 → FFmpeg MP4という経路です。字幕だけから意味を判断するため、映像の事故・表情・ゲーム画面そのものは理解しません。候補には実際の字幕IDと引用を要求し、存在しない引用や不正な時刻を拒否します。それでも「面白さ」やASRの正確さは保証できません。

## Windowsで起動

現在はソース配布です。ワンクリックEXE／インストーラーはまだありません。Python3.10以上（検証は3.12）、FFmpeg/ffprobe（libx264対応）、日本語フォントを用意してください。

```powershell
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install ".[local-ai]"
.venv\Scripts\python -m clipbeta serve --workspace clipbeta-work
```

初回準備後は`start-local.cmd`のダブルクリックでも起動できます。このランチャーは依存関係やモデルを自動取得しません。

PowerShell実行ポリシーの変更は不要です。Python実行ファイルを直接指定できます。FFmpeg/ffprobeはPATH、または環境変数`CLIPBETA_FFMPEG_PATH` / `CLIPBETA_FFPROBE_PATH`で指定します。日本語フォントは画面の通常・太字フォント欄、または`CLIPBETA_FONT_PATH` / `CLIPBETA_BOLD_FONT_PATH`で指定できます。Notoがない場合は両方を指定してください。Noto Sans CJK推奨。Windows既存フォントの使用可否はそのフォントのライセンスを確認してください。

ローカルファイル経路のブラウザは127.0.0.1に接続します。任意のTwitch取得機能を選んだ場合だけ、サーバーから公開Twitch/CDNへ接続します。AI推論先は引き続きローカルだけです。`--no-browser`なら表示されたセッションURLを開きます。ネットワーク公開用ではありません。URLの一時トークンは他人に共有しないでください。

## モデルの事前準備（明示的なダウンロード）

アプリはモデルをダウンロードしません。初回に利用者が公式配布元から取得し、ライセンスを確認してください。モデルや実行ファイルはこのリポジトリに含めません。

- ASR: [faster-whisper公式](https://github.com/SYSTRAN/faster-whisper)。変換済みモデルのローカルフォルダーに`model.bin`, `config.json`, `tokenizer.json`, `vocabulary.txt`を置きます。例: [Systran tiny](https://huggingface.co/Systran/faster-whisper-tiny)。tinyは動作確認向けで、日本語実配信の品質を保証しません。より大きいモデルは追加メモリーと処理時間が必要です。
- 通常のLLM経路: [Ollama](https://docs.ollama.com/faq#how-do-i-disable-ollama-cloud-features)をインストールし、`OLLAMA_NO_CLOUD=1`、`OLLAMA_HOST=127.0.0.1:11434`で起動。モデルは事前に明示的にpullします。画面には実際にインストール済みのローカルモデル名を入力します。クラウドモデルは拒否します。
- 軽量検証経路: [llama.cpp server](https://github.com/ggml-org/llama.cpp/tree/master/tools/server)をローカルGGUFで起動し、画面でllama.cppを選択します。固定ポート8080です。絶対パスの`-m`と明示的な`--alias`を指定した単一モデルのサーバーが必要で、ルーターモードは拒否します。`--offline --host 127.0.0.1 --port 8080 -ngl 0 -t 2 -tb 2 -c 4096 -np 1`を使います。画面のモデル名には`/v1/models`が返すモデル名（起動時に`--alias`を付けると明確）を指定します。
- 候補モデル例: [Qwen3-4B](https://huggingface.co/Qwen/Qwen3-4B)、小さい接続確認用は[公式Qwen3-0.6B GGUF](https://huggingface.co/Qwen/Qwen3-0.6B-GGUF)。いずれもモデルカードとライセンスを確認してください。GPU性能・速度・日本語の候補品質は対象Windows機で未計測です。

faster-whisper1.2.1とPyAV19の組み合わせで実デコードエラーを確認したため、現時点の追加依存は`av==16.0.1`へ固定しています。将来更新時は実音声で再検証してください。

### 明示的な初回モデル準備の例

以下は利用者が実行するダウンロード操作です。アプリは実行しません。

```powershell
# まず日本語の合成テストで検証したWhisper smallを明示的に取得（約486MB）
.venv\Scripts\hf download Systran/faster-whisper-small --local-dir models\whisper-small --include model.bin config.json tokenizer.json vocabulary.txt
# Ollamaをローカル専用で起動するターミナル
$env:OLLAMA_NO_CLOUD="1"
$env:OLLAMA_HOST="127.0.0.1:11434"
ollama serve
# 別ターミナルで、選んだローカルモデルを明示的に取得
ollama pull qwen3:4b
```

Ollamaが既に常駐している場合は、そのプロセスにもローカル専用設定が適用されていることを確認してください。上のモデル名は構成例で、対象PCでの性能保証ではありません。

## 使い方と保存

1. 動画を選択。最大8GiB。選択ファイルは作業フォルダーへローカルコピーされます。十分な空き容量が必要です。
2. ASRモデルのフォルダー、CPU/GPU、用語、起動済みLLMを指定。既存SRT/JSONを選ぶ場合はASRを明示的に省略します。
3. 解析。音声は分割処理、LLMには時間窓単位の字幕を渡します。ASRモデルを解放してからLLMへ進みます。CPUフォールバックはチェックした場合だけです。
4. 候補を選び、動画を見ながら字幕・単語色・SE・ズームを確認。全ての字幕を採用／不採用にしてMP4書き出し。
5. MP4と編集JSONを保存。生成物と解析JSONは作業フォルダーにも残ります。元動画は変更せず、出力フォルダーは毎回別になります。

ブラウザを再読み込みすると画面の選択・未保存レビューはリセットされますが、実行中ジョブには再接続できます。サーバー再起動後のジョブ復元は未実装です。解析の再実行は可能ですが、モデルの出力は毎回完全一致する保証がありません。候補の開始／終了は画面で60秒以内の範囲に調整できます。範囲変更時は字幕レビューを作り直すため、未保存の編集はリセットされます。CLIの字幕区間指定と編集JSONは引き続き利用できます。

キャンセルはFFmpeg・HTTP待機中に反映し、ASRはセグメント境界で協調停止します。ネイティブASR演算中は即時停止しない場合があります。処理中は別ジョブを開始できません。ブラウザへコピー中のファイル転送は現状キャンセル対象外です。

## プライバシーと限界

推論の外部API、動画アップロードはアプリの処理に含みません。検証中にONNX Runtimeの初期化テレメトリーを検出したため、任意機能のSilero VADを無効にし、ONNXを読み込まない標準Whisper経路を使います。実スモークでONNXが読み込まれないことを確認しています。Ollamaは上記ローカル専用設定が必要です。モデル取得と依存パッケージのインストールには初回ネット接続が必要です。

作業フォルダーには動画・音声・字幕・レビュー結果が残るので、自分で管理してください。自動削除は行いません。単一利用者の信頼されたPC向けで、共有PC・インターネット公開・複数ユーザーの認証には対応しません。

Twitch取得の対応範囲・検証状態は[取得状況](TWITCH_URL_STATUS.md)を確認してください。配信映像の視覚理解、話者分離、字幕の語単位手動時刻編集、Premiere連携、Windowsバイナリ配布は未実装です。長時間VODは分割設計ですが、数時間の実配信での性能・品質は未検証です。

## 任意のTwitch URL入力（β・実取得未検証）

```powershell
.venv\Scripts\python -m pip install ".[twitch]"
```

URL入力欄にチャンネルURLまたはVOD URLを貼り、一覧から完了済みVODを選ぶと、公開動画の取得後に設定済みのローカルAI解析へ進みます。再読み込みで取得ジョブへ戻った場合は、取得完了後に解析開始を押します。AI設定は先に済ませてください。

取得上限は2GiB／10分、取得・MP4変換には約4.2GiBの空き容量が必要です。長いアーカイブはこの試作の上限を超える可能性があります。限定公開・サブスク限定・進行中配信、暗号化HLS・分割MP4は対象外です。ログインやCookieの読み取り、アクセス拒否の回避はしません。公開で見られることと再利用権限は別なので、権利・許可のある動画だけを使ってください。

実装は通常の公開再生経路を利用するため、Twitch側変更で動かなくなる場合があります。クラウド検証環境ではgql.twitch.tvのDNS解決ができず、実Twitch動画の取得からMP4までの一連の検証は未完了です。模擬転送・安全性テストと合成TS→MP4変換を検証しています。詳しくは[取得状況](TWITCH_URL_STATUS.md)を参照してください。

日本語の追加検証結果と、tiny/smallの実際の誤りは[日本語検証](JAPANESE_VALIDATION.md)を参照してください。smallの結果も合成素材1件に限られます。
