#!/usr/bin/env python3
"""每天定时任务：千问联网搜索最新茶饮资讯 -> 提取活动 -> 必应定位文章页抓官方配图 -> 更新 activities.json

由 .github/workflows/update_activities.yml 调用（每天 0/6/12/18 点，北京时间）。
搜索与提取全部由千问（qwen-max）完成；图片从文章页抓取官方宣传图。
依赖环境变量：
  DASHSCOPE_API_KEY
"""
import datetime
import io
import json
import os
import re
import urllib.parse
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
        body["search_options"] = {"forced_search": True, "search_strategy": "pro"}
    resp = _post(DASHSCOPE_CHAT, body, DASHSCOPE_KEY)
    return resp["choices"][0]["message"]["content"]


def extract_json(text):
    """从模型输出中截取第一个 JSON 数组"""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def bing_search(query, n=3):
    """必应搜索，返回前 n 个外部文章链接（过滤必应/微软内部链接）"""
    links = []
    try:
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers=UA)
        html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
        for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"', html):
            u = m.group(1)
            low = u.lower()
            if any(x in low for x in ("bing.com", "microsoft.com", "msn.com",
                                      "go.microsoft", "r.msn", "bingj.com")):
                continue
            if u not in links:
                links.append(u)
            if len(links) >= n:
                break
    except Exception as e:
        print("bing search fail:", query, e)
    return links


def fetch_page_images(page_url):
    """抓取页面候选图：og:image -> 前几张 <img>，返回去重列表"""
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
    """海报级别校验：分辨率 >300px 且 >120k 像素（过滤小 logo/图标）"""
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
    """按候选顺序尝试下载，成功返回保存路径"""
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

    # 1. 千问联网搜索最近动态（搜索+提取全部交给千问）
    search_prompt = (
        f"现在是{today}。请联网搜索最近 7 天内（尤其最近 3 天）中国茶饮行业品牌的最新动态："
        "新品上市、品牌联名、限定饮品、买赠优惠、门店活动等。"
        "只保留发布日期在最近 7 天内的真实新动态，历史旧闻一律不要。"
        "尽量多列（5-8 条），每条包含：品牌名、活动或新品名、开始/截止时间（有则给具体日期）。"
    )
    news = qwen_chat(search_prompt, search=True)
    print("== NEWS ==")
    print(news[:1000])

    # 2. 千问提取结构化 JSON
    parse_prompt = (
        "根据下面的茶饮资讯，提取 3 个最值得收藏的品牌活动，输出严格的 JSON 数组（只输出 JSON，不要其他文字）：\n"
        '[{"brand":"品牌名","title":"活动名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD（资讯未给具体日期时用今天，若活动早于 7 天前开始则整体排除该条）",'
        '"endDate":"YYYY-MM-DD（未给时用 startDate 加 30 天）",'
        '"description":"不超过 40 字的官方活动介绍"}]。\n'
        f"资讯：\n{news}"
    )
    raw = qwen_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:600])
    acts = extract_json(raw)[:5]

    # 3. 逐条处理：旧闻过滤 + 去重 + 必应定位文章页抓官方配图
    items = []
    seen_titles = set()
    for i, a in enumerate(acts):
        title = str(a.get("title", "")).strip()
        brand = str(a.get("brand", "")).strip()
        if not title or title in seen_titles:
            continue
        sd = str(a.get("startDate", "")).strip()
        try:
            start_dt = datetime.datetime.strptime(sd, "%Y-%m-%d").replace(tzinfo=bj)
            if (now - start_dt).days > 7:
                print("skip old news:", title, sd)
                continue
        except Exception:
            pass
        seen_titles.add(title)

        # 用必应找该活动的文章页，抓官方配图
        candidates = []
        for page in bing_search(f"{brand} {title}", n=3):
            candidates += fetch_page_images(page)
        if not candidates:
            print("skip activity (no page found):", title)
            continue
        poster = try_download(candidates, f"images/poster_{today}_{i}")
        if not poster:
            print("skip activity (no image):", title)
            continue
        items.append({
            "brand": brand,
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
