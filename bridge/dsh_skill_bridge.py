"""DSH skill 桥：把 ~/.dsh/skills 里的 skill 规则动态喂给任意 OpenAI 兼容客户端。

它不产生规则，只做三件事：
  1. 看你传进来的 system_prompt 里出现了哪个「场景关键词」，据此挑 skill 与参考文档
  2. 把 skill 规则 + 通用流水线覆盖层 拼成真正的 system prompt
  3. 转发给上游（任何 OpenAI 兼容服务），原样回传

所以换底模（krea2 / anima / sdxl / minimax）时你只改一句声明，规则本身不用碰。
校验不在这里做——交给 ComfyUI 的 anima_validate 节点。
"""

import collections
import hashlib
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "bridge_config.json")

_cache = {}
_cache_lock = threading.Lock()

# ── 回复缓存 ─────────────────────────────────────────────────────────────
# 抽卡场景（同一张图 + 同一串 tag，换随机种子反复跑）里，每次请求的 system prompt
# 都是同一份两万多字的规则，无条件重发等于每次都白烧一遍上下文、还多等十几秒。
# 这里按「最终送出的 system + 用户文本 + 模型 + token 上限」建键，命中就直接回放上次
# 的正文，不碰上游。
#
# 图像默认不进键：CN 图生图的 tag 与图基本是硬绑定的，图稍变 tag 就会变，用 tag 判断
# 足够。要更严谨可以把 include_image_in_key 打开。
_reply_cache = collections.OrderedDict()
_cache_guard = threading.Lock()


def cache_key(system_prompt, user_text, model, max_tokens, image_fingerprint=""):
    blob = "\x00".join([system_prompt or "", user_text or "", model or "",
                        str(max_tokens), image_fingerprint])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def cache_lookup(key):
    with _cache_guard:
        hit = _reply_cache.get(key)
        if hit is not None:
            _reply_cache.move_to_end(key)
        return hit


def cache_store(key, content, usage, limit):
    with _cache_guard:
        _reply_cache[key] = (content, usage)
        _reply_cache.move_to_end(key)
        while len(_reply_cache) > max(1, limit):
            _reply_cache.popitem(last=False)


def cache_size():
    with _cache_guard:
        return len(_reply_cache)

_cfg_cache = {"mtime": None, "cfg": None}
_cfg_lock = threading.Lock()

LOG_PATH = os.path.join(HERE, "bridge.log")


def current_config():
    """按 mtime 热重载配置。

    只有监听端口必须重启才能改；upstream / routes / overrides 改了立刻生效。
    以前配置只在启动时读一次，改完忘了重启就会一直连旧地址，症状是 10061
    「目标计算机积极拒绝」——看起来像上游挂了，其实桥拿着过期配置。
    """
    try:
        mtime = os.path.getmtime(CONFIG_PATH)
    except OSError:
        mtime = None
    with _cfg_lock:
        if mtime is not None and _cfg_cache["mtime"] == mtime and _cfg_cache["cfg"]:
            return _cfg_cache["cfg"]
    cfg = load_config()
    with _cfg_lock:
        _cfg_cache["mtime"] = mtime
        _cfg_cache["cfg"] = cfg
    return cfg


def log(msg):
    """同时写日志文件与控制台。

    pythonw 启动时没有控制台，日志文件是唯一的观测窗口，所以两条路都要走，
    且任一条失败都不能影响服务本身。
    """
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    try:
        if sys.stdout is not None:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
    except Exception:
        pass


def load_config():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


def read_cached(path):
    """按 mtime 缓存；skill 改动后自动重新读。"""
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return None
    with _cache_lock:
        hit = _cache.get(path)
        if hit and hit[0] == mtime:
            return hit[1]
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    with _cache_lock:
        _cache[path] = (mtime, text)
    return text


def pick_route(user_system, routes, default_route):
    """场景关键词命中哪个就用哪个；命中最长者优先，避免 'anima' 抢走 'anima3'。

    route 的 key 可用 | 分隔多个别名（如 'minimax|minimax fl2va|minimax帧锚定模式'）：
    任一别名命中即算该 route 命中，比较长度时取实际命中的那个别名。
    这样同一份场景配置可以挂多个触发词，不必复制整块配置。
    """
    lowered = (user_system or "").lower()
    best, best_len = None, 0
    for k in routes:
        for alias in str(k).split("|"):
            alias = alias.strip().lower()
            if alias and alias in lowered and len(alias) > best_len:
                best, best_len = k, len(alias)
    if best is None:
        return default_route, None
    return best, None


def has_image(messages):
    """ZML 的图像是以 OpenAI 多模态 content 数组里的 image_url 传进来的。"""
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def extract_text(messages):
    """抽出消息里的文本部分。content 可能是字符串，也可能是 OpenAI 多模态数组。"""
    out = []
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, str):
            out.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    out.append(part.get("text") or "")
    return "\n".join(t for t in out if t)


def image_fingerprint(messages):
    """图片数据的指纹，供 include_image_in_key 模式使用。"""
    h = hashlib.sha256()
    for m in messages or []:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    h.update(((part.get("image_url") or {}).get("url") or "").encode("utf-8", "ignore"))
    return h.hexdigest()[:16]


# 两段「本次带图」的注入说明。配置里的同名字段优先，这里只是兜底默认值：
# 措辞属于使用偏好，放配置里改起来不必碰 Python。
DEFAULT_IMAGE_GUIDANCE_IMAGE_ONLY = (
    "# ===== 本次输入：只有图像，没有打标产物 =====\n"
    "按上面 skill 的反推分支处理这张图：先打标，再回图核对，最后组装输出。")

DEFAULT_IMAGE_GUIDANCE_WITH_TAGS = (
    "# ===== 本次输入：图像 + 已确认的打标产物 =====\n"
    "tag 是打标产物，直接当已确认的锚点用，不要重新打标、不要推翻它。\n"
    "但图像才是本次扩写的基准（skill 原文未写此条，此处补齐）：\n"
    "  - 画面里正在发生什么动作、人物朝向与视线方向、身体姿态、四肢位置，一律照图写；\n"
    "  - 构图与机位（景别、视角、主体在画幅中的位置与占幅、前中后景分层、被什么遮住）照图写；\n"
    "  - 场景、道具、光源来向与阴影落点照图写；\n"
    "  - tag 只负责钉住图上看不出精确取值的离散特征（发色、瞳色、服装款式、配饰）。\n"
    "tag 与图冲突时以 tag 为准，但不要写出与图矛盾的内容。\n"
    "这张图会作为 ControlNet 控制图与本次提示词配对做图生图，"
    "所以提示词必须描述图里实际发生的事，否则两者会互相拉扯。")


def build_system_prompt(cfg, user_system, with_image=False, user_text=""):
    routes = cfg.get("routes") or {}
    key, _ = pick_route(user_system, routes, cfg.get("default_route"))
    route = routes.get(key)
    if not route:
        return ("[桥] 没有匹配到场景（%s），且默认路由 %r 不存在。"
                % (key, cfg.get("default_route"))), key, None, []

    skill_dir = os.path.join(cfg["skills_dir"], route["skill"])
    parts, missing = [], []

    # 覆盖层可以按路由单独写（route 级优先），缺省回落全局配置。
    # 图像流程（tag 流 + 三段式长稿）与视频流程（单段官方格式）的形态要求完全不同，
    # 用一个全局覆盖层没法同时伺候两边。
    overrides = (route.get("pipeline_overrides") or cfg.get("pipeline_overrides") or "").strip()
    if overrides:
        parts.append(overrides)

    skill_md = read_cached(os.path.join(skill_dir, "SKILL.md"))
    if skill_md is None:
        missing.append("SKILL.md")
    else:
        parts.append("# ===== skill 规则：%s =====\n%s" % (route["skill"], skill_md))

    # 分支选择：只有「带图且没给打标文本」才切到反推；
    # 图 + tag 属于"打标产物已就绪"，继续走主 refs（扩写），把图当视觉参考。
    has_tags = bool((user_text or "").strip())
    image_only = with_image and not has_tags
    refs = route.get("refs") or []
    if image_only and route.get("refs_image_only"):
        refs = route["refs_image_only"]
    for ref in refs:
        text = read_cached(os.path.join(skill_dir, "references", ref))
        if text is None:
            missing.append("references/" + ref)
        else:
            parts.append("# ===== 参考：%s =====\n%s" % (ref, text))

    # 同上：带图说明也支持 route 级覆盖（视频场景里图是首帧，不是 ControlNet 控制图）。
    if image_only:
        parts.append(route.get("image_guidance_image_only")
                     or cfg.get("image_guidance_image_only")
                     or DEFAULT_IMAGE_GUIDANCE_IMAGE_ONLY)
    elif with_image and has_tags:
        parts.append(route.get("image_guidance_with_tags")
                     or cfg.get("image_guidance_with_tags")
                     or DEFAULT_IMAGE_GUIDANCE_WITH_TAGS)

    if (user_system or "").strip():
        parts.append("# ===== 本次场景声明（优先级最高）=====\n%s" % user_system.strip())

    return "\n\n".join(parts), key, route, missing


def pick_model(cfg, route, requested):
    """上游模型优先级：请求指定 > 场景指定 > 配置默认。

    advertised_model（默认 skill-bridge）是占位名，出现它等于「没指定」，
    于是 ZML 面板里选它就吃场景配置、选真实模型名就当场压过配置。
    """
    default_model = (cfg.get("upstream") or {}).get("model")
    advertised = cfg.get("advertised_model") or "skill-bridge"
    if requested and requested != advertised:
        return requested, "请求指定"
    if route and route.get("model"):
        return route["model"], "场景指定"
    return default_model, "配置默认"


def forward(cfg, messages, extra, model):
    up = cfg["upstream"]
    key = up.get("api_key") or ""
    # HTTP header 只能存 latin-1，占位符里的中文会在这里炸得莫名其妙，提前拦掉
    if not key or "REPLACE_ME" in key or any(ord(c) > 127 for c in key):
        return None, ("上游 api_key 不可用：bridge_config.json 的 api_key 仍是占位符，"
                      "或含非 ASCII 字符（当前 %d 字符）。请在 bridge_config.json 的 api_key 填入你的密钥。"
                      % len(key))
    if not model:
        return None, "没有可用模型：route 没配 model，请求没带，upstream.model 也是空的。"
    body = {"model": model, "messages": messages, "stream": False}
    # 白名单转发采样参数。注意 max_completion_tokens 是新版 openai SDK 的正式字段名
    # （max_tokens 已弃用），漏掉它会让面板上设置的"最大Token数"静默失效——
    # 上游退回自己的默认值，推理模型的思考一吃光，正文就是空的。
    for k in ("temperature", "top_p", "max_tokens", "max_completion_tokens",
              "presence_penalty", "frequency_penalty", "seed", "stop",
              "n", "logit_bias", "response_format", "stream_options"):
        if k in extra and extra[k] is not None:
            body[k] = extra[k]

    req = urllib.request.Request(
        up["base_url"].rstrip("/") + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + (up.get("api_key") or "")},
        method="POST",
    )
    timeout = int(cfg.get("timeout") or 600)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8")), None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")
        return None, "上游 HTTP %d：%s" % (e.code, detail[:800])
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        blob = "%s %s" % (reason, e)
        if (isinstance(reason, ConnectionRefusedError)
                or "10061" in blob or "积极拒绝" in blob):
            return None, ("连不上上游 %s —— 目标端口没有在监听（WinError 10061）。"
                          "通常是上游网关没启动，或者 bridge_config.json 里的 base_url 写错了。"
                          % up["base_url"])
        return None, "上游请求失败：%s" % e
    except Exception as e:
        return None, "上游请求失败：%s" % e


class QuietServer(ThreadingHTTPServer):
    """客户端读完就断开是常态（ComfyUI / openai SDK 都这样），
    默认的 handle_error 会为此打一大段堆栈，纯属噪音，静默掉。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log("%s" % (fmt % args))

    def _send(self, code, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_sse(self, content, model):
        """把完整正文包装成 SSE 流发回。

        ZML_LLM_Chat 用 stream=True 调用，只认 text/event-stream；直接回普通 JSON
        时 openai SDK 会静默给出 0 个 chunk，于是上游明明有内容、下游却收到空串。
        上游这一步我们仍用非流式（拿全量后再切块），所以这里 Content-Length 可以算准。
        """
        cid = "chatcmpl-bridge"
        created = int(time.time())

        def frame(delta, finish=None):
            return {"id": cid, "object": "chat.completion.chunk", "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}

        parts = [
            "data: " + json.dumps(frame({"role": "assistant", "content": ""}), ensure_ascii=False),
            "data: " + json.dumps(frame({"content": content}), ensure_ascii=False),
            "data: " + json.dumps(frame({}, "stop"), ensure_ascii=False),
            "data: [DONE]",
        ]
        data = ("\n\n".join(parts) + "\n\n").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{
                "id": current_config().get("advertised_model") or "skill-bridge",
                "object": "model", "owned_by": "dsh-skill-bridge"}]})
            return
        self._send(404, {"error": {"message": "只有 /v1/models 和 /v1/chat/completions"}})

    def _reply(self, content, usage, payload, cfg):
        """把正文回给调用方：流式走 SSE，非流式走普通 JSON。缓存命中也走这里。"""
        if payload.get("stream"):
            self._send_sse(content, cfg.get("advertised_model") or "skill-bridge")
            return
        self._send(200, {
            "id": "chatcmpl-bridge", "object": "chat.completion",
            "created": int(time.time()),
            "model": (cfg.get("advertised_model") or "skill-bridge"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": usage or {},
        })

    def do_POST(self):
        cfg = current_config()
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send(404, {"error": {"message": "只有 /v1/chat/completions"}})
            return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as e:
            self._send(400, {"error": {"message": "请求体不是合法 JSON：%s" % e}})
            return

        src = payload.get("messages") or []
        user_system = "\n".join(m.get("content", "") for m in src if m.get("role") == "system")
        user_msgs = [m for m in src if m.get("role") != "system"]
        if not user_msgs:
            self._send(400, {"error": {"message": "缺少 user 消息"}})
            return

        with_image = has_image(user_msgs)
        user_text = extract_text(user_msgs)
        system_prompt, route_key, route, missing = build_system_prompt(
            cfg, user_system, with_image, user_text)
        if missing:
            log("警告：缺文件 %s" % ", ".join(missing))

        model, model_from = pick_model(cfg, route, payload.get("model"))
        req_mt = payload.get("max_completion_tokens") or payload.get("max_tokens") or "未传"
        messages = [{"role": "system", "content": system_prompt}] + user_msgs
        # 抽卡时同一份 system + 同一串 tag 会反复来，命中就直接回放，不碰上游
        if with_image and not user_text.strip():
            inp, branch = "仅图(无tag)", "反推"
        elif with_image:
            inp, branch = "图+tag(%d字)" % len(user_text), "扩写(图当参考)"
        else:
            inp, branch = "tag(%d字)" % len(user_text), "扩写"

        ccfg = cfg.get("cache") or {}
        ck = None
        if ccfg.get("enabled"):
            fp = image_fingerprint(user_msgs) if ccfg.get("include_image_in_key") else ""
            ck = cache_key(system_prompt, user_text, model, req_mt, fp)
            hit = cache_lookup(ck)
            if hit is not None:
                content, usage = hit
                log("场景=%s  输入=%s  分支=%s  模型=%s  命中缓存  直接回放 %d 字"
                    "（未调用上游；缓存共 %d 条）"
                    % (route_key, inp, branch, model, len(content), cache_size()))
                self._reply(content, usage, payload, cfg)
                return

        started = time.time()
        result, error = forward(cfg, messages, payload, model)

        if error:
            log("场景=%s 失败：%s" % (route_key, error))
            self._send(502, {"error": {"message": error}})
            return

        try:
            content = result["choices"][0]["message"]["content"]
        except Exception:
            self._send(502, {"error": {"message": "上游返回结构异常：%s"
                                       % json.dumps(result, ensure_ascii=False)[:600]}})
            return

        if not (content or "").strip():
            # 空正文最常见的成因：请求没带 max_tokens/max_completion_tokens，
            # 上游退回自己的小默认值，被推理模型的思考全部吃掉。
            usage0 = result.get("usage") or {}
            r0 = (usage0.get("completion_tokens_details") or {}).get("reasoning_tokens")
            hint = ("上游返回了空正文。"
                    + (("本次思考消耗 %s tokens。" % r0) if r0 else "")
                    + " 常见原因：请求未带 max_tokens/max_completion_tokens，"
                      "或该值太小被思考吃光。请把 ZML_LLM 参数设置的「最大Token数」调到 8192。")
            log("警告：上游空正文  思考=%s  completion=%s  请求带的 max_tokens=%s"
                % (r0, usage0.get("completion_tokens"), req_mt))
            self._send(502, {"error": {"message": hint}})
            return

        usage = result.get("usage") or {}
        rtok = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
        log("场景=%s  输入=%s  分支=%s  模型=%s(%s)  system %d 字  max_tokens=%s  "
            "completion=%s  思考=%s  用掉 %.1fs"
            % (route_key, inp, branch, model, model_from,
               len(system_prompt), req_mt, usage.get("completion_tokens"),
               rtok, time.time() - started))

        if ck is not None:
            cache_store(ck, content, usage, int(ccfg.get("max_entries") or 64))

        self._reply(content, usage, payload, cfg)


def preflight(cfg):
    """启动时把每条 route 的 skill 落地情况摊开。

    路径填错时请求仍会成功（只是缺规则），失败太安静——用户只会觉得"输出变差了"。
    所以在启动时就把缺失项列清楚，而不是等 200 响应里夹一条埋在日志深处的警告。
    """
    sd = cfg.get("skills_dir") or ""
    log("skills_dir = %s" % (sd or "(未配置)"))
    if not sd:
        log("!! skills_dir 没配，桥只会发覆盖层，skill 规则全部缺失")
        return
    if not os.path.isdir(sd):
        log("!! skills_dir 不存在：%s" % sd)
        log("   请把它改成你放 skill 的目录；改完不必重启桥（配置是热重载的）")
        return

    for k, r in (cfg.get("routes") or {}).items():
        skill = r.get("skill") or ""
        d = os.path.join(sd, skill)
        bad = []
        if not os.path.isfile(os.path.join(d, "SKILL.md")):
            bad.append("SKILL.md")
        for key in ("refs", "refs_image_only"):
            for ref in r.get(key) or []:
                if not os.path.isfile(os.path.join(d, "references", ref)):
                    bad.append("references/" + ref)
        log("  route %-10s -> %-18s %s"
            % (k, skill, "OK" if not bad else "缺失 " + ", ".join(sorted(set(bad)))))


def main():
    cfg = load_config()
    port = int(cfg.get("port") or 8899)
    server = QuietServer(("127.0.0.1", port), Handler)
    log("=" * 58)
    log("监听 http://127.0.0.1:%d/v1" % port)
    log("上游 %s   兜底模型 %s" % (cfg["upstream"]["base_url"], cfg["upstream"]["model"]))
    log("场景路由 %s" % ", ".join((cfg.get("routes") or {}).keys()))
    preflight(cfg)
    log("ComfyUI 的 ZML_LLM 系统提示词里只写一句场景声明即可，例如：krea2 扩写")
    log("日志文件 %s" % LOG_PATH)
    log("=" * 58)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("退出")


if __name__ == "__main__":
    # pythonw 启动时没有控制台，sys.stdout 可能是 None，直接 reconfigure 会抛异常
    if sys.stdout is not None:
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    main()
