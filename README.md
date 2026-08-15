# claude-code-local

Claude Code を、ローカルの Ollama で動かすモデルに向けて起動するランチャ。

Ollama は Anthropic Messages API を実装しているので `ANTHROPIC_BASE_URL` を向けるだけ…
と思いきや、**多くの GGUF は chat template の都合でそのままでは 500 を返し続ける。**
この repo はその回避と、16GB VRAM 級で実用になる設定の実測をまとめたもの。

```
Claude Code --> normalize_proxy.py (11435) --> ollama (11434)
```

**プロキシは推論に関与しない。** メッセージ配列の形を直すだけ。

## なぜプロキシが要るのか

**Claude Code は `messages[1]` に role=system のメッセージを差し込む**（system-reminder の仕組み）。
一方 Qwen3.6 / Qwen3-Next 系の GGUF に入っている Jinja テンプレートは、
先頭以外の system を見ると例外を投げる:

```
Jinja Exception: System message must be at the beginning.
```

**結果、全リクエストが 500 になり Claude Code は一切動かない。**

`normalize_proxy.py` は先頭以外の system を user ターンに畳み、隣り合う同 role を統合する。
これだけで通る。

### 切り分けの記録（外した仮説を残す）

最初 **「常用モデルに焼いてある SYSTEM が原因」と判断したが、外れた。**
SYSTEM 無しの派生を作っても同じ例外が出た。

`/v1/messages` を直接叩いた切り分けでは、以下は**全部通る**:

* system が文字列 / 配列 / `cache_control` 付き / 2ブロック
* system + tools、multi-turn、`tool_use` + `tool_result`

**通らないのは「messages の中に role=system がある」場合だけ。**
手で作った JSON では再現しにくく、**ロギングプロキシを挟んで
Claude Code の実リクエストを見るまで分からなかった。**
詰まったら推測せずプロキシを挟むこと。

## 必要なもの

* Windows + Git Bash（`ccl` は bash スクリプト）
* Ollama（Anthropic Messages API 対応版。0.32.7 で確認）
* Python 3（プロキシ用。標準ライブラリのみ）
* Claude Code

`OLLAMA_FLASH_ATTENTION=1` を入れておくこと。無いと同じ `num_ctx` で
アテンションの計算バッファが数GB増え、16GB のカードに収まらなくなる。

## 使い方

**Git Bash から `ccl` を使う。** カレントディレクトリがそのまま作業ディレクトリになる。

```bash
cd /path/to/project
/path/to/claude-code-local/ccl              # 対話
/path/to/claude-code-local/ccl -p "hello"   # 一発
```

PATH に通すなら `~/.bashrc` などに:

```bash
export PATH="$PATH:/path/to/claude-code-local"
```

**カレントディレクトリは重要。** Claude Code はそこから `./CLAUDE.md` と `./.claude/` を読む。
プロジェクトを読ませたいなら、そのディレクトリで起動すること
（ユーザーレベルの `~/.claude/CLAUDE.md` は cwd に関係なく常に読まれる）。

`start.bat` も置いてあるが、**ダブルクリック用**。cwd が意図しない場所になるので、
プロジェクトを読ませたいときは `ccl` を使う。

どちらも Ollama と正規化プロキシを必要なら起動してから `claude` を呼ぶ。

### 環境変数

| | 既定 | 意味 |
|---|---|---|
| `CCL_MODEL` | `hf.co/unsloth/Qwen3-Coder-Next-GGUF:UD-IQ2_M` | 使うモデル |
| `CCL_CONTEXT` | `131072` | `CLAUDE_CODE_MAX_CONTEXT_TOKENS` |
| `CCL_FIT_TARGET` | `384` | `LLAMA_ARG_FIT_TARGET`（MiB） |
| `CCL_PROXY_PORT` | `11435` | 正規化プロキシの待ち受け |
| `CCL_THINK` | 未設定（＝thinking を切る） | `1` にするとクライアント側の設定に任せる |
| `CCL_NO_CHECK` | 未設定 | `1` で起動時の fit チェックを飛ばす |
| `CCL_LOG` | 未設定 | 正規化の前後の構造をこのパスに書き出す |

**thinking は既定で切ってある。** 同じ質問で出力トークンが **136 → 2** になり、
**答えは変わらなかった**。毎ターン効くので効果は大きい。

### 起動時の fit チェック

`LLAMA_ARG_FIT_TARGET` は**サーバの起動時に読まれる**ので、既に動いている Ollama には
後から適用できない。`ccl` は既存サーバを再起動しない（他の用途で使っているかもしれないため）
代わりに、**モデルを読み込んで実際に載ったかを確認して報告する。**

```
[ccl] fit OK: 100% GPU at num_ctx 131072
```

溢れている場合:

```
[ccl] WARNING: only 89% on GPU -- the rest spills to CPU (~30% slower).
[ccl] fix: taskkill //F //IM llama-server.exe && taskkill //F //IM ollama.exe && ccl
```

モデルのロードはどのみち必要なので、この確認で余計に待つ時間はほぼ無い。

## Ollama の落とし方（順番がある）

**app → serve → llama-server の順に落とす。**

```bash
taskkill //F //IM "ollama app.exe"     # これを忘れると serve が蘇り続ける
taskkill //F //IM ollama.exe
taskkill //F //IM llama-server.exe     # 孤児として残り VRAM を掴む
nvidia-smi --query-gpu=memory.used --format=csv,noheader   # 500MiB 前後なら綺麗
```

**デスクトップアプリ（`ollama app.exe`）が `ollama serve` を監視していて、
kill するたびに自分の設定で再起動する。** この状態では `LLAMA_ARG_*` も
`OLLAMA_MODELS` も届かず、`/api/tags` が `{"models":[]}` を返すこともある。

**`ollama.exe` を落としても `llama-server.exe` は道連れにならない。**
孤児として残り VRAM を掴んだままになる。この状態で新しいサーバを起動すると、
`ollama ps` は `100% GPU` と表示するのに実際には VRAM が足りず、
**83 tok/s のはずが 7〜8 tok/s まで落ちる。**
さらに悪化すると `llama-server startup failed` や `GGML_ASSERT` のクラッシュになる。

**これを踏んで一度「`FIT_TARGET` は逆効果」という逆の結論を出した。**
掃除して測り直したら逆だった。

（Git Bash では `/F` がパスに化けるので **`//F` と2本にする**）

## 実測

**RTX 5060 Ti 16GB / PCIe 3.0 x8 / RAM 32GB / Ryzen 5 2600 / Ollama 0.32.7。
2026-08-14〜15。判断を左右するなら測り直すこと。**

### Claude Code の起動時消費は 31,577 トークン

system 3ブロック + tool 30個 + ユーザ発話の1往復目。
**`num_ctx` の下限はこれで決まる。** 40960 だと起動だけで 77% を使ってしまい、
「ファイルを1つ読んだら終わり」になる。

### 35B-A3B クラス（重み 12GB / IQ2_M）の `num_ctx` 上限

`OLLAMA_FLASH_ATTENTION=1` 前提。既定の fit マージンだと 102400 で頭打ちだが、
**`LLAMA_ARG_FIT_TARGET=384` にすると 163840 まで伸びる**。速度は落ちない。

| `num_ctx` | fit 既定 | `FIT_TARGET=384` |
|---|---|---|
| 40960 | 11.84GiB / 100% / 85.6 tok/s | — |
| 98304 | 12.98GiB / 100% / 86.4 tok/s | — |
| 102400 | **13.07GiB / 100% / 84.6 tok/s** ←既定の上限 | — |
| 106496 | 13.07GiB / **97%** / 72.9 tok/s | — |
| 131072 | 13.07GiB / 93% / 62.5 tok/s | 13.64GiB / 100% / 75.0 tok/s |
| **163840** | — | **14.30GiB / 100% / 86.3, 85.8 tok/s**（2回） |
| 180224 | — | 14.54GiB / **97%** / 77.2 tok/s |
| 262144 | — | 14.58GiB / 87% / 49.0 tok/s |

**マージンを削るとその分の安全余裕が無くなる。** 163840 で VRAM 残は 1GiB 弱なので、
**ブラウザの動画や他アプリが VRAM を取ると溢れる。** 専用に使うとき向けの設定。

KV の実測は 40960→98304 の増分から **約 21,300 bytes/token**。
公表値 20,480 とほぼ一致するので、他モデルの見積もりにも公表値を使ってよい。

### Qwen3-Coder-Next 80B-A3B（UD-IQ2_M / 25GB）を 16GB に載せる

重みが VRAM に収まらないので、**MoE の routed expert を CPU に逃がす。**
Ollama のヘルプには載っていないが、**llama.cpp の `LLAMA_ARG_*` は素通しで効く。**

`LLAMA_ARG_N_CPU_MOE` のスイープ（`num_ctx 32768`）:

| `n_cpu_moe` | `size_vram` | eval |
|---|---|---|
| 48 | 2.74GiB | 12.3 tok/s |
| 36 | 8.13GiB | 15.5 tok/s |
| 28 | 11.72GiB | 17.3 tok/s |
| 24 | 13.52GiB | 21.2 tok/s |
| **22** | 14.42GiB | **21.7 tok/s** ←最速 |
| 20 | 15.32GiB | **7.1 tok/s** ←物理 VRAM を超えて崩れる |

`num_ctx` を上げた場合:

| `num_ctx` | `n_cpu_moe` | `size_vram` | eval |
|---|---|---|---|
| 65536 | 22 | 15.20GiB | 22.6 tok/s |
| **131072** | **26** | **14.96GiB** | **20.5 tok/s** |
| 131072 | 24 | 15.86GiB | 8.3 tok/s ←崩れる |

**採用している設定:**

```bash
OLLAMA_CONTEXT_LENGTH=131072 LLAMA_ARG_FIT_TARGET=384 LLAMA_ARG_N_CPU_MOE=26 ollama serve
```

`ollama create` で `num_ctx` を焼く方法もあるが、**24GB のモデルでは10分以上かかって
終わらなかった**ので、サーバ側の `OLLAMA_CONTEXT_LENGTH` で指定している。

**`ollama ps` の PROCESSOR 列は `n-cpu-moe` を検知できない。**
expert を CPU に逃がしても `100% GPU` と表示される。逃がした分は割合ではなく
`size` 自体が縮む形で現れるので、**判定は `/api/ps` の `size` を見ること。**

### エージェントとしての挙動（Qwen3-Coder-Next / IQ2_M、5タスク）

| タスク | 結果 | 所要 |
|---|---|---|
| ディレクトリから TODO 行を探して一覧化 | **完全正解** | 141s |
| fizzbuzz.py を作る → 実行 → 出力を見せる | **完全成功** | 394s |
| バグを直す → テストで確認 | **修正は正しい**。ただし報告が読めない | 452s |
| HTML を作る → ブラウザで開く | HTML は正しい。**ブラウザは開かず案内で終了** | 363s |
| ファイルを作る | 成功 | 165s |

**弱点は2つ:**

1. **最後の一歩を省くことがある**（ただし fizzbuzz は自分で実行しているので、
   一般的な消極性ではなく特定の動作の話）
2. **報告が下手。** タスクは成功しているのに最終メッセージが読めないことがある

**「散文の報告を読まず、機械的に検証する」**のが実用上の作法。
「できました」は当てにならないが、結果は正しいことが多い。

## その他の罠

### `.bat` の中で `timeout` を使わない

* **Git Bash の PATH を継承していると coreutils の `timeout` に食われる**
  （`timeout: invalid time interval '/t'`）
* フルパスで `timeout.exe` を呼んでも、**stdin がリダイレクトされていると
  「入力のリダイレクトはサポートされていません」で落ちる**

`ping -n 2 127.0.0.1 >nul` を使う。

### `.bat` に引数を渡すときのクォート

**これで一度「モデルが指示に従わない」と誤診した。**

```bash
cmd //c "/path/to/start.bat -p \"What is 2+2?\""   # 駄目
```

とすると、**バックスラッシュ付きの `\"` がそのまま bat に渡り**、
`claude` は `-p \"What` を受け取る。モデルは壊れたプロンプトに答えていただけだった。

```bash
cmd //c "/path/to/start.bat" -p "What is 2+2?"     # 正しい
```

**bat のパスだけを cmd に渡し、引数は別々に渡す。**

### tool call を試すときプロンプトにツール名を書かない

「read_file ツールを使え」と書くと、モデルが**ツールを呼ばずに
「使います」と喋って終わる**（`stop_reason: end_turn`）。
中立に「このファイルの中身は？」と聞けば正しく呼ぶ。
**モデルの不具合ではなく聞き方の問題。** 最初これで誤診した。

### Claude Code はこのモデルを知らない

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` を渡さないと **200k と仮定して
auto-compact のタイミングを誤る**。`ccl` / `start.bat` が設定済み。

`ANTHROPIC_SMALL_FAST_MODEL` と `ANTHROPIC_DEFAULT_HAIKU_MODEL` の
**どちらを現行 Claude Code が見るかは未検証**なので、両方に同じ値を入れてある。

#### `[claude-code:unrecognized_model]` は無視してよい

`-p` で実行すると stderr にこの行が出る。**print モード専用の診断行**で、
Claude Code 2.1.233 で追加されたもの。**対話モードでは出ないし、実害も無い。**

CHANGELOG は `modelOverrides` で消せると書いているが、**この構成では使わない。**
`modelOverrides` のキーに使えるのは Anthropic のモデル ID だけで（2.1.222 で明記）、
使うと **context window もそのモデルのもの（200k）として扱われる**。
`CLAUDE_CODE_MAX_CONTEXT_TOKENS` で正しい値を渡している今の形のほうが正確。

**診断行を消すために context の正しさを捨てる取引になるので、消さない。**

## デバッグ

`CCL_LOG` にパスを入れると、正規化の前後のメッセージ構造を書き出す。

```
before: user[text+text] system[str] assistant[tool_use] user[tool_result(165)] system[text]
after:  user[text+text+text] assistant[tool_use] user[text+tool_result(165)]
```

**記録するのは role とブロック種別だけで、中身は書かない**（ソースコードが入るため）。

## ライセンス

MIT
