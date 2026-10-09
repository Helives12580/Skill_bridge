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
| **skill-bridge-toolkit** | Optional | `Anima 提示词校验` node: deterministic tag validation (normalization / hypernym folding / slot conflicts / length) plus refusal detection | This repo, `plugins/` |
| **[ComfyUI-NL-PromptForge](https://github.com/Helives12580/ComfyUI-NL-PromptForge)** | Optional | `NL Prompt Forge 黑名单过滤` node: catches stray code fences. The validate node's **refusal-detection heuristic** (dual-keyword table + punctuation folding) is modelled on this plugin | <https://github.com/Helives12580/ComfyUI-NL-PromptForge> |

> **This repo ships no skill.** Skills must be installed and called by you; the rules live outside
> this repo. Once installed, point the `routes` table in `bridge_config.json` at your own skills
> (section 4).

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

> The table below shows **example declarations** (how the author's own setup is wired). What the
> bridge actually matches is the keywords you wrote in `routes` — if your routes use other
> keywords, write those instead.

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
- Your own skills — **not included in this repo**; install them yourself (section 4)

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
│  ├─ add_krea2_routes.py         register / dry-run example routes (`--check`, writes nothing)
│  ├─ fix_anima_routes.py         attach the "two-part + 512-token cap" override to anima routes
│  └─ zml_model_key.json          ZML preset file (holds the API key)
├─ plugins/
│  └─ skill-bridge-toolkit/        the "Anima 提示词校验" node + number→text + H3 frame-anchor stack
├─ updates/                       incremental patches (skip the full re-download)
│  └─ 2026-10-10-anima-krea2.md   anima two-part output + krea2 natural-language routes
└─ reference/                     for people who prefer not to use the bridge
   ├─ krea2_pipeline_system_prompt.txt  static rules for direct mode
   └─ krea2_wiring.md             Krea2 Control wiring reference (Chinese)
```

> ⚠️ **No skill is bundled.** Skills must be installed and called by you. The
> `bridge_config.json` in this repo is a **format example** too — `routes` has to be filled in
> against the skills you installed; see the agent-ready block in section 4.

---

## 4. Setup (three steps)

### Step 1 — put the skills in place

**This repo does not ship any skill.** Put your own skills in one directory; the conventional
location for DSH users is:

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

Everything else is documented inline. **Note that `routes` is a placeholder example** — it has to
be filled in against your own skills; see the next step.

### Step 2.5 — how to fill `routes` (hand this block to your agent)

The `bridge_config.json` shipped here is a **format example**: the skill names and reference file
names inside `routes` are placeholders pointing at nothing. Paste the block below to your DSH /
Claude / any agent and it will build `routes` from the skills **you** actually have installed:

````text
skill-bridge lives at <path>. Please fill in the routes table in bridge/bridge_config.json.

1. List the skills installed under my skills_dir, and for each one the files in its references/ (read SKILL.md to decide which of them are required).
2. Read the `_路由格式说明` field in bridge_config.json and follow it to add one route per usable skill:
   - key: a set of keywords identifying that skill, several aliases separated by `|`, most common first. The bridge picks the route whose matched alias is longest, so keep dedicated aliases longer than generic words.
   - skill: that skill's folder name under skills_dir.
   - refs: array of file names under that skill's references/ that must be fed to the model.
   - refs_image_only: if the skill switches to a different set of docs for "an image but no text anchor", put it here; otherwise omit.
   - model: optional — omitted means upstream.model.
3. To turn chat-style output into pipeline output (body only, no code fences, no explanation, length budget), add pipeline_overrides to that route; without it the global one applies.
4. Point default_route at the route you use most.
5. Do not touch upstream, skills_dir or port. Validate the JSON when done and tell me which fields changed.
````

Afterwards maintenance is trivial: **rule changes need no ComfyUI edits** — edit the `.md` files in
the skill folder and the bridge reloads them by modification time. Changing `bridge_config.json`
(including `routes`) is hot-reloaded as well; only `port` needs a restart.

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

The `system_prompt` field takes **one line**. The bridge matches keywords, longest match wins.
The table is an **example** of the author's own wiring — the bridge only knows the keywords you put
in your own `routes`:

| Declaration (example) | How that route is wired | Output shape |
|---|---|---|
| `krea2 扩写` | a "tag expansion" skill; refs point at its expansion branch + format rules | long form; tags act as anchors that clamp the input |
| `krea2 megastructure` / `krea2 colossal` / `krea2 kaiju` / `krea2 cthulhu` | a "colossal photography" skill; refs point at its relevant chapters | photoreal long paragraph (scale / mass / negative space) |
| `krea2 poster` / `krea2 typography` | a "poster / typography" skill | poster-style graphic output (layout + text first) |
| `krea2 random` / `krea2 random poster` | a "no-reference, free invention" skill | the model picks the subject |
| `anima 扩写` | a "tag-stream framework" skill ＋ a two-part override | tag stream ＋ natural language (hard 512-token cap) |
| `sdxl 扩写` | same as above, only the length budget differs | same two-part shape |
| `minimax 扩写` | a "video prompt" skill | video prompts |
| empty | — | falls back to `default_route` |

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

## 7. The "Anima 提示词校验" node

`plugins/skill-bridge-toolkit` wraps the `tools/anima_validate.py` that ships with a skill
(whichever one you installed) into a node:

- **Input**: text produced by the LLM
- **Output**: corrected text + a report + an exit code
- **It does**: tag normalization (underscores → spaces, lowercasing), hypernym folding,
  slot-conflict checks
- **It does not**: filter quality words, artist names, or non-English text — **those have to be
  blocked by the prompt rules themselves**; the node only flags them in the report

**Install / upgrade**: drop the whole `plugins/skill-bridge-toolkit` folder into
`ComfyUI/custom_nodes/` and restart ComfyUI. The node looks for the validator under
`~/.dsh/skills`; if your skills live elsewhere or under other names, change the node's `skill_dir`.

> ⚠️ **This package was renamed: on upgrade you must delete the old folder first.**
> Old folder name `comfyui-anima-validate-node` → new name `skill-bridge-toolkit`, and it grew from a
> single validation node into three: the validation node, number→text (`number_to_text.py`) and an
> H3 frame-anchor stack (`h3_guide_stack.py`). ComfyUI keys packages **by folder name**, so
> overwriting in place — or adding the new folder without deleting the old one — loads both packages,
> registers the nodes twice, and can leave workflow nodes pointing at the overwritten copy
> (lost widgets, wrong values, or load errors).
>
> ```powershell
> # 1) delete the old package first (skip if this is a fresh install)
> Remove-Item "<ComfyUI>\custom_nodes\comfyui-anima-validate-node" -Recurse -Force
> # 2) add the new one
> Copy-Item "<repo>\plugins\skill-bridge-toolkit" "<ComfyUI>\custom_nodes\" -Recurse -Force
> # 3) restart ComfyUI
> ```

> The node needs `tools/anima_validate.py` and `models/t5_tokenizer/` from the skill folder —
> those ship with the corresponding skill, and this repo does not repackage them.

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

## 8. Reply cache (saves money while seed-hunting)

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

## 9. FAQ

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
  route <keyword>  -> <skill dir>        OK
  route <keyword>  -> <skill dir>        missing SKILL.md
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

This repo contains **no** skill content and **no** wd14
model weights. Obtain those from their own sources and respect their original licenses.
