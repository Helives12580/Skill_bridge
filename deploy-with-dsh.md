# Hand me to DSH and let it install this

**For the user** — give your DSH agent the full path of this file and say:

> Follow `deploy-with-dsh.md` and install skill-bridge for me

Then let it work. What follows is the brief it reads.

> 中文版见 [deploy-with-dsh.zh.md](deploy-with-dsh.zh.md)。

---
---

# Brief for the DSH agent

You are installing **skill-bridge** for the user — a small local service that feeds AI skill
rules into ComfyUI's LLM nodes at request time. This file sits next to `bridge/`, `plugins/`
and `README.md`.

**Note: this repo ships no skill** — skills must be installed and called by the user, and `routes` must be
filled in against the skills they actually have (see the agent-ready block in README section 4).

**Goal**: get it deployed **and verified working**, then report back. Do not hand over a
half-finished install.

---

## Step 0 — survey the environment (read-only; change nothing yet)

```powershell
# Where is this package?
$PK = "<directory holding this file>"

# DSH home and skill directory
"$env:USERPROFILE\.dsh"
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty Name

# Candidate Python interpreters (ComfyUI's bundled one first; adjust the path to your install)
& "<ComfyUI dir>\standalone-env\python.exe" -V
python -V

# Locate ComfyUI: find its custom_nodes directory
Get-ChildItem "C:\","D:\","E:\" -Directory -Recurse -Depth 2 -Filter "custom_nodes" -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
```

Show the user what you found, get confirmation, then continue.

---

## Step 1 — find an LLM endpoint this DSH can use

**Don't ask the user for a key yet** — look first. DSH keeps its model config in two places:

```powershell
# (1) provider config: URL, model list, name of the key's environment variable
Get-Content "$env:USERPROFILE\.dsh\settings.yaml" -Encoding UTF8

# (2) the key itself
Get-Content "$env:USERPROFILE\.dsh\.credentials.yaml" -Encoding UTF8
```

In `settings.yaml`, look for a block shaped like this:

```yaml
llm-pi-ai:
  providers:
    <name>:
      api: openai-completions        # <- must be OpenAI-compatible
      baseURL: http://127.0.0.1:xxxx/v1
      apiKeyEnv: <ENV_VAR_NAME>
      models:
        - id: "..."
```

**Selection rules**:
- Only pick providers with `api: openai-completions` (the bridge speaks no other protocol)
- Resolve its `apiKeyEnv` against the `refs:` section of `.credentials.yaml`
- If the provider exposes a single model, use it. If several, **list them and let the user
  choose** — don't decide for them.

**Then prove the endpoint actually works** — this is the gate; don't continue if it fails:

```powershell
$h = @{ 'Authorization' = 'Bearer <key>' }
Invoke-RestMethod -Uri "<baseURL>/models" -Headers $h | ConvertTo-Json -Depth 4
```

A 401 means the key is wrong. A 404 usually means the URL has a `/v1` too many or too few —
try correcting it.

---

## Step 2 — write `bridge/bridge_config.json`

Change exactly these fields. **Leave everything else alone** (`routes` and
`pipeline_overrides` are the core rule plumbing; editing them breaks the output):

```json
{
  "upstream": {
    "base_url": "the baseURL from step 1",
    "api_key":  "the key from step 1",
    "model":    "the model id from step 1"
  },
  "skills_dir": "C:\\Users\\<current user>\\.dsh\\skills"
}
```

Fill `skills_dir` with the real path from step 0 — **no placeholders left behind**.

Then rebuild `routes` from the skills the user actually has: `bridge_config.json` carries a
`_路由格式说明` field explaining every key, and README section 4 ("Step 2.5") has a ready-made block
for that. **Until `routes` is filled in the bridge idles** — no keyword matches, so every request
falls back to `default_route`.

Verify the JSON survived the edit:

```powershell
& "<python>" -c "import json; json.load(open(r'<package>\bridge\bridge_config.json', encoding='utf-8')); print('JSON OK')"
```

---

## Step 3 — place the skill and the plugin

```powershell
# (1) Skills are not distributed with this repo: confirm the user's own skills are in DSH's skill dir
#     are present in DSH's skill directory (the bridge reads from there by default too).
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory | Select-Object -ExpandProperty Name

# (2) Validation node -> ComfyUI's custom_nodes (use the path found in step 0)
Copy-Item "<package>\plugins\comfyui-anima-validate-node" "<ComfyUI dir>\custom_nodes\" -Recurse -Force
```

Confirm afterwards:

```powershell
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory | Select-Object -ExpandProperty Name
# list the installed skills — these are what the routes table must point at
```

**Tell the user**: the validation node only appears after a **ComfyUI restart** — you cannot
do that step for them.

---

## Step 4 — start the bridge and prove it works

```powershell
$py = "<python chosen in step 0>"
Start-Process -FilePath $py -ArgumentList '-X','utf8',"<package>\bridge\dsh_skill_bridge.py" -WindowStyle Hidden
Start-Sleep -Seconds 3

# (1) Is it serving?
Invoke-WebRequest "http://127.0.0.1:8899/v1/models" -UseBasicParsing | Select-Object -ExpandProperty Content
```

**(2) Send one real request.** This step is mandatory — it's the only thing that proves the
skill rules were actually loaded:

```powershell
& $py -X utf8 -c @"
import json, urllib.request
body = {
  'model': 'skill-bridge',
  'messages': [
    {'role': 'system', 'content': 'krea2'},
    {'role': 'user',   'content': '1girl, solo, blue eyes'}
  ],
  'max_tokens': 8000
}
req = urllib.request.Request('http://127.0.0.1:8899/v1/chat/completions',
    data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
    headers={'Content-Type': 'application/json'})
d = json.loads(urllib.request.urlopen(req, timeout=600).read().decode('utf-8'))
c = d['choices'][0]['message']['content']
print('body length:', len(c))
print('first 200 chars:', c[:200])
"@
```

**How to read the result**:
- body length > 1000 → success
- body length == 0 → check the `思考=` field in the bridge log. If it's close to `max_tokens`,
  the budget was eaten by reasoning; raise `max_tokens` to 8192 and retry
- 502 mentioning `api_key 不可用` → the key in step 2 is wrong
- can't connect → read the last lines of `bridge\bridge.log`
- **shape check (send a second request with `anima 扩写`)**: the body should be exactly two parts —
  a tag stream, a blank line, then a few short English sentences (a few hundred characters total).
  A three-part long form, seven-layer headings or `[SUBJECT]` blocks mean that route's
  `pipeline_overrides` did not take effect — compare the anima entries in `bridge_config.json`
  (older configs lack that field; update through the patch in `updates/`)

**(3) Confirm the route matched**, in the log:

```powershell
Get-Content "<package>\bridge\bridge.log" -Tail 8 -Encoding UTF8
```

Expect a line like
`场景=krea2  输入=tag(...字)  分支=扩写  模型=...  system 21377 字 ...`
(the log is written in Chinese; `场景`=scene, `输入`=input type, `分支`=branch, `system`=prompt length).

---

## Step 5 — report back

Keep it structured; don't paste walls of log:

1. **What you found** — python path, LLM endpoint, model, skill directory, ComfyUI directory
2. **What you changed** — the three fields in `bridge_config.json`
3. **What you placed** — validation node into `custom_nodes`; confirmation that the user's own skills
   are present in `~/.dsh/skills`
4. **Verification result** — the body length, and what the bridge log line looked like
5. **What the user must do next**:
   - Restart ComfyUI (so the validation node appears)
   - In ComfyUI, point `ZML_LLM 模型加载器V2`'s `config_folder` at the folder holding `zml_model_key.json`
   - Set `system_prompt` to just `krea2`
   - Set `json_strategy` to `仅提示词 (不强求)`
   - Set `最大Token数` to 8192

---

## Cautions

- **Change nothing in `bridge_config.json` beyond those three fields.** `routes` decides scene
  routing and `pipeline_overrides` converts conversational output into pipeline output; both
  are tuned.
- **The bridge is a long-running process.** Its window/process must stay alive for ComfyUI to
  reach it. If the user wants it on boot, set up a scheduled task.
- **Never stop it with `taskkill /im python.exe`** — that kills ComfyUI too. Kill by port:
  ```powershell
  Get-NetTCPConnection -LocalPort 8899 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```
- **If the user prefers not to use the bridge**, point them at the direct-mode approach in
  `README.md`; the full static rules live in `reference/krea2_pipeline_system_prompt.txt`.
- For anything this brief doesn't cover, read `bridge/bridge.log` and `README.md` first —
  between them they explain essentially every symptom.
