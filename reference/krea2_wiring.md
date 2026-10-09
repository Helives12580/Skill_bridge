# Krea2 流水线接线图（含 LLM 扩写与校验）

主线：**本地反推 → 本地分类 → LLM 扩写 → 确定性校验 → CLIP → 采样**。
除 LLM 那一步，全程零 token。

---

## 一、全链路总图

```
                          ┌───────────── 图源 ─────────────┐
   LoadImage(本地图) ──┐
   DanbooruGallery ────┴→ Switch image ─→ ImageScaleToMaxDimension(1536)
                                              │
        ┌─────────────────────────────────────┼──────────────────────────┐
        ▼                                     ▼                          ▼
   VAEEncode                        AIO_Preprocessor(Depth)     AIO_Preprocessor(OpenPose)
   (i2i latent)                              │                          │
        │                          Krea2ControlImageEncode              │
        │                                     │                          │
        │                          ┌──────────┴──────────┐               │
        │                          ▼                     ▼               ▼
        │              Krea2ControlLoRALoader   LoraLoaderModelOnly   TextEncodeKrea2OstrisEdit
        │              → Krea2ControlApply      → Krea2OstrisEditModelPatch      │
        │                          │                     │               │
        │                          └────→ LazySwitchKJ(MODEL) ←──────────┘
        │                                     ▲
        │                          PrimitiveBoolean(模式开关)
        │                                     │
        ▼                                     ▼
   ┌── 采样链 ────────────────────────────────────────────┐
   │  i2i:  KSampler(denoise .5) → LatentUpscaleBy 1.2    │
   │        → KSampler(denoise .4)                        │
   │  t2i:  KreaTwoStageSampler                           │
   │           └──→ LazySwitchKJ(LATENT) ←────────────────┘
   │                     ▼
   │        VAEDecode → PreviewImage → RTX VSR → BSK_SaveImage
   └──────────────────────────────────────────────────────

                          ┌──────── 提示词链 ────────┐
   BSK_Tagger(wd14 本地反推)
     → AnimaTKDanbooruTagGetter(分类过滤)
       → DanbooruTagSorterNode / TK String Router
         → PromptCleaner
           → JoinStringMulti（并入风格词 / tag selectors / 手写词）
             │
             ├──────────────────→ ShowText（预览，保留）
             │
             ▼
        ★ LLM 扩写段（见下）
             │
             ▼
        CLIPTextEncode ─→ KSampler.positive
```

---

## 二、★ LLM 扩写段详图（本次新增）

```
JoinStringMulti.string_6 ──┬──→ ShowText（预览用，不影响主链）
                           │
                           ▼
                  ┌────────────────────┐
                  │  ZML_LLM_Chat      │
   model_config ──┤  (对话主程序)       │
   system_prompt ─┤                    │──→ 回复内容 ──┐
   params ────────┤  json_strategy =   │               │
                  │  「仅提示词(不强求)」│               │
                  └────────────────────┘               │
                                                       ▼
                                            ┌──────────────────────┐
                                            │ ZML_LLM 过滤思考      │
                                            │ (去 <think>...</think>)│
                                            └──────────┬───────────┘
                                                       ▼
                                            ┌──────────────────────┐
                                            │ 黑名单过滤（可选兜底） │
                                            └──────────┬───────────┘
                                                       ▼
                                            ┌──────────────────────┐
                                            │ Anima 提示词校验       │★
                                            │ (anima_validate)      │
                                            └──────────┬───────────┘
                                                       ▼
                                            CLIPTextEncode.text
                                                       ▼
                                                KSampler.positive
```

### 三个配套节点的连线

```
┌────────────────────────────┐
│ ZML_LLM 模型加载器V2        │          │ ZML_LLM 系统提示词          │
│  preset_name = SKILL-桥     │          │  system_prompt =            │
│  config_folder = 放         │          │   规则全文                  │
│   zml_model_key.json 的目录 │          │   （直连模式才需要）        │
└──────────────┬─────────────┘          └──────────────┬─────────────┘
               │ model_config                          │ system_prompt
               └──────────────┬────────────────────────┘
                              ▼
                    ZML_LLM_Chat
                              ▲
                              │ params
               ┌──────────────┴─────────────┐
               │ ZML_LLM 参数设置            │
               │  温度       = 0.7 ~ 0.9     │
               │  最大Token数 = 8192  ★必改  │
               │  核采样     = 0.9           │
               │  超时时间   = 300           │
               └────────────────────────────┘
```

---

## 三、新增节点参数一览

| 节点 | 参数 | 值 | 说明 |
|---|---|---|---|
| **ZML_LLM 模型加载器V2** | `preset_name` | `SKILL-桥` | 对应 json 里的 name |
| | `config_folder` | 放 `zml_model_key.json` 的目录 | 密钥只在这里读，不进工作流 |
| | `model_override` | 留空 | |
| **ZML_LLM 系统提示词** | `system_prompt` | 流水线扩写规则全文 | 见 `krea2_pipeline_system_prompt.txt` |
| **ZML_LLM 参数设置** | `温度` | 0.7~0.9 | 太低每次扩写雷同 |
| | `最大Token数` | **8192** | 推理模型的思考会吃掉大量 token，设小了正文会被压成空 |
| | `核采样` | 0.9 | |
| | `超时时间` | 300 | 扩写耗时长 |
| **ZML_LLM 对话主程序** | `json_strategy` | **`仅提示词 (不强求)`** | 否则会自动追加"按 JSON 输出" |
| | `user_input` | ← JoinStringMulti 输出 | |
| | 输出「回复内容」 | → 过滤思考 | |
| **ZML_LLM 过滤思考** | 无参数 | | 直通 |
| **黑名单过滤**（可选） | 黑名单 | 加 ` ```text ` / ` ``` ` | 兜底，正常不该出现 |
| **Anima 提示词校验** | `skill_dir` | `C:\Users\<你的用户名>\.dsh\skills\<你的 skill>` | 默认已填 |
| | `mode` | `扩写（不查长度）` | 扩写分支豁免 512 |
| | `on_problem` | `用修正稿` | 或改 `中断执行` 让它卡住 |

---

## 四、与已有工作流的三个接入点

| 位置 | 原来接什么 | 改成 |
|---|---|---|
| `CLIPTextEncode.text` | ← `ShowText`（JoinStringMulti 直出） | ← **Anima 提示词校验「文本」** |
| `JoinStringMulti` 输出 | 只给 `ShowText` | **分叉**：一路给 `ShowText` 预览，一路给 `ZML_LLM_Chat.user_input` |
| `KSampler.positive` | ← `CLIPTextEncode` | 不变 |

`ShowText` 保留很值得——它是你唯一能直接看到「LLM 到底收到了什么」的地方。

---

## 五、两种输入范围，自己选一个

| | 接法 | 效果 |
|---|---|---|
| **A. 全量扩写** | `JoinStringMulti` → LLM | 风格词 + tag selectors + 手写 + 反推词**一起**进长稿，输出即完整提示词，直接进 CLIP |
| **B. 只扩反推词** | `PromptCleaner` → LLM | 只扩本地反推那部分，输出还要和你的手写词/风格词再拼一次 |

三段式长稿本身是**自包含的完整提示词**，所以 **A 更顺**（一次到位，不用回拼）。选 B 的话记得在长稿之后再把风格词接回去。如果你希望「风格质量词由 LLM 按规则剔除、只保留画面描述」——那正是 A 的行为。

---

## 六、验证顺序

1. **先只接 LLM 段，不接 Control**：跑一次看 `ShowText` 收到的输入、看校验器的报告
2. 报告里出现「额外提醒」→ 说明 system prompt 没拦住，回去改 prompt
3. 报告「无问题」+ 长稿形态正确 → 再接 `CLIPTextEncode`
4. 最后叠 Control 段（先用 Depth 路，它是最稳的那条）
