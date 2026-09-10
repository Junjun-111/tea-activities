#!/usr/bin/env python3
"""每天定时任务：联网搜索当天最新茶饮资讯 -> 千问提取活动 + 官方海报直链 -> 更新 activities.json

由 .github/workflows/update_activities.yml 调用（每天 0/6/12/18 点，北京时间）。
仅依赖阿里云百炼（qwen-max 联网搜索 + 结构化提取）：
  DASHSCOPE_API_KEY
"""
import datetime
import json
import os
import re
import time
import urllib.request

DASHSCOPE_KEY = os.environ["DASHSCOPE_API_KEY"]
DASHSCOPE_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"

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


def qwen_chat(prompt, system="你是茶饮行业情报分析师。", max_tokens=2000,
              search=False, start_time=None, end_time=None):
    body = {
        "model": "qwen-max",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    if search:
        body["enable_search"] = True
        body["search_options"] = {
            "forced_search": True,
            "enable_source": True,
            "search_strategy": "pro",
            "start_time": start_time,
            "end_time": end_time,
        }
    resp = _post(DASHSCOPE_CHAT, body, DASHSCOPE_KEY)
    return resp["choices"][0]["message"]["content"]


def extract_json(text):
    """从模型输出中截取第一个 JSON 数组"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def fetch_og_image(page_url):
    """访问资讯来源页，提取 og:image（兜底用；模型给直链时优先直链）"""
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
    except Exception as e:
        print("og fetch fail:", page_url, e)
    return None


def download_image(img_url, out_path):
    """下载并校验图片：>30KB 且为常见图片格式（过滤小 logo/图标），成功返回 True"""
    try:
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        if not img_url.lower().startswith("http"):
            return False
        req = urllib.request.Request(img_url, headers=UA)
        data = urllib.request.urlopen(req, timeout=45).read()
        if len(data) < 30 * 1024:
            print("image too small (maybe logo):", len(data), img_url[:120])
            return False
        if data[:3] != b"\xff\xd8\xff" and data[:3] != b"\x89PN":
            print("not an image, head:", data[:8])
            return False
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print("image download fail:", img_url[:120], e)
        return False


def main():
    bj = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(bj)
    today = now.strftime("%Y-%m-%d")
    start = (now - datetime.timedelta(days=2)).strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 1. qwen-max 联网搜索最近 3 天最新资讯（限定时间窗口，杜绝旧闻）
    search_prompt = (
        f"请联网搜索 {start} 至 {today}（最近 3 天）中国茶饮行业的最新动态："
        "品牌新品上市、联名活动、限定饮品、买赠优惠、门店活动等。"
        "只保留这 3 天内新发布/新开始的动态，历史旧闻一律不要。"
        "要求真实具体，尽量多列（3-6 条），每条必须包含："
        "1) 品牌名 2) 活动或新品名 3) 开始/截止时间（有则给，没有就标注未知）"
        "4) 该活动的官方宣传图或海报图片直链 URL（http 开头，尽量给，没有就写无）"
        "5) 资讯来源链接 URL。"
    )
    news = qwen_chat(search_prompt, search=True,
                     start_time=f"{start} 00:00:00", end_time=f"{today} 23:59:59")
    print("== NEWS ==")
    print(news[:800])

    # 2. qwen-max 提取为结构化 JSON（含官方海报直链）
    parse_prompt = (
        "根据下面的茶饮资讯，提取 3 个最值得收藏的品牌活动，输出严格的 JSON 数组（只输出 JSON，不要其他文字）：\n"
        '[{"brand":"品牌名","title":"活动名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD（资讯未给时用今天）","endDate":"YYYY-MM-DD（未给时用 startDate 加 30 天）",'
        '"description":"不超过 40 字的官方活动介绍",'
        '"image_url":"官方宣传图或海报图片直链 URL（资讯里有就原样给出，没有就填空字符串）",'
        '"source_url":"资讯来源链接 URL"}]。\n'
        f"资讯：\n{news}"
    )
    raw = qwen_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:400])
    acts = extract_json(raw)[:3]

    # 3. 逐条下载官方海报（直链优先，来源页 og:image 兜底）
    items = []
    for i, a in enumerate(acts):
        ext = ".jpg"
        url = str(a.get("image_url", "")).strip()
        mm = re.search(r"\.(png|jpe?g|webp)(\?|$)", url.lower())
        if mm:
            ext = ".png" if mm.group(1) == "png" else ".jpg"
        poster = f"images/poster_{today}_{i}{ext}"
        ok = False
        if url:
            ok = download_image(url, poster)
        if not ok:
            src = str(a.get("source_url", "")).strip()
            og = fetch_og_image(src) if src else None
            if og:
                mm = re.search(r"\.(png|jpe?g|webp)(\?|$)", og.lower())
                if mm:
                    ext = ".png" if mm.group(1) == "png" else ".jpg"
                poster = f"images/poster_{today}_{i}{ext}"
                ok = download_image(og, poster)
        if not ok:
            print("skip activity (no image):", a.get("title"))
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
