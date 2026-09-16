#!/usr/bin/env python3
"""每天定时任务：抓公开网页 -> 提取品牌活动 -> 更新 activities.json

数据源：饮品报移动版 m.drinknewspaper.com（茶饮行业垂直媒体，日更，文章配图多为品牌官方宣传图）。
两种模式：
1) 免费模式（默认，无需任何 key）：抓饮品报标题 + Bing/DuckDuckGo 搜索结果，
   用关键词规则提取活动 —— 0 成本，数据比模型模式粗一些。
2) 模型模式：设置了 ARK_API_KEY（且 FREE_MODE 不为 1）时，走豆包联网搜索，
   数据更准更全，但联网搜索按次计费。
"""
import datetime
import gzip
import json
import os
import re
import urllib.parse
import urllib.request

ARK_KEY = os.environ.get("ARK_API_KEY", "")
# 免费模式：没有填 ARK_API_KEY，或显式设置 FREE_MODE=1
FREE_MODE = (not ARK_KEY) or os.environ.get("FREE_MODE") == "1"
ARK_RESPONSES = "https://ark.cn-beijing.volces.com/api/v3/responses"
# 想省钱可以换成便宜的模型（例如 doubao-seed-2-0-lite-260628），
# 质量会下降一些；不改就用 pro。
DOUBAO_MODEL = os.environ.get("DOUBAO_MODEL", "doubao-seed-2-1-pro-260628")

# 用户选定的品牌关注清单（茶饮 + 咖啡）
WATCH_BRANDS = (
    "喜茶、奈雪的茶、霸王茶姬、茶百道、蜜雪冰城、古茗、甜啦啦、沪上阿姨、"
    "茉莉奶白、爷爷不泡茶、柠季、一点点、700cc、鲜果时间、"
    "瑞幸、星巴克、库迪、cubic3立方咖啡"
)
BRAND_NAMES = [b.strip() for b in WATCH_BRANDS.split("、") if b.strip()]

# 免费模式的规则关键词
ACT_KEYWORDS = ("新品", "上新", "上市", "回归", "联名", "限定", "买一送一", "买1送1",
                "第二杯", "开业", "快闪", "主题店", "打卡", "赠", "首发", "Pro", "报到")
NON_ACT_KEYWORDS = ("抽奖", "兑奖", "兑换券", "0.01元", "0.1元", "口令", "免单券", "抢券",
                    "领券", "美团外卖", "外卖平台", "平台套餐", "双杯套餐", "买1送1券",
                    "买一送一券", "代金券", "刮奖", "游戏得", "任务兑换")
NON_BRAND_KEYWORDS = ("食堂", "档口", "个体", "私人", "工作室", "小巷", "商户", "咖啡屋",
                      "咖啡店", "小摊", "摊贩", "社区团购", "自营")
# 免费模式里容易混进来的"非活动"内容：盘点/汇总/答疑/帖子这类
NOISE_KEYWORDS = ("有哪些", "盘点", "汇总", "合集", "回顾", "一文看懂", "排行榜",
                  "持续更新", "讨论", "测评", "开个贴", "奶茶上新贴")
COFFEE_BRANDS = ("瑞幸", "星巴克", "库迪", "Manner")

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
        {
            "brand": "喜茶",
            "title": "超多肉椰椰芒芒新品上市",
            "category": "茶饮",
            "startDate": "2026-09-15",
            "endDate": "",
            "description": "喜茶新品「超多肉椰椰芒芒」上新，全国门店陆续上线",
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
        # max_keyword 控制一次调用最多发起几次联网搜索——联网搜索是主要费用来源，
        # 3 次足够覆盖一组品牌（原来 5 次，配合分组搜索会成倍烧钱）
        body["tools"] = [{"type": "web_search", "max_keyword": 3}]
    resp = _post_responses(body)
    # 打印每次调用的 token 用量，方便在 Actions 日志里核算费用
    usage = resp.get("usage")
    if usage:
        print("[usage]", json.dumps(usage, ensure_ascii=False))
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


def fetch_article(art):
    """抓文章页：补充发布时间"""
    try:
        html = _get(art["href"]).decode("utf-8", "ignore")
    except Exception as e:
        print("article fetch fail:", art["href"], e)
        return art
    mt = re.search(r'(\d{4})-(\d{2})-(\d{2})T\d{2}:\d{2}', html)
    if mt:
        art["date"] = "%s/%s/%s" % (mt.group(1), mt.group(2), mt.group(3))
    return art


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


def strip_site_tail(text):
    """去掉搜索摘要里的站点名/日期尾巴（免费模式用，0 成本）"""
    t = re.sub(r"\s+", "", str(text))
    # 还原 HTML 实体（搜索结果里常见的 &quot; &amp; 等）
    t = (t.replace("&quot;", "\"").replace("&#39;", "'")
          .replace("&amp;", "&").replace("&nbsp;", "").replace("&lt;", "<")
          .replace("&gt;", ">"))
    t = re.sub(
        r"(中国咖啡网|咖啡网|饮品报|搜狐|网易|新浪|腾讯|百家号|知乎|小红书|微博|B站|"
        r"今日头条|凤凰网|东方财富|界面新闻|36氪|观点网|壹览商业|哔哩哔哩|bilibili|"
        r"抖音|快手|豆瓣|雪球|和讯|同花顺|赢商网|联商网|红餐网|餐企老板内参).*$", "", t)
    # 去掉开头的微博话题标签（#海航蜜雪冰城联名活动#）
    t = re.sub(r"^(#[^#]{0,20}#)+", "", t)
    t = re.sub(r"(\d{4}[-/年])?\d{1,2}月\d{1,2}日更新.*$", "", t)
    t = re.sub(r"\d{4}-\d{2}-\d{2}.*$", "", t)
    t = re.sub(r"\d{1,2}月\d{1,2}日更新.*$", "", t)
    return t.strip("|-—·。）)】（）()［］ 　")


def cap_title(text, limit=25):
    """截到 limit 字以内，尽量在标点处断开，不要在词中间切"""
    t = text.strip()
    if len(t) <= limit:
        return t
    head = t[:limit]
    for sep in ("，", "、", "：", ":", "（", "(", " ", "「"):
        i = head.rfind(sep)
        if i >= 8:
            return head[:i]
    return head


def pick_activity_title(title, snippet, brand):
    """从「标题 + 摘要」里挑最像活动名的一段：优先含品牌名的那段，
    标题里没有品牌就用摘要（免费模式常见的标题是站点名或日期）"""
    t = strip_site_tail(title)
    s = strip_site_tail(snippet)
    if brand and brand in t and len(t) >= 4:
        return cap_title(t)
    if brand and brand in s and len(s) >= 4:
        return cap_title(s)
    return cap_title(t or s)


def build_city_search_prompt(today, province, city_name, city_key, cutoff_days,
                             brands_text):
    """城市搜索用的完整提示词（独立成函数，方便直接打印/调试）。

    这就是每次联网调用发给模型的原文。注意：费用大头不是这段文字（约 1k token），
    而是模型按这段要求去联网搜索后，把抓到的网页内容塞进上下文（几万 token）。
    """
    return (
        f"现在是{today}。请联网搜索中国「{province}{city_name}」地区最近 {cutoff_days} 天内"
        "以下固定品牌清单中每个品牌的真实门店活动。\n"
        f"【品牌清单】{brands_text}。\n"
        + (f"【该城市重点品牌（除上面清单外，这些也必须逐一搜索）】爷爷不泡茶、茉莉奶白、鲜果时间、柠季、700cc、cubic3立方咖啡。\n" if city_key == "shenyang" else "")
        # 以下七行按用户 2026-09-16 的改法（精简版）
        + "【信息来源优先级】优先官方微博/公众号/小红书/官网，官方最近 1-3 天发布的新品即使媒体没报道也要收录\n"
        "【执行步骤】每个品牌单独搜，搜索 2 次（品牌+新品 / 品牌+联名·买一送一·上新）\n"
        "【城市判定】全国性连锁默认该城市门店同步参与，只排除明确限定别的城市的\n"
        "【日期判定】没写日期就以官方发布日为准\n"
        "【过滤规则】排除个人小店/食堂/社区团购；排除抽奖、兑券、0.01元抢购、美团外卖套餐\n"
        "【覆盖要求】每个品牌都要覆盖，有几条写几条\n"
        "只要这4类：①新品上市 ②IP联名 ③门店无券直享买一送一 ④打卡/到店送周边\n"
        "列出所有满足条件的活动（数量不限，越多越好）。\n"
        "输出严格 JSON 数组（只输出 JSON，不要任何其他文字）：\n"
        '[{"title":"活动名(不超过25字)","brand":"品牌名","category":"茶饮或咖啡",'
        '"startDate":"YYYY-MM-DD(资讯里明确写了活动开始日期就用；没写就填该活动官方发布的日期；再没有就填今天)",'
        '"endDate":"YYYY-MM-DD(资讯里明确写了截止日期才填；没有明确截止日期就留空字符串)",'
        '"description":"不超过40字的当地活动介绍",'
        '"sourceUrl":"报道该活动的官方信息来源URL(没有就留空字符串)"}]'
    )


def fetch_city_activities(city, news, today, cutoff_days=7):
    """按城市联网搜索当地活动，
    返回 [{brand,title,category,startDate,endDate,description,lastSeen}]

    （活动数据只保留文字信息：App 端不再展示海报图，脚本也不再下载图片）
    """
    city_name = city["name"]
    province = city["province"]
    # 免费模式：不调用付费模型/搜索，改走免费抓取 + 关键词规则
    if FREE_MODE:
        return fetch_free_city_activities(city, news, today, cutoff_days)
    watch_brands = WATCH_BRANDS
    # 品牌清单按 6 个一组拆开搜索：一次把 18 个品牌全丢给模型，它每个品牌只会浅搜一下，
    # 官方刚上新的产品很容易漏（实测漏掉过喜茶「超多肉椰椰芒芒」，而单独问豆包却能搜到）。
    # 拆成小组后每个品牌能搜得更深，最后把各组结果合并成一份再做结构化解析。
    brand_names = [b.strip() for b in watch_brands.split("、") if b.strip()]
    brand_groups = [
        "、".join(brand_names[i:i + 6]) for i in range(0, len(brand_names), 6)
    ]

    def build_search_prompt(brands_text):
        return build_city_search_prompt(
            today, province, city_name, city["key"], cutoff_days, brands_text
        )

    # 每组品牌一次联网调用，直接要求返回结构化 JSON。
    # 以前是"先联网搜索、再用第二个模型调用解析"，那次解析要把搜索结果整体再读一遍，
    # 上下文重复导致费用接近翻倍；合并成一次调用（也省掉它自己的 token）。
    acts = []
    for gi, group in enumerate(brand_groups, 1):
        try:
            raw = doubao_chat(build_search_prompt(group), search=True, max_tokens=4000)
        except Exception as e:
            print(f"[{city_name}] search group {gi} fail:", e)
            continue
        print(f"== CITY {city_name} SEARCH {gi}/{len(brand_groups)} ==")
        print(raw[:300])
        try:
            acts.extend(extract_json(raw))
        except Exception as e:
            print(f"[{city_name}] group {gi} parse fail:", e)
    if not acts:
        print(f"[{city_name}] all search groups failed")
        return []

    items = []
    seen = set()
    # 不做条数上限、也不做单品牌配额：清单里每个品牌近期的活动都要收进来。
    # （原来 if len(items) >= 5: break 只留前 5 条，导致大部分品牌永远进不来）
    # 近似重复的活动由下面的 _same_act 相似度合并，3 天没再搜到的会被清理掉，
    # 所以数据量不会无限膨胀。
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
        items.append({
            "brand": brand,
            "title": title,
            "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
            "startDate": sd,
            "endDate": ed,
            "description": str(a.get("description", "")).strip(),
            "lastSeen": today,
        })

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
            "description": str(ma.get("description", "")).strip(),
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
        # 一条标题包含另一条（如「霸王茶姬×CLOT联名」和「霸王茶姬携手CLOT推出联名系列」）也算同一活动
        if a in b or b in a:
            return True
        inter = len(set(a) & set(b))
        union = len(set(a) | set(b))
        # 阈值从 0.6 放宽到 0.45：免费模式抓到的多条同活动新闻标题差异较大，
        # 太严会同一个活动重复好几条
        return union > 0 and inter / union >= 0.45

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
    # 留一个空的 images 目录：工作流里的 upload_oss.py 会扫这个目录，
    # 现在不再生成海报图，所以它扫到的是空目录（不影响数据上传）
    os.makedirs("images", exist_ok=True)
    os.makedirs("data", exist_ok=True)

    # 可选：CITY_KEYS 环境变量（逗号分隔城市 key，如 shenyang,beijing）只跑指定城市；
    # 未设置则跑全部预设城市（App 端会按用户定位去取对应城市的文件，所以默认全跑）
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
    # 免费模式不调用模型，全国文件直接用各城市合并的结果（下面 national = national or all_items）
    if news and not FREE_MODE:
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
            sd = str(a.get("startDate", "")).strip() or today
            ed = str(a.get("endDate", "")).strip() or sd
            national.append({
                "brand": str(a.get("brand", "")).strip(),
                "title": title,
                "category": "咖啡" if "咖啡" in str(a.get("category", "")) else "茶饮",
                "startDate": sd,
                "endDate": ed,
                "description": str(a.get("description", "")).strip(),
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


def free_web_search(query, n=5, days=7):
    """零成本搜索：抓 DuckDuckGo / Bing 的网页结果，返回 [(标题, 摘要)]。

    不调用任何付费接口。两条都带时间过滤（只取最近 days 天），避免搜到几个月前的旧文章。
    实测 DuckDuckGo 的 html 端点更稳，所以它排在前面，Bing 兜底。
    """
    out = []
    df = "d" if days <= 1 else ("w" if days <= 7 else "m")
    try:
        url = (f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
               f"&df={df}")
        html = _get(url).decode("utf-8", "ignore")
        titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)
        snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
        for i, t in enumerate(titles):
            title = re.sub(r"<[^>]+>", "", t).strip()
            snip = re.sub(r"<[^>]+>", "", snips[i]).strip() if i < len(snips) else ""
            if title:
                out.append((title, snip))
            if len(out) >= n:
                break
    except Exception as e:
        print("ddg free search fail:", e)
    if len(out) < n:
        try:
            url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
                   + "&filters=" + urllib.parse.quote('ex1:"ez2"'))
            html = _get(url).decode("utf-8", "ignore")
            for m in re.finditer(
                    r'<li class="b_algo".*?<h2[^>]*>\s*<a[^>]*>(.*?)</a>.*?'
                    r'(?:<p[^>]*>(.*?)</p>)?', html, re.S):
                title = re.sub(r"<[^>]+>", "", m.group(1) or "").strip()
                snip = re.sub(r"<[^>]+>", "", m.group(2) or "").strip()
                if title:
                    out.append((title, snip))
                if len(out) >= n:
                    break
        except Exception as e:
            print("bing free search fail:", e)
    return out[:n]


def date_in_text(text, today, cutoff_days=7):
    """文本里出现的日期 → (日期字符串 或 None, 是否可用)。
    能找到日期且明显过期（早于 cutoff_days 天）就判定为旧文章，丢掉。"""
    m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
    if m:
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
    else:
        m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
        if not m:
            return (None, True)
        y, mo, d = int(today[:4]), int(m.group(1)), int(m.group(2))
    try:
        found = datetime.date(y, mo, d)
    except ValueError:
        return (None, True)
    today_d = datetime.date.fromisoformat(today)
    if (today_d - found).days > cutoff_days:
        return (found.isoformat(), False)
    return (found.isoformat(), True)


def fetch_free_city_activities(city, news, today, cutoff_days=7):
    """零成本路径：只用免费可抓的公开网页，用关键词规则提取品牌活动。

    来源：① 饮品报文章标题（带发布日期）② 每个品牌一次免费网页搜索。
    规则：文本里出现品牌名 + 命中活动关键词 → 收录；抽奖/券类/平台套餐直接丢掉。
    精度不如模型模式（标题当活动名、描述用摘要），但完全不花钱。
    """
    city_name = city["name"]
    items = []
    seen = set()

    def add(brand, title, snippet, start, desc):
        t = pick_activity_title(title, snippet, brand)
        if not t or t in seen:
            return
        seen.add(t)
        items.append({
            "brand": brand,
            "title": t,
            "category": "咖啡" if brand in COFFEE_BRANDS else "茶饮",
            "startDate": start,
            "endDate": "",
            "description": strip_site_tail(desc)[:40],
            "lastSeen": today,
        })

    def scan(title, snippet, date_hint=None):
        text = f"{title} {snippet}".strip()
        if not text or any(k in text for k in NON_ACT_KEYWORDS):
            return
        if any(k in text for k in NOISE_KEYWORDS):
            return
        if not any(k in text for k in ACT_KEYWORDS):
            return
        # 文本里写了日期、而且明显是旧文章（早于 cutoff）→ 丢掉
        found, ok = date_in_text(text, today, cutoff_days)
        if not ok:
            print(f"[{city_name}] skip stale({found}):", text[:40])
            return
        for brand in BRAND_NAMES:
            if brand not in text:
                continue
            if any(k in brand for k in NON_BRAND_KEYWORDS):
                continue
            add(brand, str(title), str(snippet), found or date_hint or today,
                text)
            return  # 一段文本只归给第一个命中的品牌

    # ① 饮品报最新文章标题（带发布日期，用它当活动开始日期）
    for art in news:
        title = art.get("title", "")
        if not title:
            continue
        d = normalize_dates(str(art.get("date", "")), "", today)
        scan(title, "", d[0] if d else today)

    # ② 每个品牌一次免费搜索，从结果标题 + 摘要里提取
    for brand in BRAND_NAMES:
        try:
            hits = free_web_search(f"{brand} 新品 上新 联名", 5)
        except Exception as e:
            print(f"[{city_name}] free search fail {brand}:", e)
            continue
        for (t, snip) in hits:
            if brand not in f"{t}{snip}":
                continue
            scan(t, snip, today)
    print(f"[{city_name}] free mode items:", len(items))
    return items


if __name__ == "__main__":
    main()
