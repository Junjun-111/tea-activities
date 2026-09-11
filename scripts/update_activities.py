#!/usr/bin/env python3
"""每天定时任务：抓饮品报最新文章 -> 豆包联网搜索活动 -> 抓官方配图 -> 更新 activities.json

数据源：饮品报移动版 m.drinknewspaper.com（茶饮行业垂直媒体，日更，文章配图多为品牌官方宣传图）。
搜索与提取全部由豆包（doubao-seed-2-1-pro，火山方舟 Responses API + Web Search 插件）完成。
依赖环境变量：ARK_API_KEY
"""
import datetime
import gzip
import io
import json
import os
import re
import urllib.request

ARK_KEY = os.environ["ARK_API_KEY"]
ARK_RESPONSES = "https://ark.cn-beijing.volces.com/api/v3/responses"
DOUBAO_MODEL = "doubao-seed-2-0-lite-260428"
RAW_BASE = "https://raw.githubusercontent.com/Junjun-111/tea-activities/main"

MOBILE_UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
             "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")
LIST_URL = "http://m.drinknewspaper.com/"
ART_URL = "http://m.drinknewspaper.com/news/{id}.html"
# 栏目"饮品头条"(groupId=25) 全量列表数据接口
LIST_FULL = ("http://m.drinknewspaper.com/nr.jsp?_reqArgs=%7B%22args%22%3A%7B%22groupId%22%3A25%2C%22mid%22%3A302%7D%7D")

# 预设城市列表（一线+省会，与 App 内置 location_service.dart 保持一致）
CITIES = [
    {"key": "beijing", "name": "北京", "province": "北京"},
    {"key": "shanghai", "name": "上海", "province": "上海"},
    {"key": "guangzhou", "name": "广州", "province": "广东"},
    {"key": "shenzhen", "name": "深圳", "province": "广东"},
    {"key": "shenyang", "name": "沈阳", "province": "辽宁"},
    {"key": "chengdu", "name": "成都", "province": "四川"},
    {"key": "hangzhou", "name": "杭州", "province": "浙江"},
    {"key": "wuhan", "name": "武汉", "province": "湖北"},
    {"key": "xian", "name": "西安", "province": "陕西"},
    {"key": "nanjing", "name": "南京", "province": "江苏"},
    {"key": "changsha", "name": "长沙", "province": "湖南"},
    {"key": "chongqing", "name": "重庆", "province": "重庆"},
]

# 品牌门店城市覆盖表：防止把"全国上市但该城市无门店"的品牌误加进城市列表。
# 仅收录全国性主流连锁品牌；未收录的品牌默认放行（交给提示词规则过滤）。
# 城市 key 见上方 CITIES。数据为公开可查的大致覆盖，遗漏以门店为准。
BRAND_CITY_COVER = {
    "霸王茶姬": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "瑞幸": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
             "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "蜜雪冰城": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "星巴克": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
               "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "库迪": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
             "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "奈雪的茶": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "喜茶": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
             "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "茶百道": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
               "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "沪上阿姨": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "书亦烧仙草": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                   "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "CoCo都可": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "一点点": {"beijing", "shanghai", "guangzhou", "shenzhen", "chengdu",
               "hangzhou", "wuhan", "xian", "nanjing", "chongqing"},
    "甜啦啦": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
               "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "益禾堂": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
               "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "茶颜悦色": {"wuhan", "changsha", "nanjing", "chongqing", "xi'an", "xian"},
    "Manner": {"beijing", "shanghai", "guangzhou", "shenzhen", "chengdu", "hangzhou", "nanjing"},
    "瑞幸咖啡": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
                 "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "古茗": {"beijing", "shanghai", "guangzhou", "shenzhen", "chengdu",
             "hangzhou", "wuhan", "xian", "nanjing", "changsha", "chongqing"},
    "柠季": {"beijing", "shanghai", "guangzhou", "shenzhen", "shenyang", "chengdu",
             "hangzhou", "wuhan", "changsha", "chongqing"},
    "LINLEE林里": {"beijing", "shanghai", "guangzhou", "shenzhen", "chengdu", "hangzhou",
                   "wuhan", "changsha", "chongqing"},
}
# 已确认"尚未进驻该城市"的硬排除（避免误加），key 为城市 key，值为品牌名列表
BRAND_CITY_EXCLUDE = {
    "shenyang": ["古茗", "茶颜悦色", "Manner"],
    "changsha": ["一点点"],
}

# 人工确认活动：用户亲自核实过的真实活动（豆包联网搜不到但真实存在的，如仅在小程序端展示的门店活动）。
# 每次跑城市数据自动并入，不依赖搜索。key 为城市 key。
MANUAL_ACTIVITIES = {
    "shenyang": [
        {
            "brand": "霸王茶姬",
            "title": "9.11全场无券直享买一送一",
            "category": "茶饮",
            "startDate": "2026-09-11",
            "endDate": "2026-09-11",
            "description": "小程序堂食+外卖下单，任意双杯及以上饮品（含茶拉朵gelato冰淇淋）结算自动减免低价一杯，无需领券，每账号限1次",
        },
    ],
}


def _get(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": MOBILE_UA, "Accept-Encoding": "gzip"},
    )
    data = urllib.request.urlopen(req, timeout=timeout).read()
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    return data


def _post_responses(body, timeout=120):
    req = urllib.request.Request(
        ARK_RESPONSES,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + ARK_KEY,
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def doubao_chat(prompt, system="你是茶饮行业情报分析师。", max_tokens=2500, search=False):
    """豆包对话/联网搜索，返回最终文本。

    search=True 时挂载 Web Search 插件（需方舟已开通联网内容插件），
    模型自动判断是否联网、自动发起多关键词搜索。
    """
    body = {
        "model": DOUBAO_MODEL,
        "input": [
            {"role": "system", "content": [{"type": "input_text", "text": system}]},
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]},
        ],
        "thinking": {"type": "disabled"},   # 关闭深度思考，直接出结果
        "max_output_tokens": max_tokens,
    }
    if search:
        body["tools"] = [{"type": "web_search", "max_keyword": 5}]
    resp = _post_responses(body)
    texts = []
    for item in resp.get("output", []):
        if item.get("type") != "message":
            continue
        for c in item.get("content", []):
            if c.get("type") == "output_text":
                texts.append(c.get("text", ""))
    return "".join(texts)


def extract_json(text):
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError("no json array in model output")
    return json.loads(text[start:end + 1])


def normalize_dates(sd, ed, today, min_start=None):
    """校验并规范化活动日期，返回 (startDate, endDate) 或 None（过期/陈旧剔除）。

    规则（用户确认）：
    - startDate：资讯里明确写了开始日期就用；没写/非法则用今天；
    - endDate：资讯里明确写了截止日期才填；没写则留空字符串""表示"持续中"，
      不编造截止日期，由 lastSeen 机制动态判定是否结束；
    - 明确给了 endDate 且早于 today：过期，返回 None；
    - startDate 早于 min_start（默认 today-40 天）：视为陈旧信息返回 None。
    """
    today_d = today if isinstance(today, datetime.date) else datetime.date.fromisoformat(today)
    try:
        sd_d = datetime.date.fromisoformat(sd.strip()) if sd and sd.strip() else None
    except ValueError:
        sd_d = None
    try:
        ed_d = datetime.date.fromisoformat(ed.strip()) if ed and ed.strip() else None
    except ValueError:
        ed_d = None
    if sd_d is None:
        sd_d = today_d
    # 只处理明确的截止日期；留空 = 持续中，不过期
    if ed_d is not None and ed_d < today_d:
        return None
    if min_start is None:
        min_start = today_d - datetime.timedelta(days=40)
    if sd_d < min_start:
        return None
    return sd_d.isoformat(), (ed_d.isoformat() if ed_d else "")


def fetch_news_list():
    """抓全量列表（数据接口），返回按 id 从新到旧的 [{id,title,href,cover}]"""
    items = {}
    try:
        html = _get(LIST_FULL).decode("utf-8", "ignore")
    except Exception as e:
        print("list fetch fail:", e)
        return []
    # 逐块解析每条新闻：块内取第一个 <a href>（真实文章链接），封面取 newsImg 的 background:url()
    pat = re.compile(r'newsId="(\d+)"\s+newsName="([^"]*)"[^>]*>(.*?)</a>', re.S)
    for m in pat.finditer(html):
        nid, title, inner = m.group(1), m.group(2), m.group(3)
        if not title.strip():
            continue
        href_m = re.search(r'<a[^>]+href="([^"]+)"', inner)
        img_m = re.search(r'background:url\(((?://|https?://)[^)]+)\)', inner)
        items.setdefault(nid, {
            "id": nid,
            "title": title.strip(),
            "href": href_m.group(1) if href_m else ART_URL.format(id=nid),
            "cover": img_m.group(1) if img_m else None,
        })
    # 按 id 从新到旧，最多 40 篇（覆盖更多近 10 天内的真实活动）
    ordered = sorted(items.values(), key=lambda x: int(x["id"]), reverse=True)
    return ordered[:40]


def full_cover_url(u):
    """去掉列表缩略图 !WxH 缩放后缀，返回原图 URL"""
    if not u:
        return None
    u = u.strip()
    if u.startswith("//"):
        u = "https:" + u
    return re.sub(r"!\d+x\d+(\.\w+)?$", "", u)


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
        # 拒绝超宽/超高横幅（文章头部 banner 等），海报比例约 0.6~2.0
        if ok:
            ratio = w / h if h > 0 else 0
            if ratio > 2.6 or ratio < 0.45:
                ok = False
                print("rejected by ratio:", ratio)
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


def bing_search(query, n=3):
    """必应搜索，返回前 n 个外部文章链接（海外网络时好时坏，失败返回空）"""
    links = []
    try:
        import urllib.parse
        url = "https://www.bing.com/search?q=" + urllib.parse.quote(query)
        req = urllib.request.Request(url, headers={"User-Agent": MOBILE_UA})
        html = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
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
        print("bing search fail:", e)
    return links


def fetch_page_images(page_url):
    """抓页面 og:image -> 前几张 <img>，返回去重候选列表"""
    cands = []
    try:
        html = _get(page_url).decode("utf-8", "ignore")
        m = re.search(
            r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
        if not m:
            m = re.search(
                r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', html)
        if m:
            cands.append(m.group(1))
        for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', html):
            cands.append(m.group(1))
            if len(cands) >= 6:
                break
    except Exception as e:
        print("page fetch fail:", page_url, e)
    return cands


def fetch_city_activities(city, news, today, cutoff_days=7):
    """按城市联网搜索当地活动，返回 [{brand,title,category,startDate,endDate,description,image,ratio}]

    city: {"key","name","province"}
    news: 已抓取的饮品报文章列表（用于配图匹配，可为空）
    无图的活动降级为纯文字条目（image 为空字符串），避免因缺图丢信息。
    """
    city_name = city["name"]
    province = city["province"]
    # 用户选定的品牌关注清单（茶饮 + 咖啡）
    watch_brands = (
        "喜茶、奈雪的茶、霸王茶姬、茶百道、蜜雪冰城、古茗、甜啦啦、沪上阿姨、"
        "茉莉奶白、爷爷不泡茶、柠季、一点点、700cc、鲜果时间、"
        "瑞幸、星巴克、库迪、cubic3立方咖啡"
    )
    search_prompt = (
        f"现在是{today}。请联网搜索中国「{province}{city_name}」地区最近 {cutoff_days} 天内"
        "以下固定品牌清单中每个品牌的真实门店活动。\n"
        f"【品牌清单】{watch_brands}。\n"
        + (f"【已确认该城市有门店的品牌（必须逐一搜索，不得跳过）】爷爷不泡茶、茉莉奶白、鲜果时间、柠季、700cc、cubic3立方咖啡。\n" if city["key"] == "shenyang" else "")
        + "【执行步骤】逐个搜索上述每个品牌最近 {cutoff_days} 天内是否有以下活动：\n"
        "1. 新品上市；\n"
        "2. IP联名/限定联名产品；\n"
        "3. 门店直享买一送一（无券、不用抢、下单自动生效）；\n"
        "4. 打卡送周边/饮品、到店赠饮、快闪主题店。\n"
        "【城市判定】不要求资讯必须明确提到「该城市」或任何城市名——全国性连锁品牌的活动默认该城市门店同步参与，"
        "直接保留；仅当资讯明确说明活动限定在别的城市/别的省份时才排除。\n"
        "【日期判定】不要求资讯必须写明活动日期——资讯没写具体开始日期时，以官方发布该活动的日期为准。\n"
        "【重要过滤规则】只保留知名连锁品牌的门店活动；"
        "排除：个人/个体经营的小店、独立咖啡店、学校食堂、非品牌摊位、社区团购、非茶饮咖啡类品牌。\n"
        "排除以下类型的活动：小程序抽奖/口令兑奖/兑换券/0.01元或0.1元抢购类、需要抢券或领券的、"
        "美团/饿了么等外卖平台套餐（如'双杯套餐17.99元'）、平台优惠券活动。\n"
        "只要这4类：①新品上市；②IP联名产品；③门店无券直享买一送一（下单自动生效）；④打卡/到店送周边。\n"
        "列出所有满足条件的活动（数量不限，越多越好），"
        "每条包含：品牌名、活动或新品名、开始日期、结束日期（如有）、"
        "一句话介绍、是否为全国活动、该活动的官方信息来源URL（必须是报道该活动的官方微博/微信公众号文章/品牌官网/新闻网页链接，不要编造URL）。"
    ).replace("{cutoff_days}", str(cutoff_days))
    try:
        news_txt = doubao_chat(search_prompt, search=True)
    except Exception as e:
        print(f"[{city_name}] search fail:", e)
        return []
    print(f"== CITY {city_name} SEARCH ==")
    print(news_txt[:500])

    parse_prompt = (
        f"根据下面的「{province}{city_name}」茶饮资讯，提取所有满足条件的品牌活动（数量不限，"
        "资讯里有几条就提取几条，满足条件都保留），"
        f"【品牌清单】只保留以下品牌的官方门店活动：{watch_brands}。"
        "排除个人小店、独立咖啡店、学校食堂、非品牌摊位、社区团购。"
        "排除：小程序抽奖/口令兑奖/兑换券/0.01元或0.1元抢购、需抢券领券的、外卖平台套餐（如双杯套餐17.99元）、平台优惠券活动。"
        "只要：①新品上市；②IP联名；③门店无券直享买一送一；④打卡送周边。\n"
        "输出严格 JSON 数组（只输出 JSON，不要任何其他文字）：\n"
        '[{"title":"活动名(不超过25字)","brand":"品牌名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD(资讯里明确写了活动开始日期就用；没写就填该活动官方发布的日期；再没有就填今天)",'
        '"endDate":"YYYY-MM-DD(资讯里明确写了截止日期才填；没有明确截止日期就留空字符串)",'
        '"description":"不超过40字的当地活动介绍","national":true或false,'
        '"sourceUrl":"报道该活动的官方信息来源URL(资讯里没给出就留空字符串)"}]\n'
        f"资讯：\n{news_txt}"
    )
    try:
        acts = extract_json(doubao_chat(parse_prompt, max_tokens=4000))
    except Exception as e:
        print(f"[{city_name}] parse fail:", e)
        return []

    items = []
    seen = set()
    # 程序兜底：剔除模型漏网的非连锁品牌/个人小店（防止个人店、食堂混入）
    NON_BRAND = ("食堂", "档口", "个体", "私人", "工作室", "小巷", "商户", "咖啡屋",
                 "咖啡店", "小摊", "摊贩", "社区团购", "自营")
    # 剔除"抽奖/兑券/平台套餐"类（用户只要：新品/IP联名/门店直享买一送一/打卡送周边）
    NON_ACT = ("抽奖", "兑奖", "兑换券", "0.01元", "0.1元", "口令", "免单券", "抢券",
               "领券", "美团外卖", "外卖平台", "平台套餐", "双杯套餐", "买1送1券",
               "买一送一券", "代金券", "刮奖", "游戏得", "任务兑换")
    for i, a in enumerate(acts):
        title = str(a.get("title", "")).strip()
        brand = str(a.get("brand", "")).strip()
        desc = str(a.get("description", "")).strip()
        if not title or not brand or title in seen:
            continue
        if any(k in brand for k in NON_BRAND) or any(k in title for k in NON_BRAND):
            print(f"[{city_name}] skip non-brand:", brand, "-", title)
            continue
        if any(k in title for k in NON_ACT) or any(k in desc for k in NON_ACT):
            print(f"[{city_name}] skip draw/platform:", brand, "-", title)
            continue
        # 品牌-城市覆盖校验：排除"该城市尚未进驻"的品牌（如古茗无沈阳门店）
        excluded = BRAND_CITY_EXCLUDE.get(city["key"], [])
        if any(b in brand for b in excluded):
            print(f"[{city_name}] skip no-store brand:", brand, "-", title)
            continue
        cover = BRAND_CITY_COVER.get(brand)
        if cover is not None and city["key"] not in cover and city["key"] not in {c.replace("xi'an", "xian") for c in cover}:
            print(f"[{city_name}] skip no-city-cover:", brand, "-", title)
            continue
        # 日期校验：过期活动（endDate 早于今天）直接剔除，避免把已结束的活动算进来
        dates = normalize_dates(str(a.get("startDate", "")), str(a.get("endDate", "")), today)
        if dates is None:
            print(f"[{city_name}] skip expired/too-old:", brand, "-", title)
            continue
        sd, ed = dates
        seen.add(title)
        # 配图优先级：活动信息来源页面的图（og:image/正文图）→ 饮品报文章配图兜底
        # 原则：图片必须来自报道该活动的来源页面，禁止用无关文章图冒充活动海报
        data = None
        src = str(a.get("sourceUrl", "")).strip()
        if src.lower().startswith("http"):
            for u in fetch_page_images(src):
                u2 = full_cover_url(u) if "//" in u else u
                if not u2.lower().startswith("http"):
                    continue
                data = download_image(u2)
                if data:
                    print(f"[{city_name}] image from source:", src[:90])
                    break
        # 饮品报文章配图兜底（要求标题含品牌 + 活动标题中的活动词，避免配错图）
        if data is None:
            act_kw = title.replace(brand, "").strip()[:4]
            matched = None
            for art in news:
                t = art.get("title", "")
                if brand and brand in t and act_kw and act_kw in t:
                    matched = art
                    break
            if matched is not None:
                print(f"[{city_name}] image from yinpinbao:", matched.get("title", "")[:60])
                cover = full_cover_url(matched.get("cover"))
                cands = [c for c in ([cover] + list(matched.get("images", []))) if c]
                for u in cands:
                    data = download_image(u)
                    if data:
                        break
        image = ""
        if data:
            poster = f"images/poster_{city['key']}_{today}_{i}.jpg"
            with open(poster, "wb") as f:
                f.write(data)
            image = RAW_BASE + "/" + poster
        sd, ed = dates
        items.append({
            "brand": brand,
            "title": title,
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": sd,
            "endDate": ed,
            "image": image,
            "description": str(a.get("description", "")).strip(),
            "ratio": 1.2,
            "lastSeen": today,
        })
        if len(items) >= 5:
            break

    # 并入人工确认活动（用户核实过的真实活动，不依赖搜索，去重）
    existing = {it["title"] for it in items}
    for ma in MANUAL_ACTIVITIES.get(city["key"], []):
        title = str(ma.get("title", "")).strip()
        if not title or title in existing:
            continue
        dates = normalize_dates(str(ma.get("startDate", "")), str(ma.get("endDate", "")), today)
        if dates is None:
            print(f"[{city_name}] manual skip expired:", title)
            continue
        sd, ed = dates
        items.append({
            "brand": str(ma.get("brand", "")).strip(),
            "title": title,
            "category": str(ma.get("category", "茶饮")),
            "startDate": sd,
            "endDate": ed,
            "image": "",
            "description": str(ma.get("description", "")).strip(),
            "ratio": 1.2,
            "lastSeen": today,
        })
        existing.add(title)

    # lastSeen 机制：活动没有明确截止日期时，靠"连续几天搜不到则移除"动态判定结束。
    # - 本次搜到的活动：lastSeen 更新为今天；
    # - 旧文件里的活动本次没搜到：保留（活动可能仍持续，只是没报道），
    #   连续 STALE_DAYS 天都没搜到才移除。
    # 去重：同一品牌下标题相似（共享 ≥4 个中文字符）视为同一活动，取本次新版本（lastSeen 更新）
    stale_days = int(os.environ.get("STALE_DAYS", "3"))
    today_d = datetime.date.fromisoformat(today)

    def _norm(s):
        # 保留中文+数字，剔除英文字母（YOYO/Happy Puppy 等品牌英文会干扰中文相似度比较）
        return re.sub(r"[^0-9\u4e00-\u9fff]", "", str(s))

    def _same_act(b1, t1, b2, t2):
        """判断两条活动是否为同一活动：品牌包含 + 标题字符集相似度>=0.6"""
        b1, b2 = _norm(b1), _norm(b2)
        if not b1 or not b2 or not (b1 in b2 or b2 in b1):
            return False
        a, b = _norm(t1), _norm(t2)
        if not a or not b:
            return False
        if a == b:
            return True
        inter = len(set(a) & set(b))
        union = len(set(a) | set(b))
        return union > 0 and inter / union >= 0.6

    try:
        with open(f"data/activities_{city['key']}.json", "r", encoding="utf-8") as f:
            old_data = json.load(f)
        old_items = old_data.get("activities", [])
    except Exception:
        old_items = []
    for it in items:
        it["lastSeen"] = today
    # 全局去重：旧条目与本轮新条目、新条目之间，品牌+标题相似视为同一条，保留 lastSeen 最新者
    merged = []
    for it in items:
        if not any(_same_act(it["brand"], it["title"], m["brand"], m["title"]) for m in merged):
            merged.append(it)
    for oit in old_items:
        ob, ot = str(oit.get("brand", "")), str(oit.get("title", ""))
        dup = any(
            _same_act(it["brand"], it["title"], ob, ot)
            for it in merged
        )
        if dup:
            continue  # 已有同活动（新版本优先），旧条目丢弃
        try:
            last = datetime.date.fromisoformat(str(oit.get("lastSeen", oit.get("startDate", today))))
        except ValueError:
            last = today_d
        if (today_d - last).days > stale_days:
            print(f"[{city['name']}] drop unseen >{stale_days}d:", ob, "-", ot)
            continue  # 连续多天搜不到，判定活动已结束，移除
        merged.append(oit)  # 保留，lastSeen 不变，等待下次确认
    # 按开始日期排序（新的在前）
    merged.sort(key=lambda x: x.get("startDate", ""), reverse=True)
    return merged


def main():
    bj = datetime.timezone(datetime.timedelta(hours=8))
    now = datetime.datetime.now(bj)
    today = now.strftime("%Y-%m-%d")
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 可选：CITY_KEYS 环境变量（逗号分隔城市 key，如 shenyang,beijing）只跑指定城市；
    # 未设置则跑全部预设城市
    city_filter = [c.strip() for c in os.environ.get("CITY_KEYS", "").split(",") if c.strip()]
    cities = [c for c in CITIES if not city_filter or c["key"] in city_filter]
    print("== CITIES ==")
    print("running", len(cities), "cities:", [c["key"] for c in cities])

    # 1. 抓最新文章列表（用于给城市活动匹配配图；失败不中断城市搜索）
    news = fetch_news_list()
    print("== LIST ==")
    print("got", len(news), "articles")
    for art in news:
        fetch_article(art)

    # 2. 逐城市联网搜索当地活动，生成 activities_{key}.json
    all_items = []
    for city in cities:
        print(f"\n===== CITY {city['name']} =====")
        try:
            items = fetch_city_activities(city, news, today)
        except Exception as e:
            print(f"[{city['name']}] city fetch error:", e)
            items = []
        if not items:
            print(f"[{city['name']}] no activities, keep old file if exists")
            continue
        with open(f"data/activities_{city['key']}.json", "w", encoding="utf-8") as f:
            json.dump({"updated_at": today, "city": city["name"], "activities": items},
                      f, ensure_ascii=False, indent=2)
        print(f"[{city['name']}] OK activities:", len(items))
        all_items.extend(items)

    # 3. 全国兜底文件：饮品报全国活动 + 各城市合并（App 未定位时使用）
    national = []
    if news:
        # 全国口径：从饮品报文章提取（原逻辑，最多 6 条）
        lines = []
        for art in news:
            d = art.get("date", "")
            lines.append(f"- [{d}] {art['title']}")
        sample = "\n".join(lines)
        parse_prompt = (
            "下面是茶饮行业媒体《饮品报》最新文章标题清单（带发布日期）。\n"
            "请提取其中属于「品牌真实活动/新品/联名」的条目，输出严格 JSON 数组（只输出 JSON）：\n"
            '[{"title":"活动名(用文章标题概括,不超过25字)","brand":"品牌名","category":"茶饮或咖啡",'
            '"startDate":"YYYY-MM-DD(按文章发布日期,若文章写明活动时间用文中时间)","endDate":"YYYY-MM-DD(未给则startDate加30天)",'
            '"description":"不超过40字的官方活动介绍(基于标题合理概括)"}]\n'
            "规则：\n"
            "1. 保留：品牌新品上市、联名合作、限定饮品、买赠优惠、快闪/主题店/开业、门店营销活动；\n"
            "2. 排除：论坛/峰会/大会/展会/颁奖、行业趋势分析、纯融资/财报/开店数量报道、无具体品牌或产品的；\n"
            "3. 排除非茶饮咖啡类品牌（如矿泉水、白酒、啤酒、能量饮料）；\n"
            "4. 每篇最多一条，总共最多 6 条；\n"
            "5. 日期必须是 2026 年。\n"
            f"文章清单：\n{sample}"
        )
        raw = doubao_chat(parse_prompt)
        print("== NATIONAL PARSE ==")
        print(raw[:600])
        try:
            acts = extract_json(raw)
        except Exception as e:
            print("national parse fail:", e)
            acts = []
        seen = set()
        for i, a in enumerate(acts):
            title = str(a.get("title", "")).strip()
            if not title or title in seen:
                continue
            seen.add(title)
            matched = None
            for art in news:
                t = art["title"]
                if title[:6] in t or t[:6] in title:
                    matched = art
                    break
            if matched is None:
                for art in news:
                    if a.get("brand", "") in art["title"]:
                        matched = art
                        break
            data = None
            if matched is not None:
                cover = full_cover_url(matched.get("cover"))
                cands = [c for c in ([cover] + list(matched.get("images", []))) if c]
                for u in cands:
                    data = download_image(u)
                    if data:
                        break
            image = ""
            if data:
                poster = f"images/poster_national_{today}_{i}.jpg"
                with open(poster, "wb") as f:
                    f.write(data)
                image = RAW_BASE + "/" + poster
            sd = str(a.get("startDate", "")).strip() or today
            ed = str(a.get("endDate", "")).strip() or sd
            national.append({
                "brand": str(a.get("brand", "")).strip(),
                "title": title,
                "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
                "startDate": sd,
                "endDate": ed,
                "image": image,
                "description": str(a.get("description", "")).strip(),
                "ratio": 1.2,
            })
            if len(national) >= 6:
                break

    # 全国文件：城市合并（保证非空）优先，其次饮品报提取
    national = national or all_items
    if national:
        with open("data/activities.json", "w", encoding="utf-8") as f:
            json.dump({"updated_at": today, "activities": national}, f,
                      ensure_ascii=False, indent=2)
        print("OK national activities:", len(national))
    else:
        print("no activities generated, keep old data")


if __name__ == "__main__":
    main()
