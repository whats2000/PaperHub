<div align="center">

<img src="frontend/public/favicon.svg" alt="PaperHub logo" width="72" height="72" />

# PaperHub

**引用したすべての文がその出典までたどれる、論文を理解するチャットクライアント。**

マルチエージェントによるツールルーティング · リポジトリ内蔵の RAG ナレッジベース · 論文ごとのエージェント型検索 · すべての `[chunk]` を論文中の正確な一節へ結びつける Citation Canvas · 分離・編集が可能な発表者ノートを備えた、学会水準の Beamer スライドパイプライン。

![Python](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)
![TypeScript](https://img.shields.io/badge/TypeScript-React%2019-3178C6?logo=typescript&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-LangGraph-009688?logo=fastapi&logoColor=white)
![Vite](https://img.shields.io/badge/Vite-Tailwind-646CFF?logo=vite&logoColor=white)
![Lint](https://img.shields.io/badge/lint-ruff-261230?logo=ruff&logoColor=white)
![Types](https://img.shields.io/badge/types-mypy%20--strict-2A6DB2)
![Tests](https://img.shields.io/badge/tests-1297%20backend%20%2B%20541%20frontend-brightgreen)
![Status](https://img.shields.io/badge/release-v2.37.1%20(SRS%20v2.37.1)-success)
![License](https://img.shields.io/badge/license-Apache%202.0-blue)

[English](README.md) · **日本語** · [繁體中文](README.zh-TW.md) · [简体中文](README.zh-CN.md)

</div>

---

PaperHub は **UX ファースト**で設計されています。検索された各チャンクにはクリック可能な出典トレイルがあり、生成の各ステップは監査用の行を記録し、すべてのチャットターンは SQLite だけから再構築できます。単一のチャットインターフェースが、各ターンを適切な専門エージェント — 論文検索、論文 Q&A、自然言語→SQL のライブラリ統計、メモリ管理、スライド生成 — へとルーティングします。

## ✨ できること

🔎 エージェント検索 · 🧷 Citation Canvas · 🌍 あなたの言語 · 📊 ライブラリ統計 · 🧠 メモリ · 🧭 ルーティング + トレース · 🌐 発見 · 📎 論文を持ち込み · 🖼️ Beamer スライド · 🔱 フォーク & 巻き戻し · ➗ 数式 · 💾 どのデバイスでも · 🔌 MCP ネイティブ

<details>
<summary><b>各項目の説明 →</b></summary>

<br>

- **🔎 エージェント型検索。** 論文ごとのサブエージェントがセクション目次に沿って読み解き(やみくもなトップ k ではありません)、フラッグシップモデルが複数論文を横断して統合します。
- **🧷 Citation Canvas。** インラインの `[chunk:N]` マーカーが正確な一節へリンク — クリックでレンダリング済み HTML *と* 元 PDF の両方をハイライトします。
- **🌍 あなたの言語。** 中国語で尋ねれば中国語で返答 — 引用は保持され、記憶された「常に X 言語で返答」がターンごとの判定を上書きします。
- **📊 ライブラリ統計。** 「論文は何件?」 → テーブル許可リストに対し読み取り専用 SQL を実行し、数値 *と* 実行した SQL を添えて回答します。
- **🧠 メモリ。** 事実や設定はチャットごと、あるいはどこでも保持 — 安全ゲート、LLM の矛盾**置き換え**、編集・(無効/有効)化できる管理パネルを備えます。
- **🧭 可視化されたルーティング + トレース。** バッジが各ターンの担当エージェント + モデルを表示し、トレースパネルが全ステップを SQLite から再生します。
- **🌐 発見。** `paper_search` は曖昧な参照(「みんなが引用するあの拡散モデルの論文」)でも Web + Semantic Scholar で解決します。
- **📎 自分の論文を持ち込み。** arXiv ID、URL、PDF で添付 — 重複排除 + キャッシュされ、バックグラウンドの **Marker** ワーカーが PDF を実際の図・キャプション・数式→LaTeX へアップグレードします。
- **🖼️ 学会水準のスライド。** **存在しない図を決して引用しない**、根拠に基づく **Beamer デッキ**。任意指定・任意言語の発表者ノート、チャットで **1 枚を差分編集**、**画面上のスライド** について尋ねてもデッキは変更しません。
- **🔱 フォーク & 巻き戻し。** 過去の任意のメッセージから新しいチャットを分岐 — プレフィル済み・編集可能・自動送信なし。フォークは参照、メモリ、デッキを引き継ぎ、**親の下にネスト**します。
- **➗ 数式をレンダリング。** LaTeX(`$…$`、`$$…$$`)は KaTeX で実際の数式としてレンダリングされます。
- **💾 どのデバイスでも。** セッションと完全な記録はブラウザではなくバックエンドに保存。チャットを削除するとどこからでも削除されます(取り消し可能)。
- **🔌 MCP ネイティブ。** エージェントのツールは MCP(`/mcp`)経由で提供され、外部クライアント(Claude Desktop、Cursor)も同じ面に到達できます。

</details>

---

## 📸 スクリーンショット

**根拠に基づく回答 — すべての主張は出典までたどれます。**

| Citation Canvas → レンダリングされた HTML | Citation Canvas → 元の PDF |
| :---: | :---: |
| [![Citation Canvas highlighting a cited chunk in the rendered paper HTML](docs/screenshots/04-citation-canvas-html.png)](docs/screenshots/04-citation-canvas-html.png) | [![Same citation highlighted in the source PDF via a geometry overlay](docs/screenshots/05-citation-canvas-pdf.png)](docs/screenshots/05-citation-canvas-pdf.png) |
| 任意の `[chunk]` をクリック → LaTeX でレンダリングされた HTML 内の正確な一節へスクロールしてハイライトします。 | …そして元の PDF 内の同じ一節も。根拠のない主張はありません。 |

**学会水準のスライド — 分離された任意指定のノート。**

| 生成(スライドのみ) | リクエストに応じて追加された発表者ノート |
| :---: | :---: |
| [![Deck chip with a Generate-notes button and the Slides panel open](docs/screenshots/11-slides-generate.png)](docs/screenshots/11-slides-generate.png) | [![Slides panel with the speaker-note pane filled in](docs/screenshots/12-slides-notes-added.png)](docs/screenshots/12-slides-notes-added.png) |
| 実際の図を備えた Beamer デッキ(でっち上げの図はなし)— まずスライドのみ、ノートなし。 | ノートは任意指定のフォローアップで、(任意の言語で)個別に作成されます。 |

**ライブラリインテリジェンス + メモリ。**

| 自然言語→SQL のライブラリ統計 | セッション + グローバルのメモリ |
| :---: | :---: |
| [![A stats answer showing the numbers and the read-only SQL it ran](docs/screenshots/09-library-stats-sql.png)](docs/screenshots/09-library-stats-sql.png) | [![Memory Manager with session/global groups and supersede badges](docs/screenshots/10-memory-manager.png)](docs/screenshots/10-memory-manager.png) |
| 「論文は何件ありますか?」 → 数値 **と** 実行した正確な SQL を添えて回答。 | 安全ゲート + 矛盾の置き換え履歴を備えた、記憶された事実や設定。 |

**ルーティング + 可観測性。**

| ルーティングバッジ | トレースパネル(再生可能な DAG) |
| :---: | :---: |
| [![A chat turn tagged with the routing badge showing intent and model](docs/screenshots/02-routing-badge.png)](docs/screenshots/02-routing-badge.png) | [![Expanded trace panel listing each step with latency and status](docs/screenshots/03-trace-panel.png)](docs/screenshots/03-trace-panel.png) |
| 各ターンが、それを処理したエージェント + モデルを表示します。 | 各モデル/MCP/パイプラインのステップは監査行です — 完全な DAG が SQLite から再生されます。 |

**発見 + 自分の論文の持ち込み。**

| 論文検索カード | リファレンスソースドロワー |
| :---: | :---: |
| [![Paper-search result cards with Add-as-reference buttons](docs/screenshots/07-paper-search-cards.png)](docs/screenshots/07-paper-search-cards.png) | [![Reference Sources drawer listing the session's enabled papers](docs/screenshots/08-reference-sources.png)](docs/screenshots/08-reference-sources.png) |
| Web + Semantic Scholar による発見。エージェントが最良の候補を自動追加します。 | 論文ごとに有効化/削除できる、セッションスコープの参照セット。 |

<details>
<summary>さらに — アプリの概要とあなたの言語での回答</summary>

| シェル | あなたの言語での回答 |
| :---: | :---: |
| [![Full PaperHub window: sidebar, chat, composer](docs/screenshots/01-app-overview.png)](docs/screenshots/01-app-overview.png) | [![A Chinese question answered in Chinese with citation markers preserved](docs/screenshots/06-language-adherence.png)](docs/screenshots/06-language-adherence.png) |
| 単一のチャットシェル。すべてのターンが専門エージェントへルーティングされます。 | 任意の言語で尋ねれば、回答もそれに従い、引用は保持されます。 |

</details>

---

## 🧱 技術スタック

| 領域 | 採用技術 |
| --- | --- |
| **バックエンド** | Python 3.11 · FastAPI · LangGraph · LiteLLM · SQLite(`aiosqlite`)· Pydantic v2 |
| **フロントエンド** | TypeScript · React 19 · Vite · Tailwind · Zustand · `react-markdown` + KaTeX |
| **検索** | SQLite の `chunks` テーブル — `list_sections`/`read_section` によるエージェント型セクションナビゲーション(ベクトルストアなし) |
| **スライド** | Beamer + `pdflatex`(`metropolis` テーマ)· docker-compose サービスとしての `datalab-to/marker` PDF 取り込み(任意、GPU 対応) |
| **LLM** | デフォルトで Gemini(任意の LiteLLM プロバイダー — 軽量ティアのサブエージェント、フラッグシップのファイナライザー) |
| **ツール** | `uv` · `pytest` · `ruff` · `mypy --strict` · Vitest · ESLint · Conventional Commits |

> [!NOTE]
> ローカル専用・シングルユーザー。認証面はありません — 自分の LLM キーを指定し、自分のマシンで実行してください。

---

## 🚀 クイックスタート

### 🐳 Docker で実行(推奨 — とにかくアプリを使う)

PaperHub を開発するのではなく*実行*したいだけなら、スタック全体がコンテナで動作します — **Python、Node、LaTeX のインストールは不要**です。必要なのは [Docker](https://docs.docker.com/get-docker/) と LLM キーだけです。1 回の `docker compose up` で 5 つのサービス(バックエンド、モデルサーバー、Marker PDF 取り込み、Web 検索、Web UI)がすべて立ち上がるので、スライド(**中国語/CJK** を含む)、RAG、Web 発見がそのまま動作します。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub
cp backend/.env.example backend/.env   # then fill in GEMINI_API_KEY (or your provider's key)

docker compose up -d --build           # CPU; first build downloads TeX Live + Marker weights (a few GB, once)
```

**http://localhost:8080** を開きます。

> [!NOTE]
> **GPU(任意、NVIDIA + [Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)):** Marker PDF 取り込みが高速になります。GPU オーバーライドを重ねてください:
> ```bash
> docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build
> ```

データは名前付きボリュームに永続化されます(`paperhub-workspace` = DB + キャッシュ、モデルの重み、Marker の重み)。`docker compose down` で停止し、`-v` を付ければデータも消去します。

---

### 🛠️ ソースから実行(開発向け)

**前提条件:** Python 3.11 + [`uv`](https://docs.astral.sh/uv/)、Node 18+、そして LLM API キー(デフォルトは Gemini)。**スライド生成**にはさらに `PATH` 上の LaTeX ディストリビューション(`pdflatex` — 例: `winget install MiKTeX.MiKTeX`)が必要です。これがなくても影響を受けるのは `slides` インテントだけです(「LaTeX ディストリビューションをインストールしてください」というメッセージを返します)。PDF の図/数式の抽出には、任意で Docker 化された `marker` サービス(`docker compose up -d marker`)を利用できます。

```bash
git clone https://github.com/whats2000/PaperHub.git
cd PaperHub

# Install both halves
cd backend && uv sync          # Python deps from uv.lock
cd ../frontend && npm install  # JS deps from package-lock.json
```

LLM キーを設定します:

```bash
cd backend
cp .env.example .env           # then fill in GEMINI_API_KEY (or your provider's key)
```

#### 開発スタックの実行

**推奨(Windows、ワンコマンド):** `scripts/start.ps1` がすべての関連プロセスを統括します — `paperhub-mcp-up` を介して外部の MCP デーモン(open-websearch)を立ち上げ、続いてホットリロード付きのバックエンドを起動します:

```powershell
# Terminal 1 — backend stack (MCP daemons + FastAPI on :8000)
cd backend
.\scripts\start.ps1
```

```bash
# Terminal 2 — frontend (Vite + React, hot-reload, :5173)
cd frontend
npm run dev
```

**http://localhost:5173** を開いてチャットを始めましょう。

<details>
<summary>低レベル: uvicorn を直接実行</summary>

```bash
cd backend
uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
```

注意: この方法では Web 検索デーモンは**起動されません**。Windows では `uvicorn --reload` が `SelectorEventLoop` 上で動作するため、ワーカー内の自動起動はグレースフルにフォールバックします(論文のみ)— `uv run paperhub-mcp-up` で Web 検索を自分で立ち上げてください(あるいは、それを行ってくれる `scripts/start.ps1` を使います)。[設定](#️-設定)の Web 検索に関する注記を参照してください。

</details>

> [!TIP]
> **API キーが手元にない?** モック化された LLM でチャットの配管を試せます(PowerShell):
> ```powershell
> $env:PAPERHUB_ROUTER_MOCK   = '{"intent":"chitchat","model_tier":"small","confidence":0.9,"reasoning":"dev"}'
> $env:PAPERHUB_CHITCHAT_MOCK = "Hello from PaperHub!"
> uv run uvicorn paperhub.app:app --reload --reload-dir src --port 8000
> ```

---

## ⚙️ 設定

すべての設定は `backend/.env` にあります([`.env.example`](backend/.env.example) で機能ごとにグループ化されています)。よく触れることになるのは次のものです:

| 変数 | 用途 | デフォルト |
| --- | --- | --- |
| `GEMINI_API_KEY` | LLM プロバイダーの認証情報(または `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`) | — |
| `PAPERHUB_PAPER_QA_MODEL` | フラッグシップのファイナライザー(複数論文の横断統合) | `gemini/gemini-2.5-pro` |
| `PAPERHUB_PAPER_QA_SUBAGENT_MODEL` | 論文ごとのセクションナビゲーター(軽量) | `gemini/gemini-3.1-flash-lite` |
| `PAPERHUB_SEMANTIC_SCHOLAR_API_KEY` | Semantic Scholar のレート上限を引き上げ(任意) | — |

**Web 検索による発見(任意)。** `paper_search` / `paper_suggest` は、[`open-websearch`](https://www.npmjs.com/package/open-websearch) デーモンが `:3000` で到達可能なとき、キー不要のマルチエンジン発見ステップを獲得します。手動でインストールする必要はありません — `scripts/start.ps1`(または `uv run paperhub-mcp-up`)が `mcp_servers.toml` を読み取り、`launch` を宣言するすべての MCP サーバーを `npx -y` 経由で起動します。これは初回実行時にパッケージを取得します(約 25 秒、1 回限り):

```bash
cd backend
uv run paperhub-mcp-up          # launches open-websearch on :3000 (skips if already up)
```

立ち上がると、バックエンドの MCP レジストリが `web.search` / `web.fetch` を自動公開します。停止しているときは、エージェントは論文のみのフローにフォールバックします — 設定不要です。起動されたデーモンはデタッチされるためバックエンドの `--reload` を生き延びます。明示的な終了は `start.ps1` の役割です(さもなければ再起動時にクリアされます)。`PATH` 上に Node 18+ が必要です。(`paperhub-papers` MCP の面はインプロセスで `/mcp` に提供され、インストールは不要です。)

---

## 🗺️ アーキテクチャ(1 画面)

```
┌─────────────────┐       SSE      ┌───────────────────────────────────────────┐
│  React shell    │ ◄───────────── │ FastAPI · POST /chat                      │
│  - Composer     │                │  ┌─────────────────────────────────────┐  │
│  - Routing badge│                │  │ LangGraph turn                      │  │
│  - Trace panel  │                │  │  Router ─► chitchat | paper_qa |    │  │
│  - Citation     │                │  │           paper_search | slides |   │  │
│    Canvas       │                │  │           library_stats             │  │
└─────────────────┘                │  └─────────────────────────────────────┘  │
                                   │     │                                     │
                                   │     ▼  paper_qa: fan out one subagent     │
                                   │        per paper → section nav →          │
                                   │        flagship finalizer over raw chunks │
                                   │  ┌─────────┐ ┌────────────────────────┐   │
                                   │  │ LiteLLM │ │ SQLite (chunks + audit │   │
                                   │  │ adapter │ │ + schema)              │   │
                                   │  └─────────┘ └────────────────────────┘   │
                                   └───────────────────────────────────────────┘
```

すべてのモデル呼び出し、MCP 呼び出し、パイプラインのステップは、戻る前に `tool_calls` の行を書き込みます — `SELECT * FROM tool_calls WHERE run_id = ?` だけから完全なエージェントコンテキストを再構築できるだけの状態です。論文コンテンツは**重複排除**されます: いくつのセッションが参照していても、一意な論文ごとに 1 つの `paper_content` 行 + 1 つのキャッシュディレクトリ + 1 セットのチャンクです。

完全なアーキテクチャは [SRS](docs/superpowers/specs/2026-05-17-paperhub-srs.md) にあります。

---

## 📍 ステータス

| プラン | スコープ | 状態 |
| --- | --- | --- |
| **A** | バックエンド基盤 + ルーターのみのチャット | ✅ 完了 |
| **B** | フロントエンド基盤(React シェル、SSE、ルーティングバッジ、トレースパネル) | ✅ 完了 |
| **C** | 論文パイプライン + リサーチエージェント(取り込み、paper_search、エージェント型 paper_qa、MCP レイヤー、PDF アップロード) | ✅ 完了 — マージ済み(SRS v2.10) |
| **D** | 検索結果 + リファレンスソース + Citation Canvas(HTML + PDF の一節ハイライト) | ✅ 完了 — マージ済み(SRS v2.13) |
| **E** | SQL エージェント + `library_stats`(sqlite MCP)+ セッション/グローバルのメモリ管理(ゲート、矛盾の置き換え、メモリマネージャー UI) | ✅ 完了 — マージ済み(SRS v2.17) |
| **F** | スライドパイプライン + レポートエージェント — Marker 取り込み(F2/F2.1)、博士水準のスライドエージェント(F3)、分離された任意指定ノート + 差分編集 + 長さ予算(F4)、学会水準のメタデータ付きタイトルページ + タイトル/スタイルのカスタマイズ(F4.2) | ✅ 完了 — マージ済み(SRS v2.22) |
| **F5** | スライドのプレゼンテーションモード(聴衆向けポップアップウィンドウ + `BroadcastChannel` 同期 + 発表者コックピット)+ 発表中の Q&A + コンポーザーの音声入力 | ✅ 完了 — マージ済み(SRS v2.26) |
| **G** | フロントエンド UI の国際化(i18n: `en` / `zh-TW` / `zh-CN` / `ja`)+ アカウントメニュー(言語/テーマ切り替え、About)+ DB バックエンドのランタイム設定パネル | ✅ 完了 — マージ済み(SRS v2.31) |
| **H** | 比較ビュー + filesystem / `paperhub.*` MCP | 🔜 計画中 |

各プランは単独で動作する、テスト可能なソフトウェアを提供します。プランは [`docs/superpowers/plans/`](docs/superpowers/plans/) にあります。

---

## 🧑‍💻 開発

PaperHub は仕様 → プラン → TDD の流れで構築され、サブエージェント主導の実装と、タスクごとの仕様適合 + コード品質レビューを伴います。

**バックエンドのゲート**(`backend/` から):

```bash
uv run pytest          # 1104 tests, hermetic
uv run ruff check src tests
uv run mypy src        # --strict
```

**フロントエンドのゲート**(`frontend/` から):

```bash
npm test               # Vitest + RTL + MSW (386 tests)
npm run typecheck      # tsc --strict
npm run lint           # ESLint flat config
npm run build          # Vite production build
```

過去の任意のチャットターンを SQLite から**再生**します(エージェントフローのデバッグ):

```bash
cd backend
uv run paperhub-replay --run-id 1
```

**エンドツーエンドのベンチマーク** — `pytest` は配線を証明し、[`backend/benchmark/`](backend/benchmark/) ハーネスは*振る舞い*を証明します。**ライブ**のバックエンドをシミュレートされたユーザーとして駆動し(キャッシュ済みの論文を添付 → `/chat` 経由でプロンプトをルーティング)、根拠の証拠(引用されたチャンクのテキスト + エージェントトレース)を収集し、各ケースを正確性 + 根拠について **0/1** で採点します — 手動、あるいは **LLM-as-Judge**(固定温度、厳密な根拠)によって。ケースは設定駆動(TOML)なので、自分で書くこともできます:

```bash
# with the backend running (scripts/start.ps1), from backend/:
scripts/run-benchmark.ps1 -Judge            # 20-case eval (16 paper_qa + 4 slides) + LLM judge
scripts/run-benchmark.ps1 -Resume <prior.json>   # retry only failed cases after a drop
```

> 貢献する AI エージェントの方へ: まず [CLAUDE.md](CLAUDE.md) を読んでください — 規約、fix-now ポリシー、エージェントフローの可観測性ルールが記載されています。

---

## 📂 リポジトリ構成

```
.
├── backend/
│   ├── src/paperhub/         # FastAPI app · agents · pipelines · mcp · tracer
│   ├── tests/                # pytest suite (1104 tests, hermetic)
│   ├── benchmark/            # config-driven real-API e2e benchmark + LLM-as-Judge
│   └── pyproject.toml        # uv project · mypy --strict · ruff
├── frontend/                 # React 19 + Vite + Tailwind + Zustand
├── docs/superpowers/
│   ├── specs/                # SRS — authoritative architecture document
│   └── plans/                # implementation plans, one per sub-project
├── reference/                # copied source from paper2slides-plus + Intro2GenAI-hw1
├── CLAUDE.md                 # AI-agent orientation for this repo
└── README.md
```

`workspace/`(gitignore 対象)はランタイムの状態 — SQLite データベースと論文キャッシュ — を保持します。

---

## 📖 ドキュメント

- **[システム要求仕様書(SRS)](docs/superpowers/specs/2026-05-17-paperhub-srs.md)** — 信頼できるアーキテクチャ、スキーマ、スコープ、受け入れ基準(**v2.37.1** まで提供)。
- **[実装プラン](docs/superpowers/plans/)** — サブプロジェクトごとに 1 つ、それぞれ TDD で実行。
- **[バックエンド開発者向けドキュメント](backend/README.md)** — バックエンド固有の注記。

---

## 📚 引用

研究で PaperHub を使用したり、それを基に何かを構築したりする場合は、次のように引用してください:

```bibtex
@software{paperhub,
  author  = {Ren-Di, Wu},
  title   = {{PaperHub: A Provenance-First Multi-Agent Research Assistant for Grounded Paper Q\&A and Slide Generation}},
  year    = {2026},
  url     = {https://github.com/whats2000/PaperHub},
  version = {2.37.1}
}
```

---

## 📄 ライセンス

[Apache License 2.0](LICENSE) — © PaperHub contributors. 本ソフトウェアは、貢献者からの明示的な特許権の付与を含むライセンスの条項のもとで、使用、改変、配布することができます。
