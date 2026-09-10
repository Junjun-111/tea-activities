#!/usr/bin/env python3
"""每天定时任务：搜索当天茶饮资讯 -> 提取活动 -> 抓取官方配图 -> 更新 activities.json

由 .github/workflows/update_activities.yml 调用（每天 0/6/12/18 点，北京时间）。
依赖环境变量：
  DASHSCOPE_API_KEY  阿里云百炼 key（qwen-max 联网搜索）
  DEEPSEEK_API_KEY   DeepSeek key（提取结构化活动）
"""
import json
import os
import re
import time
import urllib.request

DASHSCOPE_KEY = os.environ["DASHSCOPE_API_KEY"]
DEEPSEEK_KEY = os.environ["DEEPSEEK_API_KEY"]

DASHSCOPE_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
DEEPSEEK_CHAT = "https://api.deepseek.com/chat/completions"

RAW_BASE = "https://raw.githubusercontent.com/Junjun-111/tea-activities/main/images"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


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


def qwen_chat(prompt, system="你是茶饮行业情报分析师。", enable_search=False, max_tokens=1500):
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


def extract_json(text):
    """从模型输出中截取第一个 JSON 数组"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def fetch_og_image(page_url):
    """访问资讯来源页，提取 og:image（或第一张图片）"""
    try:
        req = urllib.request.Request(page_url, headers=UA)
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']',
            html,
        )
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
                html,
            )
        if m:
            return m.group(1)
        m = re.search(r'<img[^>]+src=["\']([^"\']+)', html)
        if m:
            return m.group(1)
    except Exception as e:
        print("og fetch fail:", page_url, e)
    return None


def download_image(img_url, out_path):
    """下载图片并校验（扩展名、大小、魔数），成功返回 True"""
    try:
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        if not img_url.lower().startswith("http"):
            return False
        req = urllib.request.Request(img_url, headers=UA)
        data = urllib.request.urlopen(req, timeout=45).read()
        if len(data) < 10 * 1024:
            print("image too small:", len(data))
            return False
        if not data[:3] in (b"\xff\xd8\xff", b"\x89PN") and data[:2] != b"BM":
            print("not an image, head:", data[:8])
            return False
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print("image download fail:", img_url, e)
        return False


def main():
    today = time.strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 1. qwen-max 联网搜索当天真实茶饮资讯（要求带来源链接）
    search_prompt = (
        f"今天是{today}。请联网搜索最近 3 天内中国茶饮行业（喜茶、霸王茶姬、瑞幸、蜜雪冰城、"
        "茶百道、古茗、沪上阿姨、奈雪的茶、一点点、书亦烧仙草等）的真实最新动态：新品上市、"
        "品牌联名、限定活动、买赠优惠等。要求：信息真实具体，至少列出 5 条，"
        "每条包含：品牌名、活动/产品名、开始或截止时间（如有）、以及该条资讯的来源链接 URL。"
    )
    news = qwen_chat(search_prompt, enable_search=True, max_tokens=2000)
    print("== NEWS ==")
    print(news[:600])

    # 2. DeepSeek 提取 3 个结构化活动（含来源链接）
    parse_prompt = (
        "根据下面的茶饮资讯，提取 3 个最值得收藏的品牌活动，输出严格的 JSON 数组（只输出 JSON，不要其他文字）：\n"
        '[{"brand":"品牌名","title":"活动名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD（资讯未给时用今天）","endDate":"YYYY-MM-DD（未给时用 startDate 加 30 天）",'
        '"description":"不超过 40 字的官方活动介绍","source_url":"该条资讯的来源链接 URL（必须是真实存在的 http/https 链接）"}]。\n'
        f"资讯：\n{news}"
    )
    raw = deepseek_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:300])
    acts = extract_json(raw)[:3]

    # 3. 逐条抓取官方配图，成功才写入
    items = []
    for i, a in enumerate(acts):
        page = str(a.get("source_url", "")).strip()
        img = fetch_og_image(page) if page else None
        if not img:
            print("no og image for", a.get("title"))
            continue
        ext = ".jpg"
        mm = re.search(r"\.(png|jpe?g|webp)(\?|$)", img.lower())
        if mm:
            ext = ".png" if mm.group(1) == "png" else ".jpg"
        poster = f"images/poster_{today}_{i}{ext}"
        if not download_image(img, poster):
            continue
        items.append({
            "brand": str(a.get("brand", "")),
            "title": str(a.get("title", "")),
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": str(a.get("startDate", today)),
            "endDate": str(a.get("endDate", today)),
            "image": f"{RAW_BASE}/poster_{today}_{i}{ext}",
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
