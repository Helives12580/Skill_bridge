# skill-bridge — 让 ComfyUI 自动调用 AI skill 做提示词扩写

一个本地小服务，把 **AI skill 的规则**动态喂给 ComfyUI 的 LLM 节点。
你只在节点里写一句场景声明（如 `krea2 扩写`），规则由桥从 skill 目录现场读取。

**换底模时只改那一句声明，规则本身一个字都不用动。**

> 🌐 **English docs**: [README.en.md](README.en.md) · [deploy-with-dsh.md](deploy-with-dsh.md)

> 🚀 **如果你在用 DSH**：不用手动装。把 `deploy-with-dsh.zh.md` 的完整路径发给你的 DSH Agent，
> 说一句「按这个文件帮我装好」，它会自己探环境、识别可用的 LLM 服务、写配置、跑通实测再回报。

---

## 它在 ComfyUI 里怎么跑起来

### 用到的插件

| 插件 | 必需性 | 提供什么 | 项目地址 |
|---|---|---|---|
| **ComfyUI-ZML-Image** | **必需** | LLM 节点全套：`模型加载器V2`（读 API 配置）、`系统提示词`（**填场景声明的那一句**）、`参数设置`（温度 / 最大 Token 数）、`对话主程序`（发请求拿回复）、`过滤思考`（去掉推理模型的思考块）。这套节点用官方 `openai` SDK，所以**任何 OpenAI 兼容服务都能接** | <https://github.com/zml-w/ComfyUI-ZML-Image> |
| **comfyui-anima-validate-node** | 可选 | `Anima 提示词校验` 节点：确定性校验 tag（规范化 / 上位词折叠 / 槽位冲突 / 长度）＋ 审核拒绝检测 | 本仓库 `plugins/` 目录 |
| **[ComfyUI-NL-PromptForge](https://github.com/Helives12580/ComfyUI-NL-PromptForge)** | 可选 | `NL Prompt Forge 黑名单过滤` 节点：兜底清掉意外漏出的代码块标记。本仓库校验节点的**审核拒绝检测判据**（双重命中词表 + 标点折叠）参考了该插件的实现 | <https://github.com/Helives12580/ComfyUI-NL-PromptForge> |

> **本仓库不包含任何 skill 本体。** 规则内容来自 `anima-tagger` 与 `anima-n-prompt` 两个 skill，
> 需要你自己准备（见下节「用到哪些 skill」）。

### 链路

```
   LoadImage / DanbooruGallery          ← 图片（可选，要先缩到长边 1024~1536）
          │
          ▼
   BSK_Tagger (wd14 本地打标)            ← 本机打标，不烧 token
          │
          ▼
   AnimaTKDanbooruTagGetter             ← 分类过滤
          │
          ▼
   DanbooruTagSorterNode / TK String Router / PromptCleaner
          │
          ▼
   ZML_LLM 对话主程序 . user_input ──────┐
   ZML_LLM 对话主程序 . input_image ─────┤  图片与文本一起送进 user 消息
                                         │  （OpenAI 多模态格式，base64）
   ZML_LLM 模型加载器V2 ── . model_config┤
   ZML_LLM 系统提示词   ── . system_prompt  ← 只写一句场景声明：krea2 扩写
   ZML_LLM 参数设置     ── . params       ┘  最大Token数 设 8192
          │
          │  请求发往 http://127.0.0.1:8899/v1
          ▼
   ┌──────────────────────────────────────────────────┐
   │  skill-bridge（本仓库）                          │
   │  1. 按场景声明选 skill 与参考文档                │
   │  2. 拼出两万多字的 system prompt（规则＋覆盖层）  │
   │  3. 命中缓存就直接回放，不碰上游                 │
   │  4. 转发给真正的 LLM 服务                        │
   └──────────────────────────────────────────────────┘
          │
          ▼
      模型返回正文（三段式长稿）
          │
          ▼
   ZML_LLM 过滤思考                      ← 去掉推理模型的思考块
          │
          ▼
    Anima 提示词校验                     ← 节点侧兜底：校验 + 拒绝检测
          │
          ▼
   CLIPTextEncode → KSampler . positive → ControlNet 图生图
```

### 为什么需要这一层桥

ZML 的 `系统提示词` 节点是**静态**的：你把规则全文写进去，它就一直发那一份。
这在换底模时很麻烦——krea2 要三段式长稿、anima 要短 tag 流、minimax 又是另一套，
每换一次就得把几十条规则重抄一遍。

桥把规则留在 skill 文件里，**运行时现读现拼**：你在 `系统提示词` 里只写一句
`krea2 扩写`，桥负责把对应的规则、格式规范、参考词库全部注入进去。

顺带解决的几件事：

- **换底模只改一句声明**，规则一个字不动
- **抽卡时的重复请求直接命中缓存**，不再每张图都重发两万多字
- **审核拒绝**被识别出来换成一张提示卡，而不是把拒绝文本当提示词画出一张无关的图
- **带图时以图为准**扩写，避免提示词与 ControlNet 控制图互相拉扯

---

## 系统提示词速查（桥模式 · 先看这个）

`ZML_LLM 系统提示词` 节点的 `system_prompt` **只写一句**，规则由桥自动补齐：

| 你想做的事 | `system_prompt` 就写 |
|---|---|
| **Krea2 长稿扩写**（最常用） | `krea2 扩写` |
| Krea2 且要传图 | `krea2 扩写`，并把图片接到 `input_image` |
| Minimax | `minimax 扩写` |
| Anima 场景 | `anima 扩写` |
| SDXL 场景 | `sdxl 扩写` |
| 留空 | 走默认路由 |

> **只写这一句，不要把规则全文粘进去。** 粘全文是「直连模式」才需要的做法——那是另一个方案，
> 详见 `reference/` 目录里的 `krea2_pipeline_system_prompt.txt`。

**换底模时只改这一句**，规则本身在 skill 文件里，一个字都不用动。

**想临时换模型**：在 `zml_model_key.json` 里多加几个指向同一个桥地址、只是 `model` 字段不同的 preset，
然后在 ComfyUI 面板切 `preset_name` 即可——不用改配置，也不用重启桥。

---

## 一、它解决什么问题

本地打标 + AI 扩写是两件事：

- **wd14 打标**很准，但产出的是 tag 串，缺少自然语言的组织与细节密度；
- 自然语言长稿需要 LLM，可每个底模（krea2 / anima / sdxl / minimax）要的写法都不同。

如果把规则**静态粘进工作流**，换一次底模就要重写一次，几十条规则手抄一遍。
这个桥把规则留在 skill 文件里，运行时现读现拼，工作流侧永远只有一句声明。

---

## 二、依赖什么

插件与节点的完整清单见开头的「[用到的插件](#它在-comfyui-里怎么跑起来)」。

### 运行环境

- Python 3.8+ —— 用 ComfyUI 自带的那个解释器就够，桥**只用标准库，零依赖**
- 一个 OpenAI 兼容的 LLM 服务（本地网关 / 自建代理 / 各家云 API 都行）
- `anima-tagger` 与 `anima-n-prompt` 两个 skill —— **本仓库不含**，需要自行获取（见第七节）

---

## 三、文件清单

```
skill-bridge/
├─ README.md                      中文说明（本文件）
├─ README.en.md                   英文说明
├─ deploy-with-dsh.zh.md                 交给 DSH Agent 自动部署（中文，可选）
├─ deploy-with-dsh.md             同上，英文版
├─ LICENSE                        MIT
├─ .gitignore
├─ bridge/
│  ├─ dsh_skill_bridge.py         桥本体（纯标准库）
│  ├─ bridge_config.json          配置：上游地址、密钥、场景路由、通用覆盖规则
│  ├─ start-bridge.bat                  双击启动（Windows）
│  └─ zml_model_key.json          ZML 节点的预设文件（填密钥用）
├─ plugins/
│  └─ comfyui-anima-validate-node/  「Anima 提示词校验」节点
└─ reference/                           不想用桥的话看这里
   ├─ krea2_pipeline_system_prompt.txt  直连模式的静态规则全文
   └─ krea2_wiring.md                Krea2 Control 那套的接线参考
```

> ⚠️ **本仓库不含 skill 本体。** 规则内容来自 `anima-tagger` 与 `anima-n-prompt` 两个 skill，
> 需要你自己获取后放进 `skills_dir`（见第七节）。

---

## 四、配置（三步）

### 第 1 步：放 skill

**本仓库不含 skill 本体**，请先自行获取 `anima-tagger` 与 `anima-n-prompt`，
放到你习惯的位置。默认约定是：

```
C:\Users\<你的用户名>\.dsh\skills\
```

放别处也行，第 2 步改 `skills_dir` 指过去即可。

### 第 2 步：改 `bridge/bridge_config.json`

只需改两处：

```json
{
  "upstream": {
    "base_url": "https://你的服务地址/v1",
    "api_key":  "REPLACE_ME",          ← 改成你的密钥
    "model":    "你的默认模型名"
  },
  "skills_dir": "C:\\Users\\<你的用户名>\\.dsh\\skills"   ← 改成第 1 步的实际目录
}
```

其余字段都有注释说明，不动也能跑。

### 第 3 步：启动 + 接入 ComfyUI

1. 双击 `bridge/start-bridge.bat`，看到 `[bridge] ready http://127.0.0.1:8899/v1` 就成了（**窗口保持开着**）
2. 把 `zml_model_key.json` 放到任意目录，填上你的密钥，然后在 ComfyUI 里：
   - `ZML_LLM 模型加载器V2` 的 `config_folder` 填**那个目录**
   - `preset_name` 选 **`SKILL-桥`**
3. `ZML_LLM 系统提示词` 里写一句场景声明（下一节）

---

## 五、ComfyUI 里的连接

```
LoadImage / DanbooruGallery
   └─→ BSK_Tagger (wd14 本地打标)
         └─→ AnimaTKDanbooruTagGetter (分类过滤)
               └─→ DanbooruTagSorterNode / TK String Router
                     └─→ PromptCleaner
                           └─→ ZML_LLM 对话主程序 . user_input
                                 ↑
      ZML_LLM 模型加载器V2 ─ . model_config
      ZML_LLM 系统提示词   ─ . system_prompt      ← 只写一句：krea2 扩写
      ZML_LLM 参数设置     ─ . params             ← 最大Token数 设 8192
                                 │
                                 ↓ 回复内容
                           ZML_LLM 过滤思考        （去掉 <think> 思考块）
                                 ↓
                           NL Prompt Forge 黑名单过滤   （可选，兜底）
                                 ↓
                           Anima 提示词校验          （可选，见下）
                                 ↓
                           CLIPTextEncode . text
                                 ↓
                           KSampler . positive
```

### 需要连图的时候

把图片接到 `ZML_LLM 对话主程序` 的 `input_image`，并**同时**接上 wd14 的 tag 文本。桥会自动判断：

| 输入 | 走哪条分支 | 输出形态 |
|---|---|---|
| **图 + tag** | 扩写分支（tag 当已确认锚点，图作视觉参考） | 三段式长稿 |
| **仅图、无 tag** | 反推分支 | tag 流 + 几句自然语言 |
| 仅 tag | 扩写分支 | 三段式长稿 |

### ⚠️ 连图前**必须先缩图**

`ZML_LLM 对话主程序` **不做任何缩放**——它把整张原图直接转 base64 塞进请求。实测量级：

| | 原图 | 送进上下文的大小 |
|---|---|---|
| 不做缩放 | 2112×3840 PNG，9.3 MB | base64 后约 **12.5 MB** |
| 缩到长边 1024 | 563×1024 JPEG，108 KB | base64 后约 **144 KB**（**差 87 倍**）|

不缩图的后果是请求要么直接失败、要么贵得离谱，而且**不会报错**，只是变慢和烧额度；
更隐蔽的是有些上游会**静默丢掉图片部分**，于是模型只按 tag 扩写，你还以为它在参考图。

**做法**：在接进 `input_image` 之前加一个缩放节点（`ImageScale` / `ImageScaleBy`），
**长边压到 1024~1536 就足够**——ControlNet 不需要更高分辨率，视觉模型也读不出更多细节。

### 连图时，图是扩写的基准

图接上后，模型会**以图像为准来写长文**：动作与姿态、人物朝向与视线、四肢位置、
构图与机位（景别 / 视角 / 主体占幅 / 前后景分层）、场景道具、光源方向与阴影落点，
全部照图写。tag 只负责钉住图上看不出精确取值的离散特征（发色、瞳色、服装款式、配饰）。

**tag 与图冲突时以 tag 为准**，但不会写出与图矛盾的内容。实测某次输入 tag 写 `outdoors`
而图是白背景，输出两项都保留了——按规则就该如此。

### 两个必改的参数

| 参数 | 值 | 为什么 |
|---|---|---|
| `ZML_LLM 对话主程序` → `json_strategy` | **`仅提示词 (不强求)`** | 默认选项会往系统提示里追加「请按 JSON 输出」，把长稿形态搞乱 |
| `ZML_LLM 参数设置` → `最大Token数` | **8192** | 推理模型的思考会消耗大量 token，设小了正文会被压成空 |

---

## 六、场景声明怎么写

`ZML_LLM 系统提示词` 的 `system_prompt` **只写一句**，桥按关键词匹配（命中最长者优先）：

| 声明 | 加载的 skill | 适用 |
|---|---|---|
| `krea2 扩写` | anima-tagger · 扩写分支 | krea2 长稿 |
| `anima 扩写` | anima-n-prompt · 框架规则 | anima 场景 |
| `sdxl 扩写` | anima-n-prompt · 框架规则 | sdxl 场景 |
| `minimax 扩写` | anima-tagger · 扩写分支 | minimax |
| 留空 | 默认路由 | — |

**想临时换模型**：在 `zml_model_key.json` 里多加几个指向同一个桥地址、只是 `model` 不同的 preset，然后在 ComfyUI 面板切换即可——不用改配置、不用重启桥。

---

## 七、用到哪些 skill

| skill | 内容 | 本包里用它做什么 |
|---|---|---|
| **`anima-tagger`** | 反推 / 创作 / **扩写**三分支，含格式硬规则、槽位顺序、禁用清单 | **主线**：tag → 三段式长稿；带图时做反推 |
| **`anima-n-prompt`** | 中文场景 → 提示词 的整套生成框架（ROLE / 输出协议 / 互斥表 / 槽位 / 场景决策树） | anima / sdxl 场景 |

两者都放在 `skills_dir` 指向的目录下，桥按场景声明和 `bridge_config.json` 的 `routes` 路由。

**改规则不用改 ComfyUI**：直接编辑 skill 目录里的 `.md` 文件即可，桥按文件修改时间自动重载（改 `bridge_config.json` 才需要重启桥）。

---

## 八、那个「Anima 提示词校验」节点

`plugins/comfyui-anima-validate-node` 是配套的确定性校验器，把 `anima-tagger` 自带的 `tools/anima_validate.py` 包成了节点：

- 输入：LLM 产出的文本
- 输出：修正后的文本 + 报告 + 退出码
- 它做：tag 规范化（下划线转空格、小写）、上位词折叠、槽位冲突检查
- 它**不做**：质量词、画师名、中文的过滤——**那些要靠提示词规则拦住**，节点会在报告里单独提醒

**安装**：把 `plugins/comfyui-anima-validate-node` 整个目录放进 `ComfyUI/custom_nodes/`，重启 ComfyUI。
节点默认去 `~/.dsh/skills/anima-tagger` 找校验器；skill 放在别处的话，在节点的 `skill_dir` 里改。

> 这个节点依赖 skill 目录里的 `tools/anima_validate.py` 与 `models/t5_tokenizer/`——
> 它们随 `anima-tagger` skill 一起分发，本仓库不重复打包。

### 顺带解决「审核拒绝被当成提示词」

上游模型触到审核边界时会直接回一段拒绝话（`I'm sorry, but I can't help with that request.`）。
不拦的话，这段拒绝文本会被当成提示词送进文本编码器，**画出一张毫无关系的图**——而且你多半看不出
发生了什么，只会反复重跑、白白烧额度。

节点会先做一次拒绝检测，命中后按 `on_reject` 处理：

| `on_reject` | 行为 |
|---|---|
| **替换为提示卡**（默认） | 换成 `reject_card` 里的提示词，画出一张举牌少女，**把拒绝显示在图上** |
| 原样输出并报警 | 保留原文不动，只在报告里报警 |
| 中断执行 | 直接抛错停下 |

检测采用「**拒绝动作词**」＋「**审核对象词**」**双重命中**判据（另有 `content_filter` 直通），
所以 `Rate limit exceeded`、`Insufficient balance`、`unknown model`、`请求过于频繁` 这类普通失败
**不会被误判**——这点很关键，否则真正的问题会被掩盖成「被审核了」。词表也覆盖中文拒绝
（`该请求涉及政治敏感内容，已被拒绝`）与自我声明式（`As an AI, I am not able to…`）。

它会先把印刷体标点折成 ASCII 再匹配，因为 `I can’t help` 用的是弯撇号，不折叠的话所有
`can't` 模式都会落空。

`reject_card` 是可编辑的，想换成自己的提示词直接改。

---

## 九、回复缓存（抽卡省钱）

CN 图生图抽卡时，常出现**同一张图 + 同一串 tag、只换随机种子反复跑**的情况。
每次请求的 system prompt 都是同一份两万多字的规则——无条件重发等于每张都白烧一遍上下文，
还要多等十几秒。

开启缓存后（`bridge_config.json` 的 `cache.enabled`，**默认开**），桥按
**「最终组装的 system + 用户文本 + 模型 + token 上限」**建键，命中就直接回放上次的正文，
**完全不碰上游**。实测：

| | 耗时 | 说明 |
|---|---|---|
| 第一次 | **16.2 s** | 走上游，正常生成 |
| 同输入第二次 | **0.0 s** | **命中缓存，逐字相同的正文** |
| 换 tag | 14.6 s | 重新走上游 |
| 同 tag 但改 `max_tokens` | 14.1 s | 正确地视为不同请求 |

日志里会明确写出来：`命中缓存  直接回放 3830 字（未调用上游；缓存共 N 条）`。

**改 skill 或改 config 会改变 system prompt，键自然失效** —— 不需要手动清缓存。
缓存是 LRU，超过 `max_entries`（默认 64）自动淘汰最旧的。

### 一个需要你知道的取舍

**图片默认不参与建键**。因为 CN 图生图的 tag 与图基本是硬绑定的，图略变 tag 就会变，
用 tag 判断已经足够，还能省掉对图片数据做哈希的开销。

**但如果你遇到"换了图却拿到上一张的提示词"**，就把 `include_image_in_key` 打开——
代价是每次请求都要哈希一遍图片数据（几百 KB 的 base64，开销很小）。

### 什么时候该关掉它

如果你**故意想让同样的输入产出不同结果**（比如把 LLM 当灵感来源，同一组 tag 想多抽几种写法），
缓存会妨碍你——那种场景把 `cache.enabled` 设为 `false`。抽卡时不要关。

---

## 十、常见问题

**Q：ComfyUI 报「返回空内容」**
`最大Token数` 设小了，思考把预算吃光了。设 8192。桥的日志里会打出 `max_tokens=` 和 `思考=` 两个值，一眼能看出。

**Q：输出里出现代码块或前言**
检查 `json_strategy` 是否选了「仅提示词」。另外 `bridge_config.json` 里有一段 `pipeline_overrides`，专门压制「skill 原文要求打包进代码块」这类对话交付习惯——不要删它。

**Q：双击启动脚本一闪而过**
本包里的 `start-bridge.bat` 已是 **纯 ASCII + CRLF** 换行，不会再有这个问题。如果你自己改过它，注意两点：**别在 bat 里写中文**（cmd 按系统代码页解析会崩），**换行必须是 CRLF**。

**Q：报 502，说「目标端口没有在监听（WinError 10061）」**
上游网关没启动，或者 `bridge_config.json` 里的 `base_url` 写错了。这不是桥的问题——
桥自己还在跑。把上游启动起来即可。

**Q：改了 `bridge_config.json` 要不要重启桥？**
**不用**。除 `port` 外所有字段都是**热重载**的，存盘后下一次请求就生效。
（`port` 是监听端口，改它必须重启。）

> 这条以前是个坑：配置只在桥启动时读一次，改了不重启就会一直连旧地址，症状看起来
> 跟"上游挂了"一模一样。现在不存在了。

**Q：传了图，但输出像是没看图、跟图对不上**
两步排查。**先看桥日志**那一行——它会显示 `输入=图+tag(N字)` 或 `输入=仅图(无tag)`；
若显示的是 `输入=tag(...)`，说明图的线没接上。

**再看图有没有缩过**。原图过大时，部分上游会静默丢弃图片部分，模型就只按 tag 扩写，
表现和"没接图"一模一样，但日志里 `输入=图+tag` 是对的。把长边缩到 1024~1536 再试。

**Q：配置里 `skills_dir` 写错了会怎样？**
桥在**启动时**就会把每条 route 的落地情况打出来，不用等出问题才发现：

```
skills_dir = C:\Users\你\.dsh\skills
  route krea2      -> anima-tagger       OK
  route anima      -> anima-n-prompt     缺失 SKILL.md
```

目录不存在时直接提示「改成你放 skill 的目录；改完不必重启桥（配置是热重载的）」。
**不会静默降级**——改之前它会照常返回 200，只是 skill 规则一条都没加载，
用户只会觉得"输出莫名变差"，而警告埋在日志深处、ComfyUI 端根本看不到。

**Q：日志在哪**
`bridge/bridge.log`。每行记录场景、输入类型、走的哪个分支、用的模型、system 提示长度、token 用量、耗时。排查问题先看它。

**Q：怎么停掉桥**
关掉那个窗口即可；也可以按端口结束进程（**别用 `taskkill /im python.exe`**，会连带杀掉 ComfyUI）：
```
Get-NetTCPConnection -LocalPort 8899 -State Listen | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

---

## 许可

MIT，见 [LICENSE](LICENSE)。

本仓库**不含** `anima-tagger` / `anima-n-prompt` 两个 skill 的正文，也**不含** wd14 模型权重——
这些请从各自的来源获取，并遵循其原有许可。
