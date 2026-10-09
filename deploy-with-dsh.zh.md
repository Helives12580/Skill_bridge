# 把我交给 DSH，让她帮你装好

**给用户的话**：把本文件的完整路径发给你正在用的 DSH Agent，然后说一句：

> 按 `deploy-with-dsh.zh.md` 帮我把 skill-bridge 装好

剩下的交给它。下面是写给它的执行说明。

> 🌐 English version: [deploy-with-dsh.md](deploy-with-dsh.md)

---
---

# 给 DSH Agent 的执行说明

你正在帮用户部署 **skill-bridge** —— 一个本地小服务，把 AI skill 的规则动态喂给 ComfyUI 的 LLM 节点。
本文件与你同处一个目录，同目录下应有 `bridge/`、`plugins/`、`README.md`。

**注意：本仓库不含 skill 本体**——`anima-tagger`、`anima-n-prompt`、`krea2-prompt` 都需要先自行获取（第三方或本地特有资产，仓库只发纯工具）。

**目标**：部署完成并**实测通过**，最后向用户报告结果。不要只做一半就交付。

---

## 第 0 步 · 先摸清环境（全部只读，先别改任何东西）

```powershell
# 打包目录在哪
$PK = "<本文件所在目录>"

# DSH 家目录与 skill 目录
"$env:USERPROFILE\.dsh"
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name

# Python 解释器候选（ComfyUI 自带的优先，路径按你的实际安装位置改）
& "<ComfyUI目录>\standalone-env\python.exe" -V
python -V

# ComfyUI 安装位置：找 custom_nodes 目录
Get-ChildItem "C:\","D:\","E:\" -Directory -Recurse -Depth 2 -Filter "custom_nodes" -ErrorAction SilentlyContinue |
  Select-Object -ExpandProperty FullName
```

把探到的结果先列给用户看，确认后再继续。

---

## 第 1 步 · 找出这个 DSH 能用的 LLM 服务

**不要问用户密钥**，先自己找。DSH 的模型配置就在两处：

```powershell
# ① provider 配置（地址、模型清单、密钥的环境变量名）
Get-Content "$env:USERPROFILE\.dsh\settings.yaml" -Encoding UTF8

# ② 密钥本体
Get-Content "$env:USERPROFILE\.dsh\.credentials.yaml" -Encoding UTF8
```

在 `settings.yaml` 里找形如这样的段落：

```yaml
llm-pi-ai:
  providers:
    <名字>:
      api: openai-completions        # ← 必须是 openai 兼容
      baseURL: http://127.0.0.1:xxxx/v1
      apiKeyEnv: <环境变量名>
      models:
        - id: "..."
```

**选取规则**：
- 只挑 `api: openai-completions` 的 provider（其他协议桥不支持）
- 它的 `apiKeyEnv` 对应的值，去 `.credentials.yaml` 的 `refs:` 段里找同名键
- 若 provider 只有一个模型，就用它；有多个则**列出来让用户选**，不要自己替用户定

**然后验证这个服务真的通**（这是关键一步，不通就别继续）：

```powershell
$h = @{ 'Authorization' = 'Bearer <密钥>' }
Invoke-RestMethod -Uri "<baseURL>/models" -Headers $h | ConvertTo-Json -Depth 4
```

如果 `/models` 返回 401，检查密钥；返回 404，说明地址少了或多了 `/v1`，试着补正。

---

## 第 2 步 · 写入 `bridge/bridge_config.json`

只改这三个字段，**其余一律不要动**（`routes` 和 `pipeline_overrides` 是核心规则，改了会破坏输出）：

```json
{
  "upstream": {
    "base_url": "第 1 步探到的 baseURL",
    "api_key":  "第 1 步探到的密钥",
    "model":    "第 1 步探到的模型 id"
  },
  "skills_dir": "C:\\Users\\<当前用户名>\\.dsh\\skills"
}
```

`skills_dir` 用第 0 步探到的真实路径填，**不要留占位符**。

改完用 Python 校验 JSON 没写坏：

```powershell
& "<python>" -c "import json; json.load(open(r'<包路径>\bridge\bridge_config.json', encoding='utf-8')); print('JSON OK')"
```

---

## 第 3 步 · 放置 skill 与插件

```powershell
# ① skill 不随本仓库分发：确认需要的 skill 已在 DSH 的 skill 目录（anima-tagger / anima-n-prompt / krea2-prompt）
#    （没有的话先去获取它们，桥默认也从这里读）
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory | Select-Object -ExpandProperty Name

# ② 校验节点 → ComfyUI 的 custom_nodes（路径用第 0 步探到的）
Copy-Item "<包路径>\plugins\comfyui-anima-validate-node" "<ComfyUI目录>\custom_nodes\" -Recurse -Force
```

放完确认一下：

```powershell
Get-ChildItem "$env:USERPROFILE\.dsh\skills" -Directory | Select-Object -ExpandProperty Name
# 应该能看到 anima-tagger 和 anima-n-prompt（有 krea2-prompt 也会列出；缺它只影响 krea2 自然语言那三条路由）
```

**提醒用户**：校验节点需要**重启 ComfyUI** 才会出现，这一步你代替不了。

---

## 第 4 步 · 启动桥并实测

```powershell
$py = "<第 0 步选定的 python>"
Start-Process -FilePath $py -ArgumentList '-X','utf8',"<包路径>\bridge\dsh_skill_bridge.py" -WindowStyle Hidden
Start-Sleep -Seconds 3

# ① 服务在不在
Invoke-WebRequest "http://127.0.0.1:8899/v1/models" -UseBasicParsing | Select-Object -ExpandProperty Content
```

**② 发一个真请求**（这一步必须做，它是唯一能证明"规则真的被读进去了"的验证）：

```powershell
& $py -X utf8 -c @"
import json, urllib.request
body = {
  'model': 'skill-bridge',
  'messages': [
    {'role': 'system', 'content': 'krea2 扩写'},
    {'role': 'user',   'content': '1girl, solo, blue eyes'}
  ],
  'max_tokens': 8000
}
req = urllib.request.Request('http://127.0.0.1:8899/v1/chat/completions',
    data=json.dumps(body, ensure_ascii=False).encode('utf-8'),
    headers={'Content-Type': 'application/json'})
d = json.loads(urllib.request.urlopen(req, timeout=600).read().decode('utf-8'))
c = d['choices'][0]['message']['content']
print('正文长度:', len(c))
print('前 200 字:', c[:200])
"@
```

**判定标准**：
- 正文长度 > 1000 → 成功
- 正文长度 = 0 → 看桥日志里的 `思考=` 字段，若该值接近 max_tokens，就是预算不够，把 `max_tokens` 调到 8192 再试
- 报 502 且提到 `api_key 不可用` → 第 2 步的密钥没填对
- 连不上 → 看 `bridge\bridge.log` 最后几行
- **形态判定（再发一次 `anima 扩写`）**：正文应是「tag 流 ＋ 空行 ＋ 自然语言」两段，总长几百字符。若出现三段式长稿、七层小标题或 `[SUBJECT]` 分块，说明那条 route 的 `pipeline_overrides` 没生效——拿 `bridge_config.json` 里 anima 路由的覆盖层对照一下（旧版没有这个字段，需要按 `updates/` 里的补丁更新）

**③ 看日志确认路由命中**：

```powershell
Get-Content "<包路径>\bridge\bridge.log" -Tail 8 -Encoding UTF8
```

应该能看到类似：`场景=krea2  输入=tag(...字)  分支=扩写  模型=...  system 21377 字 ...`

---

## 第 5 步 · 报告给用户

按这个结构说，别贴大段日志：

1. **探到什么**：python 路径、LLM 服务地址、模型、skill 目录、ComfyUI 目录
2. **改了什么**：`bridge_config.json` 的三个字段
3. **放了什么**：校验节点进 `custom_nodes`；并确认三个 skill 中实际存在的那些已在 `~/.dsh/skills`
4. **实测结果**：正文长度多少、桥日志那一行长什么样
5. **用户接下来要做**：
   - 重启 ComfyUI（校验节点才会出现）
   - 在 ComfyUI 里把 `ZML_LLM 模型加载器V2` 的 `config_folder` 指向放 `zml_model_key.json` 的目录
   - `system_prompt` 只写一句 `krea2 扩写`
   - `json_strategy` 选「仅提示词 (不强求)」
   - `最大Token数` 设 8192

---

## 注意事项

- **`bridge_config.json` 里除了那三个字段，别的都不要改**。`routes` 决定场景怎么路由、`pipeline_overrides` 负责把对话式输出掰成流水线式输出，都是精心调过的。
- **桥是常驻进程**。它的窗口/进程要一直留着，ComfyUI 才能调它。用户如果想随开机启动，可以把它做成计划任务。
- **别用 `taskkill /im python.exe` 停桥**，那会连带杀掉 ComfyUI。要停就按端口定位：
  ```powershell
  Get-NetTCPConnection -LocalPort 8899 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
  ```
- **用户不想用桥**的话，看 `README.md` 的直连方案，规则全文在 `reference/krea2_pipeline_system_prompt.txt`。
- 遇到本说明没覆盖的问题，先读 `bridge/bridge.log` 和 `README.md`，两者加起来基本能解释所有现象。
