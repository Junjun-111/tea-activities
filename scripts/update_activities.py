#!/usr/bin/env python3
"""每天定时任务：抓饮品报最新文章 -> 千问提取活动 -> 抓官方配图 -> 更新 activities.json

数据源：饮品报移动版 m.drinknewspaper.com（茶饮行业垂直媒体，日更，文章配图多为品牌官方宣传图）。
搜索与提取全部由千问（qwen-max）完成，不再使用必应/DeepSeek。
依赖环境变量：DASHSCOPE_API_KEY
"""
import datetime
import gzip
import io
import json
import os
import re
import urllib.request

DASHSCOPE_KEY = os.environ["DASHSCOPE_API_KEY"]
DASHSCOPE_CHAT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
RAW_BASE = "https://raw.githubusercontent.com/Junjun-111/tea-activities/main/images"

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
LIST_URL = "http://m.drinknewspaper.com/"
ART_URL = "http://m.drinknewspaper.com/news/{id}.html"


def _get(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": MOBILE_UA, "Accept-Encoding": "gzip"},
    )
    data = urllib.request.urlopen(req, timeout=timeout).read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def _post(url, body, timeout=90):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + DASHSCOPE_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def qwen_chat(prompt, system="你是茶饮行业情报分析师。", max_tokens=2500):
    body = {
        "model": "qwen-max",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
    }
    return _post(DASHSCOPE_CHAT, body)["choices"][0]["message"]["content"]


def extract_json(text):
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def fetch_news_list():
    """抓列表页，返回 [{id,title,href,cover}]（按页面出现顺序）"""
    html = _get(LIST_URL).decode("utf-8", "ignore")
    items = {}
    for m in re.finditer(r'newsId="(\d+)"[^>]*newsName="([^"]*)"', html):
        nid, title = m.group(1), m.group(2)
        if not title.strip():
            continue
        seg = html[m.end():m.end() + 2500]
        href = re.search(r'href="([^"]+?/news/\d+\.html)"', seg)
        img = re.search(r'src-original="(//[^"]+|https?://[^"]+)"', seg)
        items.setdefault(nid, {
            "id": nid,
            "title": title.strip(),
            "href": href.group(1) if href else ART_URL.format(id=nid),
            "cover": img.group(1) if img else None,
        })
    return list(items.values())[:10]


def fetch_article(art):
    """抓文章页：补充发布时间与正文图片列表"""
    try:
        html = _get(art["href"]).decode("utf-8", "ignore")
    except Exception as e:
        print("article fetch fail:", art["href"], e)
        return art
    mt = re.search(r'(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}', html)
    if mt:
        art["date"] = "%s/%s/%s" % (mt.group(1), mt.group(2), mt.group(3))
    imgs = []
    for m in re.finditer(
            r'(?:src|src-original)="(//[^"]+\.(?:jpg|jpeg|png|webp|gif)[^"]*|https?://[^"]+\.(?:jpg|jpeg|png|webp|gif)[^"]*)"',
            html):
        u = m.group(1)
        if u.startswith("//"):
            u = "https:" + u
        imgs.append(u)
    if imgs:
        art["images"] = imgs
    return art


def is_qualified(data):
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


def download_image(img_url):
    """下载并校验图片，成功返回 bytes，失败返回 None"""
    try:
        img_url = img_url.strip()
        if not img_url.lower().startswith("http"):
            return None
        low = img_url.lower()
        if any(x in low for x in ("loading", "transparent", "placeholder",
                                  "logo", "icon", "qrcode", "banner.png", "spacer")):
            print("skip placeholder url:", img_url[:100])
            return None
        candidates = [img_url]
        m = re.search(r"![\w\d]+(\?|$)", img_url)
        if m:
            candidates.insert(0, img_url[:m.start()])
        for u in candidates:
            try:
                data = _get(u, timeout=45)
            except Exception:
                continue
            if data[:3] == b"\xff\xd8\xff" or data[:3] == b"\x89PN" or data[:4] == b"RIFF":
                if is_qualified(data):
                    return data
                return None
        return None
    except Exception as e:
        print("image download fail:", img_url[:120], e)
        return None


def main():
    bj = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(bj)
    today = now.strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 1. 抓最新文章列表（真实、最新）
    news = fetch_news_list()
    print("== LIST ==")
    print("got", len(news), "articles")
    if not news:
        print("no articles fetched, keep old data")
        return

    # 2. 逐篇抓详情（发布时间 + 图片）
    for art in news:
        fetch_article(art)

    # 3. 千问提取：只保留真实品牌活动
    lines = []
    for art in news:
        d = art.get("date", "")
        lines.append(f"- [{d}] {art['title']}")
    sample = "\n".join(lines)
    parse_prompt = (
        "下面是茶饮行业媒体《饮品报》最新文章标题清单（带发布日期）。\n"
        "请提取其中属于「真实品牌营销活动/新品/联名」的条目，输出严格 JSON 数组（只输出 JSON）：\n"
        '[{"title":"活动名(用文章标题概括,不超过25字)","brand":"品牌名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD(按文章发布日期,若文章写明活动时间用文中时间)","endDate":"YYYY-MM-DD(未给则startDate加30天)",'
        '"description":"不超过40字的官方活动介绍(基于标题合理概括)"}]\n'
        "规则：\n"
        "1. 只保留有具体品牌和产品/活动的条目；行业分析、论坛、展会、纯财务/开店报道、无具体活动的文章一律排除；\n"
        "2. 每篇最多一条，总共最多 4 条；\n"
        "3. 日期必须是 2026 年；发布超过 10 天的文章排除。\n"
        f"文章清单：\n{sample}"
    )
    raw = qwen_chat(parse_prompt)
    print("== PARSE ==")
    print(raw[:800])
    try:
        acts = extract_json(raw)
    except Exception as e:
        print("parse fail:", e, "->", raw[:300])
        return

    # 4. 对每条提取结果匹配文章，下载配图
    items = []
    seen = set()
    for a in acts:
        title = str(a.get("title", "")).strip()
        if not title or title in seen:
            continue
        seen.add(title)
        matched = None
        for art in news:
            t = art["title"]
            if title[:8] in t or a.get("brand", "") in t or t[:8] in title:
                matched = art
                break
        if matched is None:
            print("no article matched, skip:", title)
            continue
        cands = list(matched.get("images", []))
        if matched.get("cover"):
            cands.append(matched["cover"])
        data = None
        for u in cands:
            data = download_image(u)
            if data:
                break
        if not data:
            print("skip activity (no image):", title)
            continue
        poster = f"images/poster_{today}_{len(items)}.jpg"
        with open(poster, "wb") as f:
            f.write(data)
        sd = str(a.get("startDate", "")).strip()
        ed = str(a.get("endDate", "")).strip()
        if not sd:
            sd = today
        items.append({
            "brand": str(a.get("brand", "")).strip(),
            "title": title,
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": sd,
            "endDate": ed or sd,
            "image": RAW_BASE + "/" + poster,
            "description": str(a.get("description", "")).strip(),
            "ratio": 1.2,
        })
        if len(items) >= 4:
            break

    if not items:
        print("no activities generated, keep old data")
        return

    with open("data/activities.json", "w", encoding="utf-8") as f:
        json.dump({"updated_at": today, "activities": items}, f, ensure_ascii=False, indent=2)
    print("OK activities:", len(items))


if __name__ == "__main__":
    main()
