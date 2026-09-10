#!/usr/bin/env python3
"""每天定时任务：联网搜索最新茶饮资讯 -> 千问提取活动 -> 抓官方海报 -> 更新 activities.json

由 .github/workflows/update_activities.yml 调用（每天 0/6/12/18 点，北京时间）。
仅依赖阿里云百炼（qwen-max 联网搜索 + 结构化提取）：
  DASHSCOPE_API_KEY
"""
import datetime
import io
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


def qwen_chat(prompt, system="你是茶饮行业情报分析师。", max_tokens=2500, search=False):
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


def fetch_page_images(page_url):
    """抓取页面所有候选图：og:image -> 第一张 <img>，返回列表（去重）"""
    cands = []
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
            cands.append(m.group(1))
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html):
            cands.append(m.group(1))
            if len(cands) >= 6:
                break
    except Exception as e:
        print("page fetch fail:", page_url, e)
    return cands


def valid_image(data):
    """校验图片是海报级别：用 Pillow 检查分辨率（>300px 且 >120k 像素）"""
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        w, h = im.size
        ok = w >= 300 and w * h >= 120000
        if not ok:
            print("rejected by size:", w, "x", h)
        return ok
    except Exception:
        return len(data) >= 30 * 1024


def download_image(img_url, out_path):
    """下载并校验图片，成功返回 True"""
    try:
        if img_url.startswith("//"):
            img_url = "https:" + img_url
        if not img_url.lower().startswith("http"):
            return False
        req = urllib.request.Request(img_url, headers=UA)
        data = urllib.request.urlopen(req, timeout=45).read()
        if data[:3] != b"\xff\xd8\xff" and data[:3] != b"\x89PN":
            print("not an image, head:", data[:8])
            return False
        if not valid_image(data):
            return False
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        with open(out_path, "wb") as f:
            f.write(data)
        return True
    except Exception as e:
        print("image download fail:", img_url[:120], e)
        return False


def try_download(candidates, out_path):
    """按候选顺序尝试下载，成功返回 True"""
    for u in candidates:
        if not u:
            continue
        u = u.strip()
        ext = ".jpg"
        mm = re.search(r"\.(png|jpe?g|webp)(\?|$)", u.lower())
        if mm:
            ext = ".png" if mm.group(1) == "png" else ".jpg"
        path = out_path + ext
        if download_image(u, path):
            return path
    return None


def main():
    bj = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(bj)
    today = now.strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 1. qwen-max 联网搜索最近动态（不设搜索时间窗口，靠 prompt + 脚本校验保证最新）
    search_prompt = (
        f"现在是{today}。请联网搜索最近 7 天内（尤其最近 3 天）中国茶饮行业品牌的最新动态："
        "新品上市、品牌联名、限定饮品、买赠优惠、门店活动等。"
        "只保留发布日期在最近 7 天内的真实新动态，历史旧闻一律不要。"
        "尽量多列（5-8 条），每条必须给出："
        "1) 品牌名 2) 活动或新品名 3) 开始/截止时间（有则写具体日期，没有写'未知'）"
        "4) 该活动的官方宣传图/海报图片直链 URL（必须 http 开头、真实可访问的图片链接，找不到写'无'）"
        "5) 资讯来源链接 URL（必须 http 开头、真实可访问的新闻/文章/公众号链接）。"
    )
    news = qwen_chat(search_prompt, search=True)
    print("== NEWS ==")
    print(news[:1000])

    # 2. qwen-max 提取结构化 JSON
    parse_prompt = (
        "根据下面的茶饮资讯，提取 3 个最值得收藏的品牌活动，输出严格的 JSON 数组（只输出 JSON，不要其他文字）：\n"
        '[{"brand":"品牌名","title":"活动名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD（资讯未给具体日期时用今天，若活动早于 7 天前开始则整体排除该条）",'
        '"endDate":"YYYY-MM-DD（未给时用 startDate 加 30 天）",'
        '"description":"不超过 40 字的官方活动介绍",'
        '"image_url":"官方宣传图/海报图片直链 URL（资讯里有就原样给出，没有填空字符串）",'
        '"source_url":"资讯来源链接 URL（必须是真实 http 链接，没有填空字符串）"}]。\n'
        f"资讯：\n{news}"
    )
    raw = qwen_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:600])
    acts = extract_json(raw)[:5]

    # 3. 逐条处理：旧闻过滤 + 去重 + 抓图
    items = []
    seen_titles = set()
    for i, a in enumerate(acts):
        title = str(a.get("title", "")).strip()
        if not title or title in seen_titles:
            continue
        # 旧闻过滤：开始日期早于 7 天前则跳过
        sd = str(a.get("startDate", "")).strip()
        try:
            start_dt = datetime.datetime.strptime(sd, "%Y-%m-%d").replace(tzinfo=bj)
            if (now - start_dt).days > 7:
                print("skip old news:", title, sd)
                continue
        except Exception:
            pass
        seen_titles.add(title)

        img_url = str(a.get("image_url", "")).strip()
        src = str(a.get("source_url", "")).strip()
        candidates = [img_url] if img_url else []
        if src:
            candidates += fetch_page_images(src)
        poster = try_download(candidates, f"images/poster_{today}_{i}")
        if not poster:
            print("skip activity (no image):", title)
            continue
        items.append({
            "brand": str(a.get("brand", "")),
            "title": title,
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": sd or today,
            "endDate": str(a.get("endDate", "")).strip() or sd or today,
            "image": RAW_BASE + "/" + poster,
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
