<h1 align="center">UI Clone Skills</h1>

<p align="center">
  <strong>見た目だけでなく、Webサイトの動きまでクローンする。</strong>
</p>

<p align="center">
  <a href="#skills"><img alt="Agent Skills" src="https://img.shields.io/badge/Agent_Skills-3-1FC07C?style=flat-square&amp;labelColor=black"></a>
  <a href="https://claude.com/product/claude-code"><img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-compatible-D97757?style=flat-square&amp;labelColor=black&amp;logo=anthropic&amp;logoColor=white"></a>
  <a href="https://github.com/openai/codex"><img alt="Codex" src="https://img.shields.io/badge/Codex-compatible-412991?style=flat-square&amp;labelColor=black&amp;logo=openai&amp;logoColor=white"></a>
  <a href="#what-it-recovers"><img alt="Input" src="https://img.shields.io/badge/input-live_URL-2EAD33?style=flat-square&amp;labelColor=black"></a>
  <a href="https://github.com/voidmatcha/ui-clone-skills/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/voidmatcha/ui-clone-skills/ci.yml?branch=main&amp;label=CI&amp;style=flat-square"></a>
  <a href="./LICENSE.txt"><img alt="License" src="https://img.shields.io/github/license/voidmatcha/ui-clone-skills?style=flat-square"></a>
</p>

<p align="center">
  <a href="README.md">🇺🇸 English</a> | <a href="README.ko.md">🇰🇷 한국어</a> | <strong>🇯🇵 日本語</strong> | <a href="README.zh-cn.md">🇨🇳 简体中文</a>
</p>

<!-- README-CANONICAL-REVISION: sha256=94c5893d3844012801dbd4251fea7ac2b0d4018a4484dfbd6b2b975e75c08243; bytes=exact-README.md-UTF-8; translation-quality=not-attested -->

`ui-clone-skills` は、公開中のWebサイトを根拠に基づく React + Tailwind 実装へ変換します。レンダリングされたページをキャプチャし、実際の CSS とアセットをダウンロードし、レスポンシブスタイルと計算済みスタイルを読み取り、JavaScript バンドルからアニメーションのパラメータを復元したうえで、複数のビューポートとインタラクション状態にわたって結果を検証します。

これは、アニメーションを伴うWebのためのモーション・フォレンジックです。スクリーンショットからコードを生成するモデルでは肝心な部分を捉えられないページ、たとえば GSAP タイムライン、Framer Motion のスプリング、Webflow IX2 インタラクション、Lenis のスムーズスクロール、Lottie 再生、ホバー状態、スクロール表示、スティッキーセクション、レスポンシブトランジションを備えたページのために作られています。

| 一つの公開URLを入力 | パイプラインの処理 | 出力されるもの |
| --- | --- | --- |
| **キャプチャ** | デスクトップ、タブレット、モバイル、スクロール、ホバー、クリック、トランジションの証拠を記録 | リファレンスフレーム、動画、DOMマップ、セクションマップ |
| **解析** | スタイルシート、計算済みの値、アセット、フォント、バンドル、モーションパラメータを抽出 | `transition-spec.json`、ランタイム証拠、計測済みレイアウトデータ |
| **再現** | 見た目を創作せず、観測した構造と値から構築 | React/TSX、Tailwind、保持された CSS、ローカルアセット |
| **検証** | レイアウトゲート、絶対誤差（AE）、構造的類似性（SSIM）、モーションチェックでリファレンスと実装を比較 | 再現可能な合否の証拠と、範囲を限定した修正 |

## 試してみる

プラグインをインストールし、コーディングエージェントに公開URL、対象、出力先ディレクトリを指定します。

```text
Clone the hero and pricing sections from https://example.com into React + Tailwind.
Preserve the responsive layout, scroll reveals, and hover motion. Output to ./out/.
```

まず `ui-reverse-engineering` を使います。既存の実行を検出し、最後に実証されたパイプライン状態から再開して、利用可能な証拠を破棄することなく、キャプチャ、抽出、生成、検証、差異診断のいずれかへ処理を振り分けます。

## 何が違うのか

スクリーンショットからコードを生成するツールは、一枚以上の画像内のピクセルから実装を推測します。`ui-clone-skills` は、そのピクセルを生み出した稼働中のソース・オブ・トゥルースを調べ、再現したページが同じように動作するかを検証できます。

| 一般的なビジュアルジェネレーター | `ui-clone-skills` |
| --- | --- |
| スクリーンショットからレイアウトを近似する | CSS をダウンロードし、レンダリング済み DOM を計測する |
| イージング、継続時間、トリガーのタイミングを推測する | CSS と JavaScript バンドルから値を抽出する |
| 目に見えるデスクトップ画面だけを再現する | デスクトップ、タブレット、モバイル、スクロール位置をキャプチャする |
| モーションを後から加える仕上げとして扱う | 実装前に共有モーション仕様を作成する |
| ビルドが通る、またはもっともらしく見えれば完了とする | レンダリング、構造、アセット、モーションの証拠を必須とする |

目標は、もっともらしい模倣ではありません。目標は、表示アセット、DOM 構造、レスポンシブ挙動、モーションをリファレンスと比較できるクローンです。

## 他のオープンソースツールとの違い

オープンソースのWebサイト再現ツールは、参照する証拠も到達する出力も異なります。必要な結果に合わせて選択してください。

| プロジェクト | 最適な用途 | `ui-clone-skills` との違い |
| --- | --- | --- |
| [Screenshot to Code](https://github.com/abi/screenshot-to-code) | スクリーンショット、モックアップ、Figma デザイン、画面録画を HTML、React、Vue に変換 | 視覚入力からコードを生成します。`ui-clone-skills` は公開URLから始め、CSS、バンドル、ランタイム状態、インタラクションの証拠を調査します |
| [AI Website Cloner Template](https://github.com/JCodesMore/ai-website-cloner-template) | 計算済みスタイルの調査、インタラクションの確認、実アセット、並列ビルダーエージェントを使って Next.js クローンを構築 | この比較対象の中では最も近いツールです。`ui-clone-skills` は、再利用可能なキャプチャ、診断、監査ワークフロー、バンドル由来のモーション仕様、再開可能なゲート、決定論的なビジュアルおよびモーションチェックを追加します |
| [Open Lovable](https://github.com/firecrawl/open-lovable) | チャットアプリケーションと Firecrawl を使ってWebサイトを React アプリとして再現 | アプリ生成体験に重点を置きます。`ui-clone-skills` は、エージェントパイプラインのフォレンジック成果物と計測したパリティに重点を置きます |
| [GoClone](https://github.com/goclone-dev/goclone) | HTML、CSS、JavaScript、画像、リンクをダウンロードし、閲覧可能な静的ミラーを作成 | オフライン閲覧用にサイトファイルを保持します。`ui-clone-skills` は React + Tailwind 実装を生成し、レスポンシブ動作とインタラクションを検証します |

JavaScript バンドル内に隠れたアニメーションパラメータが重要な場合、既存実装を監査する場合、またはビルドと目視確認ではなく再現可能なゲートで完了を証明する必要がある場合は、`ui-clone-skills` を選択してください。

<a id="what-it-recovers"></a>

## 復元できるもの

- **実際のビジュアル値：** タイポグラフィ、余白、色、境界線、トランスフォーム、ブレークポイント、CSS カスタムプロパティ、元のクラス名
- **レスポンシブ構造：** ビューポートに応じたレイアウト、流動的な `vw`/`rem` の挙動、スティッキー配置、グリッド配置、モバイルでのリフロー
- **モーションパラメータ：** GSAP と ScrollTrigger のタイムライン、Framer Motion のスプリング、anime.js のタイミング、Webflow IX2 インタラクション、Lenis と Locomotive のスクロール設定、CSS キーフレーム、Web Animations API の状態
- **インタラクティブな状態：** スクロール表示とスクラブ、ホバーとクリックのトランジション、プリローダー、ページトランジション、スライダー、タブ、メニュー、時間指定シーケンス
- **メディアとシーン：** 画像、フォント、動画、Lottie、Rive、Spline、canvas、WebGL の参照と、取得可能な場合は再生またはインタラクションの証拠
- **難読化された出力：** Tailwind、CSS Modules、CSS-in-JS、圧縮済みバンドルによって記述時の値が隠れている場合の計算済みスタイル抽出

抽出エンジンは、とりわけ `transition-spec.json` などの共有アーティファクトを書き出します。これにより実装と検証は、それぞれ独立して推測するのではなく、観測済みの同じ契約を利用します。

## 失敗を検出できる検証

ビルド成功、HTTP 200、ページタイトルの一致、説得力のあるスクリーンショットだけでは完了ではありません。パイプラインは、ページに適した証拠を使ってレンダリング結果を検証します。

- レイアウトの健全性と DOM／セクション構造
- テキスト、フォント、表示アセット、レスポンシブの一致
- 絶対誤差（AE）、SSIM、セクション単位のビジュアル比較
- スクロール終端、表示トリガー、ホバー、クリック、トランジション状態の比較
- 包括的検証における 60 fps のフレーム単位モーション比較
- 抽出済みモーション項目と実装フックの静的カバレッジ

高速に反復する場合は、`quick` または `standard` 検証ティアを利用できます。デフォルトの `comprehensive` ティアでは、ブラウザとモーションのチェック一式が維持されます。

通常の比較では、すべてのスクリーンショットをモデルに判定させるのではなく、決定論的なスクリプトを使用します。Vision は最終的なセマンティックレビューと、メトリクスだけでは差異を説明できない場合の範囲を限定した診断にのみ使用します。

<a id="skills"></a>

## Skills

| やりたいこと | 使用するもの | 得られる結果 |
| --- | --- | --- |
| 公開サイトを再現する、または実行を再開する | **`ui-reverse-engineering`** | キャプチャ、抽出、生成、検証を、証拠に基づいて振り分けるWebサイトから React へのパイプライン |
| リファレンスの挙動をキャプチャする | **`ui-capture`** | スクリーンショット、スクロール、ホバー、クリック、トランジション、および任意の実装証拠 |
| クローンが異なる理由を診断する | **`visual-debug`** | 具体的な修正を伴う AE/SSIM、計算済みスタイル、構造、モーションの所見 |

デフォルトの入口には `ui-reverse-engineering` を使います。新しいリファレンス証拠だけが必要な場合は `ui-capture` を直接呼び出します。リファレンスと実装のアーティファクトがすでに存在し、差異の説明が目的なら `visual-debug` を呼び出します。

Claude Code と Codex は、同じ三つの公開スキルを提供します。ホストアダプターは、同じスクリプト、ゲート、アーティファクト、フックの挙動を共有します。

## 適した用途

| 入力元 | 最適な選択肢 |
| --- | --- |
| 実際の CSS、アセット、レスポンシブ挙動、モーションを備えた **公開URL** | **`ui-clone-skills`** |
| **Figma ファイル** | Builder.io、Anima、Plasmic、または別の Figma 実装ツール |
| **スクリーンショットのみ** | screenshot-to-code や v0 などのスクリーンショットからコードを生成するツール |
| **テキストによる説明のみ** | v0、Lovable、Bolt などのデザインジェネレーター |
| **静的ミラー**だけが必要な公開URL | `wget --mirror` または HTTrack |

新しいデザインの創作、アクセス制御の回避、許可のない第三者の保護されたデザインの公開には使用しないでください。実際のブラウザからページにアクセスでき、学習、プロトタイピング、内部ツール、または再現を許可されたサイトの再構築を目的とする場合に最も効果を発揮します。

## インストール

インストーラーを一度実行します。`PATH` 上で見つかった、対応するすべてのホスト CLI にプラグインが登録されます。

```bash
tmp=$(mktemp) && curl -LsSf -o "$tmp" https://raw.githubusercontent.com/voidmatcha/ui-clone-skills/main/install.sh && bash "$tmp" && rm -f "$tmp"
```

ホストを一つに限定するには `--claude-only` または `--codex-only` を使います。Claude Code にはプラグインとライフサイクルフックが導入されます。Codex には三つの公開スキルが導入され、ワークスペースで `ui-reverse-engineering` を初めて実行したときにプロジェクトローカルのフックが有効になります。

チェックアウトからのインストール、依存関係の手動セットアップ、ホスト固有のフラグ、スキルのみを導入する方法については、[インストールガイド](./README_detail/install.md)を参照してください。

## 要件

**テスト済み環境：** macOS 14+（主要環境）、および Ubuntu 22.04+（ネイティブまたは WSL2 経由）。Windows ネイティブはサポートされません。

| 依存関係 | 用途 |
| --- | --- |
| `agent-browser` | ブラウザでのキャプチャ、抽出、インタラクション比較 |
| ImageMagick | AE ピクセル比較 |
| `dssim` | 構造的なビジュアル類似性 |
| `ffmpeg` | 動画キャプチャとフレーム抽出 |
| `uv` + Python 3.11+ | パイプライン状態、ゲート、フック、メトリクス |

## パイプラインの仕組み

1. デスクトップ、タブレット、モバイル、および関連するインタラクション状態で**リファレンスをキャプチャ**します。
2. ページから DOM、CSS、アセット、フォント、セクション、バンドル、ランタイムの証拠を**抽出**します。
3. トリガーと計測済みパラメータを備え、ソースに裏付けられたトランジション仕様へと**モーションを解析**します。
4. キャプチャした構造と値から**実装を生成**し、独自に再構築すると再現性が損なわれる場合はソース CSS を保持します。
5. 構造、ビジュアル、レスポンシブ、モーションの各ゲートで**レンダリング結果を検証**します。
6. **計測された差異に基づいて反復**し、要求された完了条件を満たすか、実際のブロッカーが報告されるまで停止しません。

チェックアウトでは、`python -m ui_clone.pipeline live_url component_name session_name status --json` または `node bin/ui-clone pipeline live_url component_name session_name status --json` で状態を確認できます。npm への公開は一時停止しているため、`ui-clone-cli` がこのリポジトリに npm link されている場合を除き、チェックアウト内のコマンドを使用してください。

## ドキュメント

三つのルーティングスキルはコンパクトに保たれ、各パイプラインステップで必要になったときにだけ、対象を絞った51個のサブドキュメントを読み込みます。まずタスクレベルのページから始め、正確なコマンドやゲートの挙動が必要になった時点で運用契約を開いてください。

- [インストールとホストのセットアップ](./README_detail/install.md)
- [完全なリバースエンジニアリング・パイプライン](./README_detail/ui-reverse-engineering.md)
- [リファレンスとトランジションのキャプチャ](./README_detail/ui-capture.md)
- [ビジュアルとモーションのデバッグ](./README_detail/visual-debug.md)
- [パイプラインのフック、状態、ゲート](./README_detail/pipeline.md)
- [エージェントが読み取れる CLI 契約](./docs/agent-cli.md)
- [トークンとプロンプトキャッシュの管理](./README_detail/token-management.md)
- [セキュリティモデル](./README_detail/security.md)
- [責任ある利用](./README_detail/responsible-use.md)
- [FAQ とフレームワークのサポート](./README_detail/faq.md)

## 対象範囲

生成結果は本番運用を志向した React + Tailwind コードですが、複製した第三者のサイトがライセンス上問題なく、公開の準備が整っていることを自動的に保証するものではありません。動的または保護されたアセット、認証、ボット対策システム、ランダム化されたシーン、アクセスできないソースバンドルにより、抽出が制限される場合があります。パイプラインは、これらを一致したものとして暗黙に扱わず、欠落として記録します。

三つのスキルすべてに、[Agent Skills](https://agentskills.io/) 形式に準拠した eval フィクスチャが含まれています。リリース履歴は [CHANGELOG.md](./CHANGELOG.md) を参照してください。

## ライセンス

Apache-2.0。[LICENSE.txt](./LICENSE.txt) を参照してください。
