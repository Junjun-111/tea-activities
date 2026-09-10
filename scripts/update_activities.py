#!/usr/bin/env python3
"""每天定时任务：搜索当天茶饮资讯 -> 提取活动 -> 生成海报 -> 更新 activities.json

由 .github/workflows/update_activities.yml 调用（每天 0/6/12/18 点，北京时间）。
依赖环境变量：
  DASHSCOPE_API_KEY  阿里云百炼 key（qwen-max 联网搜索 + 通义万相文生图）
  DEEPSEEK_API_KEY   DeepSeek key（提取结构化活动 + 文案）
"""
import json
import os
import time
import urllib.request

DASHSCOPE_KEY = os.environ["DASHSCOPE_API_KEY"]
DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]

DASHSCOPE_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DASHSCOPE_T2I = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
DASHSCOPE_TASK = "https://dashscope.aliyuncs.com/api/v1/tasks/"
DEEPSEEK_CHAT = "https://api.deepseek.com/chat/completions"

RAW_BASE = "https://raw.githubusercontent.com/Junjun-111/tea-activities/main/images"


def _post(url, body, key, timeout=90):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + key,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def qwen_chat(prompt, system="你是茶饮行业情报分析师。", enable_search=False, max_tokens=1200):
    resp = _post(DASHSCOPE_CHAT, {
        "model": "qwen-max",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "enable_search": enable_search,
    }, DASHSCOPE_KEY)
    return resp["choices"][0]["message"]["content"]


def deepseek_chat(prompt, max_tokens=2000):
    resp = _post(DEEPSEEK_CHAT, {
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }, DEEPSEEK_KEY)
    return resp["choices"][0]["message"]["content"]


def gen_poster(prompt, out_path):
    """通义万相文生图（异步任务轮询），成功返回 True"""
    resp = _post(DASHSCOPE_T2I, {
        "model": "wanx2.1-t2i-turbo",
        "input": {"prompt": prompt},
        "parameters": {"size": "768*1024", "n": 1},
    }, DASHSCOPE_KEY, timeout=120)
    task_id = resp["output"]["task_id"]
    for _ in range(40):
        time.sleep(6)
        st = _post(DASHSCOPE_TASK + task_id, {}, DASHSCOPE_KEY, timeout=60)
        status = st["output"]["task_status"]
        if status == "SUCCEEDED":
            img_url = st["output"]["results"][0]["url"]
            with urllib.request.urlopen(img_url, timeout=90) as r:
                data = r.read()
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "wb") as f:
                f.write(data)
            return True
        if status == "FAILED":
            print("poster failed:", st)
            return False
    return False


def extract_json(text):
    """从模型输出中截取第一个 JSON 数组"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def main():
    today = time.strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 1. qwen-max 联网搜索当天真实茶饮资讯
    search_prompt = (
        f"今天是{today}。请联网搜索最近 3 天内中国茶饮行业（喜茶、霸王茶姬、瑞幸、蜜雪冰城、"
        "茶百道、古茗、沪上阿姨、奈雪的茶、一点点、书亦烧仙草等）的真实最新动态：新品上市、"
        "品牌联名、限定活动、买赠优惠等。要求信息真实具体，至少列出 5 条，"
        "每条包含品牌名、活动/产品名、开始或截止时间（如有）。"
    )
    news = qwen_chat(search_prompt, enable_search=True, max_tokens=1500)
    print("== NEWS ==")
    print(news[:500])

    # 2. DeepSeek 提取 3 个结构化活动（含海报提示词）
    parse_prompt = (
        "根据下面的茶饮资讯，提取 3 个最值得收藏的活动，输出严格的 JSON 数组（只输出 JSON，不要其他文字）：\n"
        '[{"brand":"品牌名","title":"活动名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD（资讯未给时用今天）","endDate":"YYYY-MM-DD（未给时用 startDate 加 30 天）",'
        '"description":"不超过 40 字的官方活动介绍","poster_prompt":"统一风格的竖版茶饮活动海报提示词：'
        '包含主视觉主体、品牌 logo 文字占位、活动标题文字占位、活动时间文字占位，色彩明快符合品牌调性"}]。\n'
        f"资讯：\n{news}"
    )
    raw = deepseek_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:300])
    acts = extract_json(raw)[:3]

    # 3. 逐条生成海报，失败则跳过该条
    items = []
    for i, a in enumerate(acts):
        poster = f"images/poster_{today}_{i}.png"
        ok = gen_poster(a.get("poster_prompt", "茶饮新品活动海报"), poster)
        if not ok:
            continue
        items.append({
            "brand": str(a.get("brand", "")),
            "title": str(a.get("title", "")),
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": str(a.get("startDate", today)),
            "endDate": str(a.get("endDate", today)),
            "image": f"{RAW_BASE}/poster_{today}_{i}.png",
            "description": str(a.get("description", "")),
            "ratio": 1.2,
        })

    if not items:
        print("no activities generated, keep old data")
        return

    with open("data/activities.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": today, "activities": items}, f, ensure_ascii=False, indent=2)
    print("OK activities:", len(items))


if __name__ == "__main__":
    main()
