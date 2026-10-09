"""把多个「任意帧锚点」合成一个节点，内部仍然走核心的 MiniMaxH3AddGuide。

动机：核心的 AddGuide 一次只锚一帧，N 个锚点就要 N 个节点 + N 次接线 + N 个
frame_idx 手填。这个节点用「一个 batch 图 + 一行 positions」把它收成一个，
但**不引入任何补丁、不复制核心逻辑**——内部就是按顺序调用核心节点，
所以产出的 conditioning 与手工串联逐字节等价。

为什么不用社区的 H3Keyframes：它靠猴子补丁 h3_interior_patch.py 改位置计算，
而核心（PR #15439 之后）已经内建任意帧位置支持（见 comfy/ldm/minimax/model.py
里 PackedLayout 对 resolved_frame_index 的处理），补丁要解决的问题本机已不存在，
装了反而两套并存。

positions 写法（逗号分隔，按顺序与图一一对应）：
    60, 108        绝对帧号（24fps 下分别约 2.5s / 4.5s）
    2.5s, 4.5s     秒，按 fps 换算
    20%, 80%       百分比，跟着时长自动缩放，改时长不用改这里
    -1, -24        负数从尾部数（-1 是最后一帧），核心原生支持
    混着写也行：0, 50%, 3s

与图数量不等、或解析不出数字时直接报错，不会静默少锚一帧。
"""

import math

from comfy_extras import nodes_minimax_h3 as mmh3

SEPARATORS = (",", "，", ";", "；", "\n")


def parse_positions(text, frame_count, fps):
    """把 positions 字符串解析成升序帧号列表。"""
    raw = str(text or "")
    for sep in SEPARATORS[1:]:
        raw = raw.replace(sep, SEPARATORS[0])

    out = []
    for tok in raw.split(SEPARATORS[0]):
        tok = tok.strip()
        if not tok:
            continue
        try:
            if tok.endswith("%"):
                pct = float(tok[:-1])
                idx = int(round(pct / 100.0 * (frame_count - 1)))
            elif tok.lower().endswith("s"):
                idx = int(round(float(tok[:-1]) * fps))
            else:
                idx = int(round(float(tok)))
        except (ValueError, OverflowError):
            raise ValueError(
                "positions 里解析不出数字：%r（整串为 %r）。可用写法："
                "帧号 60 / 秒 2.5s / 百分比 20%% / 负数从尾部数 -1" % (tok, text))
        # 负数按核心语义从尾部数
        if idx < 0:
            idx = frame_count + idx
        if idx < 0 or idx > frame_count - 1:
            raise ValueError(
                "锚点 %r 落在视频范围外：解析成第 %d 帧，而本段只有 %d 帧"
                "（帧数由 length 决定，17k+5 网格）" % (tok, idx, frame_count))
        out.append(idx)

    if not out:
        raise ValueError("positions 是空的——至少要写一个锚点位置")

    out.sort()
    for a, b in zip(out, out[1:]):
        if a == b:
            raise ValueError(
                "两个锚点落在同一帧（第 %d 帧），请把它们分开："
                "本段共 %d 帧，一帧约等于 %.4f%%" % (a, frame_count, 100.0 / max(1, frame_count - 1)))
    return out


class AnimaH3GuideStack:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "positive": ("CONDITIONING", {"tooltip": "上游条件（ImageToVideo 或 ReferenceToVideo 的输出）"}),
                "latent": ("LATENT", {"tooltip": "同一条链的空 latent，用来推算画幅与帧数"}),
                "vae": ("VAE", ),
                "images": ("IMAGE", {"tooltip": "锚帧图。多张时用 Batch Images 合成一个 batch，按顺序对应 positions"}),
                "positions": ("STRING", {"multiline": True, "default": "20%, 80%",
                                         "tooltip": "逗号分隔，每张图一个位置。帧号 60 / 秒 2.5s / 百分比 20% / 负数从尾部数 -1，可混用"}),
            },
            "optional": {
                "fps": ("FLOAT", {"default": 24.0, "min": 0.1, "max": 240.0, "step": 0.01,
                                  "tooltip": "只有「秒」写法会用到"}),
            },
        }

    RETURN_TYPES = ("CONDITIONING", "STRING")
    RETURN_NAMES = ("positive", "报告")
    FUNCTION = "apply"
    CATEGORY = "anima/h3"

    def apply(self, positive, latent, vae, images, positions, fps=24.0):
        # 帧数用 latent 反推，算法与 MiniMaxH3AddGuide.execute 内部完全一致，
        # 这样越界校验用的 frame_count 与核心节点看到的是同一个数
        video = latent["samples"].tensors[0]
        frame_count = sum(mmh3.FRAME_PER_TOKEN[k % 5] for k in range(video.shape[2]))

        n_img = int(images.shape[0]) if images is not None and hasattr(images, "shape") else 0
        if n_img == 0:
            raise ValueError("images 是空的，至少要接一张锚帧图")

        idxs = parse_positions(positions, frame_count, float(fps))
        if len(idxs) != n_img:
            raise ValueError(
                "图与位置数量不一致：接了 %d 张图，positions 里写了 %d 个位置（%r）。"
                "两者必须一一对应，且顺序相同。" % (n_img, len(idxs), positions))

        cond = positive
        notes = []
        for i, (idx, img) in enumerate(zip(idxs, images)):
            cond, = mmh3.MiniMaxH3AddGuide.execute(
                positive=cond, latent=latent, frame_idx=int(idx), vae=vae,
                image=img.unsqueeze(0),
            )
            notes.append("%d(%.2fs)" % (idx, idx / float(fps)))

        report = ("%d 帧（%.2fs）｜锚了 %d 个点：%s"
                  % (frame_count, frame_count / float(fps), len(idxs), ", ".join(notes)))
        print("[AnimaH3GuideStack] " + report, flush=True)
        return (cond, report)


NODE_CLASS_MAPPINGS = {"AnimaH3GuideStack": AnimaH3GuideStack}
NODE_DISPLAY_NAME_MAPPINGS = {"AnimaH3GuideStack": "Anima H3 锚帧堆（多锚点合一）"}
