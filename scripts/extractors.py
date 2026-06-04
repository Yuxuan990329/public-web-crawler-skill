import json
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urlparse


@dataclass
class ExtractedPage:
    title: str
    content: str
    links: list[tuple[str, str]]
    extractor: str


class GenericPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._current_link = None
        self._current_link_text = []
        self._title_parts = []
        self._text_parts = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self._current_link = href
                self._current_link_text = []

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag == "a" and self._current_link:
            self.links.append((self._current_link, clean_text(" ".join(self._current_link_text))))
            self._current_link = None
            self._current_link_text = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if not text:
            return
        if self._in_title:
            self._title_parts.append(text)
        if self._current_link:
            self._current_link_text.append(text)
        self._text_parts.append(text)

    def title(self):
        return clean_text(" ".join(self._title_parts))

    def content(self):
        return clean_text(" ".join(self._text_parts))


def clean_text(value):
    return re.sub(r"\s+", " ", value or "").strip()


# Class/id substrings that suggest a main article content container
_CONTENT_CLASS_HINTS = [
    "article-content", "article_content", "articlecontent",
    "post-content", "post_content", "postcontent",
    "detail-content", "detail_content", "detailcontent",
    "article-body", "articlebody",
    "news-content", "newscontent",
    "entry-content", "entrycontent",
    "main-content", "maincontent",
    "content-body", "contentbody",
    "page-content", "pagecontent",
]

# Markers that indicate the end of main content
_CONTENT_END_MARKERS = [
    "<footer",
    "<nav ",
    '<div id="footer',
    '<div class="footer',
    '<div id="nav',
    "</body>",
]


def find_main_content_html(html):
    """Heuristically find the main content block of a page.

    Priority: <article> -> <main> -> <div> with content-suggestive class/id.
    Falls back to the full HTML if nothing matches.
    """
    html_lower = html.lower()

    # 1. <article> tag
    start = html_lower.find("<article")
    if start != -1:
        end = html_lower.find("</article>", start)
        if end != -1:
            return html[start: end + 10]

    # 2. <main> tag
    start = html_lower.find("<main")
    if start != -1:
        end = html_lower.find("</main>", start)
        if end != -1:
            return html[start: end + 7]

    # 3. <div> with content-suggestive class or id
    for hint in _CONTENT_CLASS_HINTS:
        idx = html_lower.find(hint)
        if idx == -1:
            continue
        div_start = html_lower.rfind("<div", 0, idx)
        if div_start == -1:
            continue
        end = len(html)
        for marker in _CONTENT_END_MARKERS:
            pos = html_lower.find(marker, idx)
            if 0 < pos < end:
                end = pos
        return html[div_start:end]

    return html  # Fallback: full page


def normalize_domain(domain):
    domain = (domain or "").strip().lower()
    return domain[4:] if domain.startswith("www.") else domain


def url_domain(url):
    return normalize_domain(urlparse(url).netloc)


def extract_page(url, html):
    extractor = extractor_for_url(url)
    return extractor(url, html)


def extractor_for_url(url):
    domain = url_domain(url)
    for registered_domain, extractor in DOMAIN_EXTRACTORS.items():
        if domain == registered_domain or domain.endswith("." + registered_domain):
            return extractor
    return generic_extract_page


def generic_extract_page(url, html):
    # Full page: extract title and links (nav links are useful for detail discovery)
    full_parser = GenericPageParser()
    full_parser.feed(html)

    # Scope content extraction to main content area to reduce nav noise
    scoped_html = find_main_content_html(html)
    if scoped_html is not html:
        content_parser = GenericPageParser()
        content_parser.feed(scoped_html)
        content = content_parser.content()
    else:
        content = full_parser.content()

    return ExtractedPage(
        title=full_parser.title(),
        content=content,
        links=full_parser.links,
        extractor="generic",
    )


def yicai_extract_page(url, html):
    page = generic_extract_page(url, html)
    content = remove_yicai_ui_text(page.content)
    return ExtractedPage(
        title=page.title,
        content=content,
        links=page.links,
        extractor="yicai.com",
    )


def cheaa_com_extract_page(url, html):
    page = generic_extract_page(url, html)
    content = remove_repeated_phrases(
        page.content,
        [
            "中国家电网",
            "新闻中心",
            "产业圈",
            "数据",
            "回收",
            "消费与维权",
            "电视影音",
            "空调",
            "冰箱",
            "洗衣机",
            "厨房",
            "卫浴",
            "个护",
            "小家电",
            "空净",
            "净水",
            "手机",
        ],
    )
    content = re.sub(r"无障碍\s*", " ", content)
    return ExtractedPage(
        title=page.title,
        content=clean_text(content),
        links=page.links,
        extractor="cheaa.com",
    )


def chyxx_extract_page(url, html):
    page = generic_extract_page(url, html)
    content = strip_before_first_marker(
        page.content,
        [
            "首页 产业百科",
            "首页 智研观点",
            "首页 研究报告",
            "首页 资讯",
            "报告预览",
            "报告简介",
        ],
    )
    content = remove_repeated_phrases(
        content,
        [
            "智研咨询",
            "智研观点",
            "产业百科",
            "研究报告",
            "报告库",
            "排行榜",
            "资讯",
            "数据",
            "定制服务报告",
            "可行性研究报告",
            "市场地位证明",
            "专精特新申报",
        ],
    )
    content = remove_chyxx_ui_text(content)
    return ExtractedPage(
        title=page.title,
        content=content,
        links=page.links,
        extractor="chyxx.com",
    )


def huaon_extract_page(url, html):
    page = generic_extract_page(url, html)
    content = strip_before_first_marker(
        page.content,
        [
            "一、",
            "1、",
            "行业分类：",
            "共找到",
            "产品价值",
        ],
    )
    content = remove_repeated_phrases(
        content,
        [
            "华经情报网",
            "华经产业研究院",
            "资讯",
            "财经资讯",
            "企业动态",
            "产业前沿",
            "行业简讯",
            "数据",
            "宏观数据",
            "行业数据",
            "研究报告",
            "专题报告",
            "精品报告",
            "咨询服务",
            "可行性研究报告",
            "专精特新申报",
            "市场地位证明",
            "商业计划书",
            "定制报告",
        ],
    )
    content = remove_huaon_ui_text(content)
    title = page.title or huaon_title_from_content(content)
    return ExtractedPage(
        title=title,
        content=content,
        links=page.links,
        extractor="huaon.com",
    )


def qianzhan_extract_page(url, html):
    full_page = generic_extract_page(url, html)
    scoped_html = qianzhan_main_html(url, html)
    page = generic_extract_page(url, scoped_html) if scoped_html else full_page
    content = strip_before_first_marker(
        page.content,
        [
            "当前位置：",
            "报告目录",
            "报告价值",
        ],
    )
    content = remove_repeated_phrases(
        content,
        [
            "前瞻网",
            "可行性研究",
            "专项市场调研",
            "白皮书/蓝皮书",
            "研究报告",
            "产业研究",
            "产业规划",
            "产业大数据",
            "在线咨询",
        ],
    )
    content = remove_qianzhan_ui_text(content)
    return ExtractedPage(
        title=full_page.title or page.title,
        content=content,
        links=full_page.links,
        extractor="qianzhan.com",
    )


def iresearch_extract_page(url, html):
    full_page = generic_extract_page(url, html)
    scoped_html = iresearch_main_html(html)
    page = generic_extract_page(url, scoped_html) if scoped_html else full_page
    content = remove_iresearch_ui_text(page.content)
    return ExtractedPage(
        title=full_page.title or page.title,
        content=content,
        links=full_page.links,
        extractor="iresearch.cn",
    )


def cbndata_extract_page(url, html):
    page = generic_extract_page(url, html)
    state_page = cbndata_state_page(url, html)
    if not state_page:
        content = remove_cbndata_ui_text(cbndata_meta_content(html) or page.content)
        return ExtractedPage(
            title=cbndata_meta_title(html) or page.title,
            content=content,
            links=page.links,
            extractor="cbndata.com",
        )
    content = remove_cbndata_ui_text(state_page.content)
    return ExtractedPage(
        title=state_page.title or page.title,
        content=content,
        links=page.links,
        extractor="cbndata.com",
    )


def cbndata_state_page(url, html):
    match = re.search(r"window\.__INITIAL_STATE__=(\{.*?\});</script>", html, re.S)
    if not match:
        return None
    try:
        state = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    data = state.get("data") or state
    title = clean_text(first_json_value(data, {"title", "name"}))
    body_html = first_json_value(data, {"content", "body", "articleContent", "detail", "summary", "description"})
    if not body_html:
        return None
    body_page = generic_extract_page(url, body_html)
    return ExtractedPage(
        title=title or body_page.title,
        content=body_page.content,
        links=body_page.links,
        extractor="cbndata.com",
    )


def first_json_value(value, keys):
    if isinstance(value, dict):
        for key in keys:
            current = value.get(key)
            if isinstance(current, str) and clean_text(strip_tags(current)):
                return current
        for current in value.values():
            found = first_json_value(current, keys)
            if found:
                return found
    elif isinstance(value, list):
        for current in value:
            found = first_json_value(current, keys)
            if found:
                return found
    return ""


def strip_tags(value):
    return re.sub(r"<[^>]+>", " ", value or "")


def cbndata_meta_title(html):
    for pattern in [
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+name=["\']title["\']\s+content=["\']([^"\']+)["\']',
    ]:
        match = re.search(pattern, html, re.I)
        if match:
            return clean_text(match.group(1))
    return ""


def cbndata_meta_content(html):
    parts = []
    for pattern in [
        r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']+)["\']',
        r'<meta\s+property=["\']og:description["\']\s+content=["\']([^"\']+)["\']',
    ]:
        match = re.search(pattern, html, re.I)
        if match:
            parts.append(match.group(1))
    return clean_text(" ".join(parts))


def qianzhan_main_html(url, html):
    if "t.qianzhan.com/" not in url:
        return ""
    html_lower = html.lower()
    start = html_lower.find('<div class="art"')
    if start == -1:
        return ""
    end = html_lower.find('<div class="mt30"', start)
    if end == -1:
        end = html_lower.find('<div class="clf pb10', start)
    return html[start:end] if end != -1 else html[start:]


def iresearch_main_html(html):
    html_lower = html.lower()
    start = html_lower.find('<div class="g-article"')
    if start == -1:
        return ""
    end = html_lower.find('<div class="g-box', start)
    if end == -1:
        end = html_lower.find('<div class="m-page', start)
    return html[start:end] if end != -1 else html[start:]


def huaon_title_from_content(content):
    match = re.match(r"(.{8,120}?)(?:\s+来源：|\s+20\d{2}-\d{1,2}-\d{1,2})", content or "")
    if match:
        return clean_text(match.group(1))
    return clean_text((content or "")[:80])


def stats_gov_extract_page(url, html):
    scoped_html = stats_gov_main_html(html)
    page = generic_extract_page(url, scoped_html)
    title = stats_gov_title(html) or page.title
    content = remove_repeated_phrases(
        page.content,
        [
            "国家统计局",
            "首页",
            "机构",
            "新闻",
            "数据",
            "公开",
            "服务",
            "互动",
            "知识",
            "专题",
        ],
    )
    content = remove_stats_gov_extra_ui_text(remove_stats_gov_ui_text(content))
    return ExtractedPage(
        title=title,
        content=content,
        links=page.links,
        extractor="stats.gov.cn",
    )


def stats_gov_main_html(html):
    html_lower = html.lower()
    start = -1
    for marker in [
        '<div class="detail-content"',
        '<div class="trs_editor"',
        '<div id="zoom"',
        '<div class="article"',
        "<article",
        "<main",
    ]:
        start = html_lower.find(marker)
        if start != -1:
            break
    if start == -1:
        return html
    end_candidates = [
        html_lower.find('<div class="footer"', start),
        html_lower.find('<div class="wrapper-footer"', start),
        html_lower.find('<div class="xg_wz"', start),
        html_lower.find("</article>", start),
        html_lower.find("</main>", start),
        html_lower.find("</body>", start),
    ]
    end_candidates = [index for index in end_candidates if index != -1]
    end = min(end_candidates) if end_candidates else len(html)
    return html[start:end]


def stats_gov_title(html):
    for pattern in [
        r'<meta\s+name=["\']ArticleTitle["\'\']\s+content=["\']([^"\']*)["\']',
        r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']*)["\']',
        r"<h1[^>]*>(.*?)</h1>",
    ]:
        match = re.search(pattern, html, re.I | re.S)
        if match:
            return clean_text(strip_tags(match.group(1)))
    return ""


def remove_repeated_phrases(content, phrases):
    text = content
    for phrase in phrases:
        pattern = rf"(?:{re.escape(phrase)}\s*){{3,}}"
        text = re.sub(pattern, f"{phrase} ", text)
    return clean_text(text)


def remove_stats_gov_ui_text(content):
    text = content
    text = re.sub(r"\|\s*Aa\s*字体：\s*小\s*中\s*大\s*\|\s*分享到：?", " ", text)
    text = re.sub(r"Aa\s*字体：\s*小\s*中\s*大", " ", text)
    text = re.sub(r"分享到：?", " ", text)
    return clean_text(text)


def remove_stats_gov_extra_ui_text(content):
    text = content
    patterns = [
        r"\|\s*Aa\s*字体[:：]\s*小\s*中\s*大\s*\|\s*分享到[:：]?",
        r"Aa\s*字体[:：]\s*小\s*中\s*大",
        r"分享到[:：]?\s*微信\s*微博\s*QQ空间?",
        r"打印本页\s*关闭窗口",
        r"责任编辑[:：]\s*\S+",
        r"来源[:：]\s*国家统计局",
        r"发布时间[:：]\s*20\d{2}[-年]\d{1,2}[-月]\d{1,2}日?",
        r"【字体[:：].*?】",
        r"【关闭窗口】",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text, flags=re.I)
    return clean_text(text)


def strip_before_first_marker(content, markers):
    positions = [content.find(marker) for marker in markers if marker in content]
    if not positions:
        return content
    return clean_text(content[min(positions):])


def remove_chyxx_ui_text(content):
    text = content
    patterns = [
        r"关于我们\s*我的订单\s*",
        r"全站搜索\s*全站\s*智研观点\s*研究报告\s*报告库\s*排行榜\s*资讯\s*数据\s*产业百科\s*搜索\s*",
        r"最新百科\s+.*?\s+首页\s+产业百科\s+",
        r"分享：\s*复制链接\s*",
        r"订购电话\s*400-700-9383、010-60343812、010-60343813\s*",
        r"客服邮箱\s*kefu@chyxx\.com\s*",
        r"价格\s*PDF版:\s*\d+\s*元\s*数量\s*下载订购单\s*立即订购\s*在线咨询\s*下载PDF目录\s*",
        r"版权声明\s*我公司拥有所有研究报告产品的唯一著作权.*?正式授权。",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return clean_text(text)


def remove_huaon_ui_text(content):
    text = content
    patterns = [
        r"公众号\s*企业微信\s*投稿\s*我的订单\s*",
        r"如有投稿需求，请把文章发送到邮箱\s*tougao@huaon\.com\s*一经录用会有专人和您联系\s*公众号客服\s*我知道了\s*",
        r"提交您的需求\s*联系人：\s*职务：\s*电话：\s*邮箱：\s*您的需求：\s*提交\s*",
        r"相关报告：华经产业研究院发布的\s*",
        r"本文采编：.*?$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return clean_text(text)


def remove_yicai_ui_text(content):
    text = content
    patterns = [
        r"跳过广告\(还剩\s*\d+\s*秒\)\s*关闭广告",
        r"首页\s*>\s*新闻\s*>\s*\S+\s*分享到：\s*微信\s*微博\s*QQ\s*分享到微信\s*打开微信，点击底部的“发现”，\s*使用“扫一扫”即可将网页分享至朋友圈。",
        r"分享到：\s*微信\s*微博\s*QQ",
        r"AI帮你提炼,\s*10秒\s*看完要点.*?免责声明\s*",
        r"免责声明\s*前述内容由第一财经“星翼大模型”智能生成.*?yonghu@yicai\.com",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"分享到：?", " ", text)
    return clean_text(text)


def remove_qianzhan_ui_text(content):
    text = content
    patterns = [
        r"请登录\s*\|\s*购物车\s*\|\s*我的订单\s*\|\s*免费注册\s*",
        r"报告服务热线\s*400-068-7188\s*",
        r"客户专线：\s*0755\s*-\s*82925195\s*/\s*82925295\s*",
        r"免费热线：\s*400-068-7188\s*",
        r"售后热线：\s*0755-33013088\s*",
        r"最新订购\s+.*$",
        r"U\s*V\s*c\s*分享到：?\s*",
        r"都在用的报告小程序\s*写文章、做研究、查资料【必备】\s*微信扫一扫，我知道了\s*",
        r"本文来源.*?hezuo@qianzhan\.com\s*",
        r"（图片来源：摄图网）\s*",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    text = re.sub(r"无障碍\s*浏览", " ", text)
    text = re.sub(r"无障碍", " ", text)
    return clean_text(text)


def remove_cbndata_ui_text(content):
    text = content
    patterns = [
        r"未开始\s*",
        r"分享至\s*微信\s*微博\s*",
        r"打开微信.*?朋友圈。",
        r"免责声明.*?$",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return clean_text(text)


def remove_iresearch_ui_text(content):
    text = content
    patterns = [
        r"艾瑞数智\s*\|\s*艾瑞咨询\s*\|\s*艾瑞网\s*\|\s*艾瑞智慧\s*",
        r"艾瑞网\s*首页\s*热点资讯.*?报告\s*",
        r"搜索历史\s*热搜词.*?电商\s*",
        r"来源：艾瑞咨询\s*\d{4}/\d{1,2}/\d{1,2}\s*\d{1,2}:\d{2}:\d{2}\s*分享\s*微信\s*微博\s*腾讯\s*",
        r"所属行业：\s*[^ ]+\s*报告类型：\s*[^ ]+\s*页数：\s*\d+\s*图表：\s*\d+\s*￥\d+\s*加入收藏\s*下载报告\s*在线浏览\s*",
        r"【简版报告说明】.*?实际页面展示为准。\s*",
    ]
    for pattern in patterns:
        text = re.sub(pattern, " ", text)
    return clean_text(text)


DOMAIN_EXTRACTORS = {
    "stats.gov.cn": stats_gov_extract_page,
    "iresearch.cn": iresearch_extract_page,
    "yicai.com": yicai_extract_page,
    "cheaa.com": cheaa_com_extract_page,
    "chyxx.com": chyxx_extract_page,
    "huaon.com": huaon_extract_page,
    "qianzhan.com": qianzhan_extract_page,
    "cbndata.com": cbndata_extract_page,
}
