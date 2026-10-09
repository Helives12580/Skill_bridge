# skill-bridge — let ComfyUI pull AI-skill rules at runtime

A small local service that feeds **AI skill rules** into ComfyUI's LLM nodes dynamically.
You type one short scene declaration (`krea2`), and the bridge reads the matching
rules off disk and injects them.

**Switching base models means changing that one line — the rules themselves don't move.**

> 🚀 **Using DSH?** You don't have to set this up by hand. Give your DSH agent the full path of
> `deploy-with-dsh.md` and say "install this for me" — it will probe your environment, find a
> usable LLM endpoint, write the config, and run a live test before reporting back.

> 中文说明见 [README.md](README.md)。

---

## How it runs inside ComfyUI

### Plugins used

| Plugin | Required? | What it provides | Project |
|---|---|---|---|
| **ComfyUI-ZML-Image** | **Required** | The whole LLM node set: `模型加载器V2` (Model Loader, reads API config), `系统提示词` (System Prompt — **where the scene declaration goes**), `参数设置` (Parameters — temperature, max tokens), `对话主程序` (Chat — sends the request), `过滤思考` (Thought Filter — strips the reasoning block). Built on the official `openai` SDK, so **any OpenAI-compatible endpoint works** | <https://github.com/zml-w/ComfyUI-ZML-Image> |
| **comfyui-anima-validate-node** | Optional | `Anima 提示词校验` node: deterministic tag validation (normalization / hypernym folding / slot conflicts / length) plus refusal detection | This repo, `plugins/` |
| **[ComfyUI-NL-PromptForge](https://github.com/Helives12580/ComfyUI-NL-PromptForge)** | Optional | `NL Prompt Forge 黑名单过滤` node: catches stray code fences. The validate node's **refusal-detection heuristic** (dual-keyword table + punctuation folding) is modelled on this plugin | <https://github.com/Helives12580/ComfyUI-NL-PromptForge> |

> **This repo ships no skill content.** The rules come from the `anima-tagger` and
> `anima-n-prompt` skills, which you provide yourself — see "Which skills are used".

### The pipeline

```
   LoadImage / DanbooruGallery          <- image (optional; scale long edge to 1024~1536 first)
          |
          v
   BSK_Tagger (local wd14 tagging)      <- runs locally, costs no tokens
          |
          v
   AnimaTKDanbooruTagGetter             <- category filtering
          |
          v
   DanbooruTagSorterNode / TK String Router / PromptCleaner
          |
          v
   ZML_LLM 对话主程序 . user_input ------+
   ZML_LLM 对话主程序 . input_image -----+  image + text go into one user message
                                         |  (OpenAI multimodal format, base64)
   ZML_LLM 模型加载器V2 -- . model_config+
   ZML_LLM 系统提示词   -- . system_prompt  <- one scene declaration only: krea2
   ZML_LLM 参数设置     -- . params         +- max tokens = 8192
          |
          |  request goes to http://127.0.0.1:8899/v1
          v
   +--------------------------------------------------+
   |  skill-bridge (this repo)                        |
   |  1. pick skill + reference docs from the         |
   |     scene declaration                            |
   |  2. assemble a ~21k-char system prompt           |
   |     (rules + pipeline overrides)                 |
   |  3. on a cache hit, replay and never touch       |
   |     the upstream                                 |
   |  4. otherwise forward to the real LLM endpoint   |
   +--------------------------------------------------+
          |
          v
      model returns the prompt body (three-part long form)
          |
          v
   ZML_LLM 过滤思考                     <- strips the reasoning block
          |
          v
    Anima 提示词校验                    <- node-side safety net: validate + refusal check
          |
          v
   CLIPTextEncode -> KSampler . positive -> ControlNet img2img
```

### Why this layer exists

ZML's `系统提示词` (System Prompt) node is **static**: whatever rules you paste in, it sends
that same block forever. That gets painful when you switch base models — krea2 wants a
three-part long form, anima wants a short tag stream, minimax wants something else again,
and each switch means hand-copying dozens of rules.

The bridge keeps the rules in skill files and **reads them at request time**. You write
`krea2` in the System Prompt node and the bridge injects the matching rules, format spec
and reference vocabulary.

It also handles a few things on the side:

- **Switching base models is a one-line change**, rules untouched
- **Repeated requests during seed-hunting hit the cache**, instead of re-sending 21k chars per image
- **Content-policy refusals** are detected and replaced with a visible prompt card, instead of
  being fed to the text encoder as if they were a prompt
- **When an image is attached, the image leads the expansion**, so the prompt and your
  ControlNet reference stop fighting each other

---

## System prompt quick reference (bridge mode)

The `system_prompt` field takes **one short line**; the bridge fills in the rest:

| What you want | `system_prompt` |
|---|---|
| **Krea2 long-form expansion** (most common, tag style) | `krea2 扩写` |
| Krea2 with an image | `krea2 扩写`, and wire the image to `input_image` |
| **Krea2 natural language** (colossal / poster / random) | `krea2 megastructure`, `krea2 poster`, `krea2 random` |
| Krea2 natural language with an image | same declaration, wire the image to `input_image` (the image is a scale / style reference, never reverse-tagged) |
| Minimax | `minimax 扩写` |
| Anima scene | `anima 扩写` (two parts: tag stream ＋ natural language, hard 512-token cap) |
| SDXL scene | `sdxl 扩写` (same two-part shape) |
| Leave empty | falls back to the default route |

Matching is keyword-based on the declaration, longest match wins, so `krea2`, `krea2 expand`
or any sentence containing `krea2` all route the same way. **The keyword is the model name —
you don't need to write anything in Chinese.**

> **Write just that one word — do not paste the rules.** Pasting the full rules is what
> "direct mode" needs, which is a different setup; see
> `reference/krea2_pipeline_system_prompt.txt` for an example of that approach.

**Switching base models means changing only this line.** The rules live in skill files.

**To swap models on the fly**: add more presets to `zml_model_key.json` that point at the same
bridge URL but differ in the `model` field, then switch `preset_name` in the ComfyUI panel —
no config edit, no bridge restart.

---

## 1. What it solves

Local tagging and AI expansion are two different jobs:

- **wd14 tagging** is accurate, but it produces a tag string — no natural-language
  organization, no detail density.
- Natural-language long form needs an LLM, and every base model wants a different style.

If you paste the rules **statically into the workflow**, every base-model switch means
re-copying them by hand. This bridge keeps the rules in skill files, reads them at request
time, and leaves a single declaration in the workflow.

---

## 2. Dependencies

For the full plugin/node list, see "Plugins used" at the top.

### Runtime

- Python 3.8+ — the interpreter bundled with ComfyUI is fine. The bridge uses
  **only the standard library, zero dependencies**
- An OpenAI-compatible LLM endpoint (local gateway, self-hosted proxy, or any cloud API)
- The skills (`anima-tagger`, `anima-n-prompt`, `krea2-prompt`) — **not included in this repo**;
  they are third-party or local-only assets, fetch them yourself (section 7)

---

## 3. File layout

```
skill-bridge/
├─ README.md                      Chinese docs (this file's counterpart: README.en.md)
├─ README.en.md                   English docs
├─ deploy-with-dsh.zh.md                  DSH hand-off, Chinese
├─ deploy-with-dsh.md             DSH hand-off, English
├─ LICENSE                        MIT
├─ .gitignore
├─ bridge/
│  ├─ dsh_skill_bridge.py         the bridge itself (standard library only)
│  ├─ bridge_config.json          upstream URL, key, scene routes, shared override layer
│  ├─ start-bridge.bat            double-click to start (Windows)
│  ├─ add_krea2_routes.py         register / dry-run the three krea2-prompt routes (`--check`)
│  ├─ fix_anima_routes.py         attach the "two-part + 512-token cap" override to anima routes
│  └─ zml_model_key.json          ZML preset file (holds the API key)
├─ plugins/
│  └─ comfyui-anima-validate-node/  the "Anima 提示词校验" node
├─ updates/                       incremental patches (skip the full re-download)
│  └─ 2026-10-10-anima-krea2.md   anima two-part output + krea2 natural-language routes
└─ reference/                     for people who prefer not to use the bridge
   ├─ krea2_pipeline_system_prompt.txt  static rules for direct mode
   └─ krea2_wiring.md             Krea2 Control wiring reference (Chinese)
```

> ⚠️ **No skill content is bundled.** The rules come from `anima-tagger` and
> `anima-n-prompt`; fetch them yourself and point `skills_dir` at them (section 7).

---

## 4. Setup (three steps)

### Step 1 — put the skills in place

**This repo does not ship the skills.** Obtain `anima-tagger` and `anima-n-prompt` and put
them wherever you like. The conventional location is:

```
C:\Users\<you>\.dsh\skills\
```

Anywhere works — just point `skills_dir` at it in step 2.

### Step 2 — edit `bridge/bridge_config.json`

Three fields matter:

```json
{
  "upstream": {
    "base_url": "https://your-endpoint/v1",
    "api_key":  "REPLACE_ME",
    "model":    "your-default-model"
  },
  "skills_dir": "C:\\Users\\<you>\\.dsh\\skills"
}
```

Everything else is documented inline and works untouched.

### Step 3 — start it, then wire up ComfyUI

1. Double-click `bridge/start-bridge.bat`. When you see
   `[bridge] ready http://127.0.0.1:8899/v1`, it's up (**leave the window open**).
2. Put `zml_model_key.json` in any folder, fill in your key, and in ComfyUI:
   - `ZML_LLM 模型加载器V2` → `config_folder` = **that folder**
   - `preset_name` = **`SKILL-桥`**
3. Write a scene declaration in `ZML_LLM 系统提示词` (next section).

On startup the bridge prints a preflight table showing whether every route's skill files
were found — check that first if something misbehaves.

---

## 5. Wiring in ComfyUI

```
LoadImage / DanbooruGallery
   └─→ BSK_Tagger (local wd14 tagging)
         └─→ AnimaTKDanbooruTagGetter (category filter)
               └─→ DanbooruTagSorterNode / TK String Router
                     └─→ PromptCleaner
                           └─→ ZML_LLM 对话主程序 . user_input
                                 ↑
      ZML_LLM 模型加载器V2 ─ . model_config
      ZML_LLM 系统提示词   ─ . system_prompt      ← one word: krea2
      ZML_LLM 参数设置     ─ . params             ← max tokens = 8192
                                 │
                                 ↓ response text
                           ZML_LLM 过滤思考        (strips <think> blocks)
                                 ↓
                           NL Prompt Forge 黑名单过滤   (optional safety net)
                                 ↓
                           Anima 提示词校验          (optional, see below)
                                 ↓
                           CLIPTextEncode . text
                                 ↓
                           KSampler . positive
```

### When an image is involved

Wire the image to `ZML_LLM 对话主程序`'s `input_image` **and** the wd14 tag text at the same
time. The bridge decides the branch automatically:

| Input | Branch | Output shape |
|---|---|---|
| **image + tag** | expansion (tag = confirmed anchors, image = visual reference) | three-part long form |
| **image only, no tag** | reverse-tagging | tag stream + a few NL sentences |
| tag only | expansion | three-part long form |

### ⚠️ Scale the image before feeding it in

`ZML_LLM 对话主程序` **does no resizing at all** — it base64-encodes whatever you hand it.
Measured:

| | Source | What lands in the context |
|---|---|---|
| No scaling | 2112×3840 PNG, 9.3 MB | ~**12.5 MB** after base64 |
| Long edge 1024 | 563×1024 JPEG, 108 KB | ~**144 KB** after base64 (**87× smaller**) |

Without scaling, the request either fails outright or costs absurdly much — and it
**does not error**, it just gets slow and expensive. Worse, some upstreams **silently drop
the image**, so the model expands from the tag alone while you believe it is looking at the
picture.

**Fix**: put an `ImageScale` / `ImageScaleBy` node before `input_image`. **A long edge of
1024–1536 is plenty** — ControlNet needs no more, and the vision model can't read finer detail.

### With an image attached, the image is the reference

Once an image is wired in, the model writes the body **against the image**: action and pose,
body orientation and gaze, limb positions, framing and camera (shot size, angle, subject
coverage, foreground/background layering), scene props, light direction and shadow falloff.
The tag only pins down discrete features the image can't render precisely (hair colour, eye
colour, garment style, accessories).

**When tag and image disagree, the tag wins** — but nothing contradictory gets written.
In one measured run the tag said `outdoors` while the image had a white background; both
survived, exactly as the rule prescribes.

Both of these texts are editable in `bridge_config.json`
(`image_guidance_with_tags` / `image_guidance_image_only`) — tweak the wording without
touching Python.

### Two settings you must change

| Setting | Value | Why |
|---|---|---|
| `ZML_LLM 对话主程序` → `json_strategy` | **`仅提示词 (不强求)`** | Any other option appends "reply in JSON" to the system prompt and wrecks the long-form output |
| `ZML_LLM 参数设置` → `最大Token数` | **8192** | Reasoning models burn a lot of tokens thinking; too small a budget leaves the body empty |

---

## 6. Scene declarations

The `system_prompt` field takes **one line**. The bridge matches keywords, longest match wins:

| Declaration | Skill loaded | Suits |
|---|---|---|
| `krea2 扩写` | anima-tagger · expansion branch | krea2 tag-style long form |
| `krea2 megastructure` / `krea2 colossal` / `krea2 kaiju` / `krea2 cthulhu` | krea2-prompt · colossal set (6 files) | colossal subjects (photoreal, one English paragraph) |
| `krea2 poster` / `krea2 typography` | krea2-prompt · poster set (8 files) | poster-style graphic design from your text / image reference |
| `krea2 random` / `krea2 random poster` | krea2-prompt · random set (9 files) | no reference — the model invents the subject |
| `anima 扩写` | anima-n-prompt · tag-stream framework | anima scenes (two parts: tag stream ＋ natural language, hard 512-token cap) |
| `sdxl 扩写` | anima-n-prompt · tag-stream framework | sdxl scenes (same two-part shape) |
| `minimax 扩写` | minimax-h3-video-prompt | video prompts (bring that skill yourself) |
| empty | default route | — |

Chinese aliases work just as well: `krea2 巨构`, `krea2 图文海报`, `krea2 随机` route identically.
Matching is keyword-based with longest-alias-wins, so the three natural-language routes never
steal traffic from a plain `krea2`.

> **Input clamping**: the three natural-language routes carry their own pipeline override whose
> rule 0 says that any character / environment / action / event you supply **must** land in the
> prompt as the subject, background and event — the model may not swap in a subject of its own
> choosing.
>
> **Two anima budgets**: normally the two parts target ≤ 300 tokens. If your request asks for
> "vivid expression", "detailed scene", "full composition" or "rich scene detail", the cap is
> relaxed to ≤ 480 tokens so more expression / prop / environment tags can be pulled in. Both
> budgets sit far below the 512-token hard cap of anima 1.0 base.

Routing keys live in `bridge_config.json` under `routes`, so adding a base model means adding
one entry there — not touching the workflow.

---

## 7. Which skills are used

| Skill | Contents | Role here |
|---|---|---|
| **`anima-tagger`** | three branches (reverse-tagging / creation / **expansion**), with hard format rules, slot order, banned-term list | **main path**: tag → three-part long form; also does reverse-tagging when an image is attached |
| **`anima-n-prompt`** | full generation framework for Chinese scene descriptions → prompt (role / output protocol / mutual-exclusion table / slots / scene decision tree) | anima and sdxl scenes |
| **`krea2-prompt`** | krea2 natural-language rule library: colossal set (kaiju / Cthulhu / megastructure / organic colossus), poster set (graphic posters), random set — 23 rule files | krea2 natural-language scenes (`krea2 megastructure` etc.) |

All three live under `skills_dir`; the bridge routes by scene declaration and the `routes` table in
`bridge_config.json`.

> ⚠️ None of the three skills ship with this repo: `anima-tagger` / `anima-n-prompt` come from
> third-party projects and community shares, `krea2-prompt` is a local-only asset (built from a
> rule set shared in our community). This repo ships **tools only** — the bridge, the route
> scripts, the validation node and the docs. A missing skill simply makes its route report
> missing files and hand nothing to the model.

**Rule changes need no ComfyUI edits**: edit the `.md` files in the skill folder and the
bridge picks them up by modification time (only `bridge_config.json` needs a restart… which
it doesn't either, see the FAQ).

---

## 8. The "Anima 提示词校验" node

`plugins/comfyui-anima-validate-node` wraps `anima-tagger`'s bundled `tools/anima_validate.py`
into a node:

- **Input**: text produced by the LLM
- **Output**: corrected text + a report + an exit code
- **It does**: tag normalization (underscores → spaces, lowercasing), hypernym folding,
  slot-conflict checks
- **It does not**: filter quality words, artist names, or non-English text — **those have to be
  blocked by the prompt rules themselves**; the node only flags them in the report

**Install**: drop the whole `plugins/comfyui-anima-validate-node` folder into
`ComfyUI/custom_nodes/` and restart ComfyUI. The node looks for the validator under
`~/.dsh/skills/anima-tagger`; if your skills live elsewhere, change the node's `skill_dir`.

> The node needs `tools/anima_validate.py` and `models/t5_tokenizer/` from the skill folder —
> those ship with `anima-tagger`, and this repo does not repackage them.

### Bonus: refusals are no longer mistaken for prompts

When the upstream model hits a content boundary it replies with a refusal
(`I'm sorry, but I can't help with that request.`). Left alone, that text is fed to the text
encoder as a prompt and **renders an image with no relation to your intent** — and you
usually can't tell what happened, so you retry and burn quota.

The node runs a refusal check first; on a hit it acts according to `on_reject`:

| `on_reject` | Behaviour |
|---|---|
| **Replace with card** (default) | Swap in the prompt from `reject_card` — draws a girl holding a sign, **making the refusal visible in the image** |
| Pass through and warn | Leave the text, only warn in the report |
| Abort | Raise and stop |

Detection requires **both** a *refusal action* keyword **and** a *moderation subject* keyword
(with `content_filter` as a direct pass). So ordinary failures like `Rate limit exceeded`,
`Insufficient balance`, `unknown model` or a generic timeout are **not misclassified** — which
matters, because a false positive would disguise a real problem as "I got moderated".
The tables cover Chinese refusals too, and self-identifying phrasing
(`As an AI, I am not able to…`).

It folds typographic punctuation to ASCII before matching, because `I can’t help` uses a
curly apostrophe — without folding, every `can't` pattern would miss.

`reject_card` is editable; put your own prompt in it if you prefer.

---

## 9. Reply cache (saves money while seed-hunting)

CN img2img seed-hunting often means **the same image + the same tag, only the seed changing**.
Every request carries the same 21k-char system prompt, so resending it each time wastes the
whole context and adds another 15-odd seconds.

With caching on (`bridge_config.json` → `cache.enabled`, **on by default**) the bridge keys on
**the final assembled system prompt + user text + model + token budget**, replays the previous
body on a hit, and **never contacts the upstream**. Measured:

| | Time | Note |
|---|---|---|
| First call | **16.2 s** | goes upstream |
| Same input again | **0.0 s** | **cache hit, byte-identical body** |
| Different tag | 14.6 s | goes upstream |
| Same tag, different `max_tokens` | 14.1 s | correctly treated as a different request |

The log states it plainly:
`命中缓存  直接回放 3830 字（未调用上游；缓存共 N 条）`.

**Editing a skill or the config changes the system prompt, so the key changes and the old
entry stops matching** — no manual cache clearing. It's an LRU: past `max_entries`
(default 64), the oldest is evicted.

### One trade-off to know about

**Images are not part of the key by default.** CN img2img tags and images are effectively
hard-bound — nudge the image and the tag changes too — so the tag alone discriminates well,
and it saves hashing the image data on every request.

**If you ever see "I changed the image but got the previous prompt"**, turn on
`include_image_in_key`. The cost is one hash of the base64 payload per request (a few hundred
KB, negligible).

### When to turn it off

If you **deliberately want different output from identical input** — using the LLM as a source
of ideas, drawing several phrasings from one tag set — the cache gets in the way. Set
`cache.enabled` to `false` for that. Don't disable it while seed-hunting.

---

## 10. FAQ

**ComfyUI reports "empty response"**
`最大Token数` is too small — the reasoning burned the whole budget. Set 8192. The bridge log
prints `max_tokens=` and `思考=` so you can tell at a glance.

**Code fences or a preamble leaked into the output**
Check that `json_strategy` is set to `仅提示词`. Also note that `bridge_config.json` contains a
`pipeline_overrides` block whose job is to suppress the skill's conversational delivery habits
(wrapping output in a code block, asking for confirmation) — don't delete it.

**Double-clicking the start script flashes and vanishes**
The `start-bridge.bat` here is already **pure ASCII with CRLF line endings**, so this shouldn't
happen. If you edited it yourself, two rules: **never put non-ASCII characters in a `.bat`**
(cmd decodes it with the system code page and blows up), and **line endings must be CRLF**.

**502 with "目标端口没有在监听 (WinError 10061)"**
The upstream isn't running, or `base_url` in `bridge_config.json` is wrong. Not a bridge
problem — the bridge itself is still alive. Start the upstream.

**Do I need to restart the bridge after editing `bridge_config.json`?**
**No.** Every field except `port` is **hot-reloaded**; save the file and the next request picks
it up. (`port` is the listening port — that one needs a restart.)

> This used to be a trap: config was read once at startup, so an edit without a restart kept
> the bridge pointed at the old address, and the symptom looked exactly like "the upstream
> died". That's gone now.

**I attached an image but the output ignores it**
Two checks. **First the bridge log** — it prints `输入=图+tag(N字)` or `输入=仅图(无tag)`. If it
says `输入=tag(...)`, the image wire isn't connected.
**Then check the image size.** With an oversized image some upstreams silently drop the image
part, and the result is indistinguishable from "no image attached" — while the log correctly
says `输入=图+tag`. Scale the long edge to 1024–1536 and retry.

**What if `skills_dir` is wrong?**
The bridge prints every route's status **at startup**, so you don't have to wait for symptoms:

```
skills_dir = C:\Users\you\.dsh\skills
  route krea2      -> anima-tagger       OK
  route anima      -> anima-n-prompt     missing SKILL.md
```

If the directory doesn't exist it says so and tells you to repoint it (no restart needed).
It **does not degrade silently** — previously it returned 200 with no skill rules loaded at
all, and the only warning sat deep in the log where ComfyUI never shows it.

**Where's the log?**
`bridge/bridge.log`. Each line records the scene, input type, branch taken, model, system
prompt length, token usage and elapsed time. Start there when debugging.

**How do I stop the bridge?**
Close its window, or kill by port (**never `taskkill /im python.exe`** — that takes ComfyUI
with it):
```
Get-NetTCPConnection -LocalPort 8899 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

## License

MIT — see [LICENSE](LICENSE).

This repo contains **no** skill content (`anima-tagger` / `anima-n-prompt`) and **no** wd14
model weights. Obtain those from their own sources and respect their original licenses.
