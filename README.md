# VTuber Clipping Beta · FU-NK LABO

字幕からレビュー可能な編集プランを作り、ローカル動画へ字幕・ズーム・合成SEを反映する試作です。

開発名義: [FU-NK LABO / fu-nk-labo](https://github.com/fu-nk-labo)。
ソース: [fu-nk-labo/vtuber-clipping](https://github.com/fu-nk-labo/vtuber-clipping)。

MITライセンスで利用できます。Copyright (c) 2026 fu-nk-labo。
[LICENSE](LICENSE) と [第三者依存関係の注意事項](THIRD_PARTY_NOTICES.md) を確認してください。

## できること

- SRT / JSON字幕の区間選択、規則ベースの装飾提案
- オフラインHTMLで本文・色・強調・SE・ズームを編集し、採用／不採用
- 「かわいい」だけピンクなど、単語範囲ごとの色・強調
- 座標を指定したズームと、明示的な効果なし
- 検証済みJSONプラン、安定ID、再適用差分プレビュー
- FFmpegでローカルMP4をレンダリング。既存出力は上書きしない

Twitch URL入力画面は**合成データのモック**です。API接続、動画取得、音声認識、AIによる見どころ検出、Premiere連携は未実装です。
実配信や実アカウント情報はサンプルに含みません。

## クイックスタート

Python 3.10+。プラン作成とHTMLレビューは外部Python依存なしです。

```sh
python -m venv .venv
# Linux / macOS
. .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install .
clipbeta --help
clipbeta intake-demo --out intake-demo.html
```

`intake-demo.html` をブラウザで開くとモックのURL入力体験ができます。
保存せずに閉じると編集内容は失われます。ソースからなら `python -m clipbeta` でも実行できます。

字幕から始める場合:

```sh
clipbeta suggest samples/synthetic.srt --start 1 --end 12 --duration 15 --out proposal.json
clipbeta html proposal.json --out review.html
```

`review.html` を開き、全項目を採用または不採用にして `review.json` を保存します。

```sh
clipbeta review proposal.json review.json --out reviewed.json
clipbeta export reviewed.json --out edit-plan.json
clipbeta validate edit-plan.json
```

## 動画プレビュー

別途FFmpeg / ffprobe（drawtext・libx264・AAC対応）、Noto Sans CJK regular/bold、Pillowが必要です。
これらのバイナリ・フォントは同梱していません。現状レンダラーはLinuxの既定フォントパスを使用します。

```sh
python -m pip install '.[render]'
clipbeta synthetic-media --out synthetic-source.mp4
clipbeta render samples/word-styles/edit-plan.json synthetic-source.mp4 --out-dir preview
```

`preview/preview.mp4` が出力されます。自分が編集権限を持つローカル録画と対応する字幕にも使えます。
URLやプレイリストは動画入力として受け付けません。

制限: 640×360 / 30fps、最大60秒・50字幕、入力4K以下、重複時間の字幕は拒否。
SEは短い合成トーンです。モデル呼び出し・課金・アップロードは行いません。
詳細は [使い方と形式](docs/USAGE.md)、[Twitch連携の境界](docs/TWITCH.md)、[依存関係](THIRD_PARTY_NOTICES.md) を参照してください。

## 開発とテスト

```sh
python -m unittest discover -s tests -v
node tests/flow_state.test.js
node tests/review_js_smoke.js
node tests/intake_ui_smoke.js
node tests/word_review_ui_smoke.js
python -m compileall -q clipbeta tests
python scripts/scan_public_tree.py
```

完全なテストには動画プレビューの依存関係とNode.jsが必要です。
GitHub Actions設定は用意していますが、ローカル検証とGitHub上の実行結果は別です。
[CONTRIBUTING.md](CONTRIBUTING.md) に変更時の確認事項をまとめています。
