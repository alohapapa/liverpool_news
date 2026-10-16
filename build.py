"""리버풀 소식 모음 사이트를 만든다.

뉴스(현지 언론·레딧), 부상자(FPL 공식), 일정·결과·순위(ESPN)를 모아
site/index.html 한 파일로 저장한다. 외부 라이브러리 없이 파이썬 기본 기능만 쓴다.
"""

import base64
import datetime as dt
import email.utils
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).parent
SITE_DIR = ROOT / "site"
TEMPLATE = ROOT / "template.html"
CACHE_FILE = ROOT / "cache" / "translations.json"
CAFE_SEEN_FILE = ROOT / "cache" / "cafe_seen.json"  # 카페 글을 처음 본 시각 (검색 API가 작성 시각을 주지 않아서)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"
)
KST = dt.timezone(dt.timedelta(hours=9))
NOW = dt.datetime.now(dt.timezone.utc)

ESPN_TEAM_ID = "364"  # 리버풀
FPL_TEAM_NAME = "Liverpool"
NEWS_MAX_AGE = dt.timedelta(days=3)
NEWS_PER_GROUP = 40
CACHE_KEEP_DAYS = 14
PBKDF2_ITERATIONS = 300_000  # 페이지(JS)에서도 이 값을 그대로 받아 쓴다


def google_news(lang, country, query="Liverpool FC when:2d"):
    q = urllib.parse.quote(query)
    return f"https://news.google.com/rss/search?q={q}&hl={lang}&gl={country}&ceid={country}:{lang}"


# group: 사이트에서 묶어 보여줄 이름 / lang: 기대 언어(구글뉴스는 다른 언어 기사를 걸러낸다)
NEWS_SOURCES = [
    {"name": "BBC Sport", "group": "영국", "lang": "en", "url": "https://feeds.bbci.co.uk/sport/football/teams/liverpool/rss.xml"},
    {"name": "The Guardian", "group": "영국", "lang": "en", "url": "https://www.theguardian.com/football/liverpool/rss"},
    {"name": "Liverpool Echo", "group": "영국", "lang": "en", "url": "https://www.liverpoolecho.co.uk/all-about/liverpool-fc?service=rss"},
    {"name": "Sky Sports", "group": "영국", "lang": "en", "url": "https://www.skysports.com/rss/11669"},
    {"name": "This Is Anfield", "group": "영국", "lang": "en", "url": "https://www.thisisanfield.com/feed/"},
    {"name": "구글뉴스 스페인", "group": "스페인", "lang": "es", "url": google_news("es", "ES"), "google": True},
    {"name": "구글뉴스 이탈리아", "group": "이탈리아", "lang": "it", "url": google_news("it", "IT"), "google": True},
    {"name": "구글뉴스 독일", "group": "독일", "lang": "de", "url": google_news("de", "DE"), "google": True},
    {"name": "구글뉴스 프랑스", "group": "프랑스", "lang": "fr", "url": google_news("fr", "FR"), "google": True},
    {"name": "Reddit r/LiverpoolFC", "group": "레딧", "lang": None, "url": "https://www.reddit.com/r/LiverpoolFC/hot/.rss", "reddit": True},
]

# 이적 루머 탭 출처.
# - 기자 보도: r/LiverpoolFC 가 기자별 신뢰도 등급(Tier) 플레어를 붙여 관리하는 글만 검색한다.
#   제목이 "[Joyce] ..." 처럼 기자 이름으로 시작한다.
# - 언론 루머: 이적 전용 피드 + 나라별 구글뉴스 이적 검색 (이적 관련 단어가 있는 제목만 남긴다)
TRANSFER_SOURCES = [
    {"name": "Reddit 기자 보도", "group": "기자 보도", "lang": None, "reddit": True, "journalist": True,
     "url": "https://www.reddit.com/r/LiverpoolFC/search.rss?q=flair%3ATier&restrict_sr=1&sort=new"},
    {"name": "Liverpool Echo", "group": "언론 루머", "lang": "en",
     "url": "https://www.liverpoolecho.co.uk/all-about/liverpool-fc-transfer-news?service=rss"},
    {"name": "구글뉴스 영국", "group": "언론 루머", "lang": "en", "google": True, "keywords": True,
     "url": google_news("en-GB", "GB", "Liverpool transfer when:3d")},
    {"name": "구글뉴스 스페인", "group": "언론 루머", "lang": "es", "google": True, "keywords": True,
     "url": google_news("es", "ES", "Liverpool fichaje when:3d")},
    {"name": "구글뉴스 이탈리아", "group": "언론 루머", "lang": "it", "google": True, "keywords": True,
     "url": google_news("it", "IT", "Liverpool calciomercato when:3d")},
    {"name": "구글뉴스 독일", "group": "언론 루머", "lang": "de", "google": True, "keywords": True,
     "url": google_news("de", "DE", "Liverpool Transfer when:3d")},
    {"name": "구글뉴스 프랑스", "group": "언론 루머", "lang": "fr", "google": True, "keywords": True,
     "url": google_news("fr", "FR", "Liverpool mercato when:3d")},
]
TRANSFER_MAX_AGE = dt.timedelta(days=4)
JOURNALIST_MAX_AGE = dt.timedelta(days=21)  # 기자 보도는 자주 올라오지 않아서 더 길게 보여준다
TRANSFER_PER_SOURCE = 25

# 제목에 이런 단어가 있어야 이적 기사로 본다 (영·스·이·독·프)
TRANSFER_RE = re.compile(
    r"transfer|\bsign(s|ed|ing)?\b|\bbid\b|offer|\bfee\b|price tag|[£€$]\s?\d|\bdeal\b|target|swoop|\bloan|contract"
    r"|release clause|\btalks\b|move for|interest|linked|fichaj|fichar|oferta|traspaso|cesi[oó]n|renov|cl[aá]usula"
    r"|mercato|trattativ|offerta|nel mirino|rinnovo|acquist|cession|prestito|clausola|wechsel|angebot|abl[oö]se"
    r"|vertrag|leih|verpflicht|transfert|offre|recrue|contrat|\bpr[eê]t\b|piste",
    re.I,
)
LIVERPOOL_RE = re.compile(r"liverpool|\blfc\b|\breds\b|anfield", re.I)
# 여러 구단 이적 소식을 한꺼번에 모은 글 ("Calciomercato Inter LIVE | ...", "Ultimissime ...")
ROUNDUP_RE = re.compile(r"ultimissime|ultim'ora|\bLIVE\s*\||\|\s*LIVE\b|mercato .{0,30}\blive\b", re.I)
# 경기 무료 중계를 사칭하는 스팸·승부 예측 글
SPAM_RE = re.compile(
    r"live ?stream|free stream|streams? (on|free)|how to watch|where to watch|watch .{0,40}\blive\b|\bTV channel"
    r"|en directo|en vivo|diretta tv|dove vedere|pronostic|prediction|betting|cuotas|quote,? analisi|[!@#$+>\[]{3,}",
    re.I,
)

COMPETITIONS = [
    {"slug": "eng.1", "ko": "프리미어리그", "kind": "league"},
    {"slug": "uefa.champions", "ko": "챔피언스리그", "kind": "ucl"},
    {"slug": "eng.league_cup", "ko": "카라바오컵", "kind": "cup"},
    {"slug": "eng.fa", "ko": "FA컵", "kind": "cup"},
]

TEAM_KO = {
    "Liverpool": "리버풀", "Manchester City": "맨체스터 시티", "Arsenal": "아스널",
    "Brighton & Hove Albion": "브라이턴", "Brentford": "브렌트퍼드", "Everton": "에버턴",
    "Chelsea": "첼시", "Leeds United": "리즈", "Hull City": "헐 시티", "Ipswich Town": "입스위치",
    "Newcastle United": "뉴캐슬", "Crystal Palace": "크리스탈 팰리스", "Manchester United": "맨체스터 유나이티드",
    "Coventry City": "코번트리", "Nottingham Forest": "노팅엄 포리스트", "Aston Villa": "아스톤 빌라",
    "Sunderland": "선덜랜드", "AFC Bournemouth": "본머스", "Tottenham Hotspur": "토트넘", "Fulham": "풀럼",
    "West Ham United": "웨스트햄", "Wolverhampton Wanderers": "울버햄튼", "Burnley": "번리",
    "Leicester City": "레스터", "Southampton": "사우샘프턴", "Sheffield United": "셰필드 유나이티드",
    "Wrexham": "렉섬", "Middlesbrough": "미들즈브러", "Norwich City": "노리치", "Watford": "왓퍼드",
    "West Bromwich Albion": "웨스트 브롬", "Stoke City": "스토크", "Swansea City": "스완지",
    "Paris Saint-Germain": "파리 생제르맹", "Barcelona": "바르셀로나", "VfB Stuttgart": "슈투트가르트",
    "Sporting CP": "스포르팅", "Real Madrid": "레알 마드리드", "Real Betis": "레알 베티스",
    "Bayern Munich": "바이에른 뮌헨", "Shakhtar Donetsk": "샤흐타르", "Como": "코모", "Villarreal": "비야레알",
    "Atlético Madrid": "아틀레티코 마드리드", "Club Brugge": "클뤼프 브뤼허", "Borussia Dortmund": "도르트문트",
    "Lens": "랑스", "AEK Athens": "AEK 아테네", "RB Leipzig": "라이프치히", "Sabah FK": "사바흐",
    "Feyenoord Rotterdam": "페예노르트", "Bodo/Glimt": "보되/글림트", "AS Roma": "AS 로마",
    "PSV Eindhoven": "PSV", "Viking FK": "바이킹", "Fenerbahce": "페네르바체", "Internazionale": "인테르",
    "FC Porto": "포르투", "Slavia Prague": "슬라비아 프라하", "Napoli": "나폴리", "LASK Linz": "LASK 린츠",
    "Lille": "릴", "Galatasaray": "갈라타사라이", "Slovan Bratislava": "슬로반 브라티슬라바",
    "Juventus": "유벤투스", "AC Milan": "AC 밀란", "Atalanta": "아탈란타", "Bayer Leverkusen": "레버쿠젠",
    "Eintracht Frankfurt": "프랑크푸르트", "Benfica": "벤피카", "Ajax Amsterdam": "아약스", "Celtic": "셀틱",
    "Marseille": "마르세유", "AS Monaco": "모나코", "Olympiacos": "올림피아코스", "FC Copenhagen": "코펜하겐",
    "Athletic Club": "아틀레틱 빌바오", "Red Bull Salzburg": "잘츠부르크", "Young Boys": "영 보이스",
}

FPL_POS = {1: "GK", 2: "DF", 3: "MF", 4: "FW"}
FPL_STATUS_KO = {"i": "부상", "d": "출전 불투명", "s": "출장 정지", "u": "결장", "n": "출전 불가"}
BODY_KO = {
    "knee": "무릎", "ankle": "발목", "hamstring": "햄스트링", "thigh": "허벅지", "achilles": "아킬레스건",
    "groin": "사타구니", "calf": "종아리", "foot": "발", "back": "허리", "lower back": "허리", "hip": "고관절",
    "shoulder": "어깨", "muscle": "근육", "head": "머리", "toe": "발가락", "hand": "손", "wrist": "손목",
    "rib": "갈비뼈", "adductor": "내전근", "quad": "대퇴사두근", "heel": "뒤꿈치", "leg": "다리",
    "neck": "목", "abdominal": "복부", "elbow": "팔꿈치", "facial": "얼굴", "collarbone": "쇄골",
    "arm": "팔", "finger": "손가락", "pelvic": "골반", "eye": "눈", "jaw": "턱", "knee ligament": "무릎 인대",
    "ankle ligament": "발목 인대", "cruciate ligament": "십자인대", "metatarsal": "중족골", "chest": "가슴",
}
FPL_PHRASE_KO = {
    "unknown return date": "복귀 시점 미정", "illness": "질병", "knock": "타박상",
    "lack of match fitness": "경기 체력 부족", "personal reasons": "개인 사정", "concussion": "뇌진탕",
    "ill": "질병", "virus": "바이러스 감염", "awaiting assessment": "검사 대기 중",
}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def fetch(url, timeout=20, attempts=3):
    last_error = None
    for attempt in range(attempts):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "en,ko;q=0.8"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            last_error = e
            if attempt == attempts - 1:
                break
            if isinstance(e, urllib.error.HTTPError) and e.code == 429:
                # "너무 자주 요청함" — 서버가 알려준 시간(최대 60초)만큼, 없으면 30초 기다린다
                try:
                    wait = min(int(e.headers.get("Retry-After", "30")), 60)
                except ValueError:
                    wait = 30
                log(f"  요청이 많다고 해서 {wait}초 기다렸다 다시 시도합니다.")
                time.sleep(wait)
            else:
                time.sleep(2)
    raise last_error


def fetch_json(url):
    return json.loads(fetch(url).decode("utf-8"))


# ---------------------------------------------------------------- 번역 (무료 구글 번역, 캐시 사용)

class Translator:
    """무료 구글 번역. 한 번 번역한 문장은 캐시에 저장해 다시 요청하지 않는다.

    구글이 막으면(요청 과다) 잠시 번역을 쉬었다가 다시 시도하고, 그동안 나온 제목은 원문으로 둔다.
    번역 못 한 제목은 캐시에 없으니 다음 실행(1시간 뒤)에서 다시 번역을 시도한다.
    """

    COOLDOWN = 60       # 막혔을 때 쉬는 시간(초). 쉴 때마다 늘어난다
    MAX_COOLDOWNS = 3   # 이만큼 쉬고도 막히면 이번 실행에서는 번역을 그만둔다

    def __init__(self):
        self.cache = {}
        if CACHE_FILE.exists():
            try:
                self.cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
            except ValueError:
                self.cache = {}
        self.cooldowns = 0
        self.resume_at = 0.0
        self.disabled = False
        self.skipped = 0
        self.today = NOW.date().isoformat()

    def translate(self, text):
        """(한국어, 감지된 언어) 를 돌려준다. 실패하면 (None, None)."""
        text = text.strip()
        if not text:
            return None, None
        hit = self.cache.get(text)
        if hit:
            hit["seen"] = self.today
            return hit["ko"], hit["lang"]
        if self.disabled or time.monotonic() < self.resume_at:
            self.skipped += 1
            return None, None
        url = (
            "https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=ko&dt=t&q="
            + urllib.parse.quote(text)
        )
        try:
            data = json.loads(fetch(url, timeout=15, attempts=2).decode("utf-8"))
            ko = "".join(seg[0] for seg in data[0] if seg and seg[0])
            lang = data[2]
        except Exception as e:
            self.skipped += 1
            self.cooldowns += 1
            if self.cooldowns > self.MAX_COOLDOWNS:
                log(f"  번역 실패: {e} — 이번 실행에서는 번역을 그만둡니다 (다음 실행에서 이어서 번역).")
                self.disabled = True
            else:
                wait = self.COOLDOWN * self.cooldowns
                log(f"  번역 실패: {e} — {wait}초 동안 번역을 쉬었다가 다시 시도합니다.")
                self.resume_at = time.monotonic() + wait
            return None, None
        self.cache[text] = {"ko": ko, "lang": lang, "seen": self.today}
        time.sleep(0.15)
        return ko, lang

    def save(self):
        cutoff = (NOW.date() - dt.timedelta(days=CACHE_KEEP_DAYS)).isoformat()
        kept = {k: v for k, v in self.cache.items() if v.get("seen", "") >= cutoff}
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(kept, ensure_ascii=False), encoding="utf-8")


# ---------------------------------------------------------------- 뉴스

ATOM = "{http://www.w3.org/2005/Atom}"


def parse_date(text):
    if not text:
        return None
    text = text.strip()
    try:
        d = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        try:
            d = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d


def clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", "", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def read_feed(source):
    root = ET.fromstring(fetch(source["url"]))
    items = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        publisher = clean(it.findtext("source")) if source.get("google") else source["name"]
        # 구글뉴스 제목은 "기사 제목 - 언론사" 형태라 언론사 이름을 떼어낸다 (번역되면 안 되므로)
        if source.get("google") and publisher and title.endswith(" - " + publisher):
            title = title[: -len(" - " + publisher)].strip()
        items.append({
            "title": title,
            "link": (it.findtext("link") or "").strip(),
            "published": parse_date(it.findtext("pubDate")),
            "source": publisher or source["name"],
        })
    for entry in root.iter(ATOM + "entry"):
        link_el = entry.find(ATOM + "link")
        items.append({
            "title": clean(entry.findtext(ATOM + "title")),
            "link": link_el.get("href") if link_el is not None else "",
            "published": parse_date(entry.findtext(ATOM + "published") or entry.findtext(ATOM + "updated")),
            "source": source["name"],
        })
    return items


REDDIT_PAUSE = 3  # 레딧은 연달아 요청하면 잠시 막으므로 사이에 쉰다


def collect_items(sources, translator, kind):
    """출처 목록에서 기사를 모아 번역한다. kind: "news" 또는 "transfer"."""
    out, errors, seen_titles = [], [], set()
    per_source = TRANSFER_PER_SOURCE if kind == "transfer" else NEWS_PER_GROUP
    for source in sources:
        log(f"{'이적' if kind == 'transfer' else '뉴스'}: {source['name']}")
        if source.get("reddit"):
            time.sleep(REDDIT_PAUSE)
        try:
            items = read_feed(source)
        except Exception as e:
            log(f"  불러오기 실패: {e}")
            errors.append(source["name"])
            continue
        kept = 0
        for item in items:
            title = item["title"]
            if kept >= per_source or not title or not item["link"] or SPAM_RE.search(title):
                continue
            journalist = None
            if source.get("journalist"):
                m = re.match(r"\[([^\]]{1,40})\]\s*[-–:]?\s*(.+)", title)
                if m:
                    journalist, title = m.group(1).strip(), m.group(2).strip()
            if source.get("reddit") and not source.get("journalist"):
                if title.lower().startswith(("daily discussion", "[daily", "free talk")):
                    continue
            else:
                max_age = JOURNALIST_MAX_AGE if source.get("journalist") else (
                    TRANSFER_MAX_AGE if kind == "transfer" else NEWS_MAX_AGE)
                if item["published"] is None or NOW - item["published"] > max_age:
                    continue
            if source.get("keywords") and not (
                    TRANSFER_RE.search(title) and LIVERPOOL_RE.search(title) and not ROUNDUP_RE.search(title)):
                continue
            key = re.sub(r"\W+", "", title.lower())
            if key in seen_titles:
                continue
            ko, lang = translator.translate(title)
            # 구글뉴스 스페인 칸에 영어·인도네시아어 기사가 섞여 들어오는 것을 걸러낸다
            if source["lang"] and lang and not lang.startswith(source["lang"][:2]):
                continue
            seen_titles.add(key)
            kept += 1
            out.append({
                "title": title,
                "title_ko": ko if ko and ko != title else None,
                "link": item["link"],
                "source": item["source"],
                "group": source["group"],
                "journalist": journalist,
                "published": item["published"].isoformat() if item["published"] else None,
            })
        log(f"  {kept}건")
    out.sort(key=lambda n: n["published"] or "", reverse=True)
    groups = []
    for s in sources:
        if s["group"] not in groups:
            groups.append(s["group"])
    return out, groups, errors


# ---------------------------------------------------------------- 네이버 카페 (lfckorea, 네이버 API HUB 카페글 검색)

CAFE_URL = "cafe.naver.com/lfckorea"
CAFE_QUERY = "리버풀"  # 카페 이름에 '리버풀'이 들어 있어서 이 카페 글 대부분이 이 검색어로 잡힌다
CAFE_API = "https://naverapihub.apigw.ntruss.com/search/v1/cafearticle"
CAFE_KEEP = 300
# 뉴스 게시판 글은 보통 "[파브리치오 로마노] ...", "[OptaJoe] ..." 처럼 [출처]로 시작하거나 인용문이다.
# 검색 결과에는 게시판 이름이 없어서 제목 모양으로 뉴스 글을 가려낸다.
CAFE_NEWS_RE = re.compile(r'^\s*\[[^\]]{1,40}\]|["“”]')
CAFE_NOT_NEWS_TAG = re.compile(r"^\s*\[(잡담|질문|후기|공지|이벤트|직관|유머|나눔|구매|판매|가입|인사|짤|움짤|번역요청)\]")


def collect_cafe():
    """lfckorea 글 목록. 네이버 키가 없으면 (None, "no_key")."""
    cid, secret = os.environ.get("NAVER_CLIENT_ID", "").strip(), os.environ.get("NAVER_CLIENT_SECRET", "").strip()
    if not cid or not secret:
        log("카페: NAVER_CLIENT_ID/SECRET 이 없어 건너뜁니다.")
        return None, "no_key"
    log("카페: lfckorea")
    url = f"{CAFE_API}?query={urllib.parse.quote(CAFE_QUERY)}&display=100&start=1&sort=date&format=json"
    req = urllib.request.Request(url, headers={
        "X-NCP-APIGW-API-KEY-ID": cid, "X-NCP-APIGW-API-KEY": secret, "User-Agent": UA,
    })
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            items = json.loads(r.read().decode("utf-8")).get("items", [])
    except Exception as e:
        # 키 값이 기록에 남지 않도록 오류 종류만 남긴다
        log(f"  불러오기 실패: {type(e).__name__} {getattr(e, 'code', '')}")
        return None, "error"

    seen = {}
    if CAFE_SEEN_FILE.exists():
        try:
            seen = json.loads(CAFE_SEEN_FILE.read_text(encoding="utf-8"))
        except ValueError:
            seen = {}
    out = []
    for it in items:
        link = it.get("link", "")
        m = re.search(r"cafe\.naver\.com/lfckorea/(\d+)", link)
        if CAFE_URL not in (it.get("cafeurl", "") + link) or not m:
            continue
        aid = m.group(1)
        title = clean(it.get("title"))
        seen.setdefault(aid, NOW.isoformat())
        is_news = bool(CAFE_NEWS_RE.search(title)) and not CAFE_NOT_NEWS_TAG.search(title)
        out.append({
            "id": int(aid),
            "title": title,
            "snippet": clean(it.get("description"))[:120],
            "link": f"https://cafe.naver.com/lfckorea/{aid}",
            "source": "lfckorea",
            "group": "뉴스" if is_news else "기타",
            "published": seen[aid],  # 처음 확인한 시각
        })
    out.sort(key=lambda x: x["id"], reverse=True)
    keep = dict(sorted(seen.items(), key=lambda kv: int(kv[0]), reverse=True)[:CAFE_KEEP])
    CAFE_SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    CAFE_SEEN_FILE.write_text(json.dumps(keep), encoding="utf-8")
    log(f"  {len(out)}건 (뉴스로 보이는 글 {sum(1 for x in out if x['group'] == '뉴스')}건)")
    return out, "ok"


# ---------------------------------------------------------------- 부상자 (FPL 공식 데이터)

def fpl_phrase_ko(phrase):
    p = phrase.strip().rstrip(".")
    low = p.lower()
    if low in FPL_PHRASE_KO:
        return FPL_PHRASE_KO[low]
    m = re.fullmatch(r"(.+?) (injury|problem|strain|surgery|fracture)", low)
    if m and m.group(1) in BODY_KO:
        suffix = {"injury": "부상", "problem": "문제", "strain": "염좌", "surgery": "수술", "fracture": "골절"}[m.group(2)]
        return f"{BODY_KO[m.group(1)]} {suffix}"
    m = re.fullmatch(r"(\d+)% chance of playing", low)
    if m:
        return f"출전 가능성 {m.group(1)}%"
    m = re.fullmatch(r"expected back (\d{1,2}) (\w{3})\w*", low)
    if m and m.group(2) in MONTHS:
        return f"{MONTHS[m.group(2)]}월 {int(m.group(1))}일 복귀 예상"
    m = re.fullmatch(r"suspended until (\d{1,2}) (\w{3})\w*", low)
    if m and m.group(2) in MONTHS:
        return f"{MONTHS[m.group(2)]}월 {int(m.group(1))}일까지 출장 정지"
    return None


def fpl_news_ko(news, translator):
    parts = [fpl_phrase_ko(p) for p in news.split(" - ")]
    if all(parts):
        return " · ".join(parts)
    ko, _ = translator.translate(news)
    return ko


def collect_injuries(translator):
    log("부상자: FPL")
    data = fetch_json("https://fantasy.premierleague.com/api/bootstrap-static/")
    team_id = next(t["id"] for t in data["teams"] if t["name"] == FPL_TEAM_NAME)
    out = []
    for p in data["elements"]:
        if p["team"] != team_id or p["status"] == "a":
            continue
        news = (p.get("news") or "").strip()
        # 이적·임대로 팀을 떠난 선수는 부상자 목록에서 뺀다
        if re.match(r"(has )?(joined|left)\b", news, re.I):
            continue
        out.append({
            "name": p["web_name"],
            "full": f"{p['first_name']} {p['second_name']}".strip(),
            "pos": FPL_POS.get(p["element_type"], ""),
            "status": p["status"],
            "status_ko": FPL_STATUS_KO.get(p["status"], p["status"]),
            "chance": p.get("chance_of_playing_next_round"),
            "news": news,
            "news_ko": fpl_news_ko(news, translator) if news else None,
            "added": p.get("news_added"),
        })
    order = {"s": 0, "i": 1, "d": 2, "n": 3, "u": 4}
    out.sort(key=lambda x: (order.get(x["status"], 9), x["name"]))
    log(f"  {len(out)}명")
    return out


def explain_suspensions(injuries, matches):
    """FPL의 "Suspended until 25 Oct" 를 실제 일정에 맞춰 풀어 쓴다.

    FPL은 프리미어리그 기준이고, 적힌 날짜는 징계가 풀려 다시 뛸 수 있는 날이다.
    그 날짜 전의 프리미어리그 경기는 결장, 그 날짜 이후 첫 경기부터 출전 가능.
    """
    pl = [m for m in matches if m["comp_slug"] == "eng.1" and m["state"] != "post"]
    for p in injuries or []:
        m = re.search(r"suspended until (\d{1,2}) (\w{3})", p["news"], re.I)
        if p["status"] != "s" or not m or m.group(2).lower() not in MONTHS:
            continue
        added = parse_date(p.get("added")) or NOW
        month, day = MONTHS[m.group(2).lower()], int(m.group(1))
        year = added.year + (1 if month < added.month else 0)
        free_from = dt.date(year, month, day)
        uk_day = lambda x: (parse_date(x["date"]) + dt.timedelta(hours=1)).date()  # 영국 날짜 기준으로 비교
        brief = lambda x: {"opp": x["away"] if x["home"] == TEAM_KO["Liverpool"] else x["home"], "date": x["date"]}
        p["ban"] = {
            "missed": [brief(x) for x in pl if uk_day(x) < free_from],
            "back": next((brief(x) for x in pl if uk_day(x) >= free_from), None),
        }
        p["status_ko"] = "PL 출장 정지"
        p["news_ko"] = "프리미어리그 출장 정지"


# ---------------------------------------------------------------- 일정·결과·순위 (ESPN)

def team_ko(name):
    return TEAM_KO.get(name, name)


# 순위표는 칸이 많아 휴대폰에서 좁으므로, 긴 이름은 흔히 부르는 짧은 이름으로 보여준다
TEAM_SHORT = {
    "Manchester City": "맨시티", "Manchester United": "맨유", "Nottingham Forest": "노팅엄",
    "Crystal Palace": "팰리스", "Brighton & Hove Albion": "브라이턴", "Tottenham Hotspur": "토트넘",
    "Wolverhampton Wanderers": "울버햄튼", "West Ham United": "웨스트햄", "Sheffield United": "셰필드",
    "West Bromwich Albion": "웨스트 브롬", "Paris Saint-Germain": "PSG", "Bayern Munich": "뮌헨",
    "Real Madrid": "레알", "Atlético Madrid": "아틀레티코", "Borussia Dortmund": "도르트문트",
    "Club Brugge": "브뤼허", "Slavia Prague": "슬라비아", "Slovan Bratislava": "슬로반",
    "Feyenoord Rotterdam": "페예노르트", "Bayer Leverkusen": "레버쿠젠", "Eintracht Frankfurt": "프랑크푸르트",
    "Shakhtar Donetsk": "샤흐타르", "Galatasaray": "갈라타사라이", "Red Bull Salzburg": "잘츠부르크",
    "Athletic Club": "빌바오", "LASK Linz": "LASK", "AEK Athens": "AEK", "AS Roma": "로마", "AC Milan": "밀란",
}


def team_short(name):
    return TEAM_SHORT.get(name) or team_ko(name)


def logo_of(team):
    logos = team.get("logos") or []
    if logos:
        return logos[0].get("href")
    return team.get("logo")


def season_start():
    year = NOW.year if NOW.month >= 7 else NOW.year - 1
    return dt.datetime(year, 7, 1, tzinfo=dt.timezone.utc)


def parse_event(event, comp):
    c = event["competitions"][0]
    status = c["status"]["type"]
    home = next(x for x in c["competitors"] if x["homeAway"] == "home")
    away = next(x for x in c["competitors"] if x["homeAway"] == "away")

    def score(x):
        s = x.get("score")
        if isinstance(s, dict):
            return s.get("displayValue")
        return s

    notes = [n.get("headline") for n in c.get("notes", []) if n.get("headline")]
    lfc = home if home["team"]["id"] == ESPN_TEAM_ID else away
    opp = away if lfc is home else home
    result = None
    if status["completed"]:
        if lfc.get("winner"):
            result = "W"
        elif opp.get("winner"):
            result = "L"
        else:
            result = "D"
    return {
        "id": event["id"],
        "date": event["date"],
        "comp": comp["ko"],
        "comp_slug": comp["slug"],
        "home": team_ko(home["team"]["displayName"]),
        "away": team_ko(away["team"]["displayName"]),
        "home_logo": logo_of(home["team"]),
        "away_logo": logo_of(away["team"]),
        "home_score": score(home) if status["state"] != "pre" else None,
        "away_score": score(away) if status["state"] != "pre" else None,
        "state": status["state"],
        "detail": status.get("shortDetail"),
        "result": result,
        "venue": (c.get("venue") or {}).get("fullName"),
        "round": round_key((event.get("seasonType") or {}).get("name")),
        "leg": (c.get("leg") or {}).get("value"),
        "note": notes[0] if notes else None,  # 예: "2nd Leg - Liverpool advance 4-1 on aggregate"
    }


def collect_matches():
    matches = {}
    base = "https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/teams/" + ESPN_TEAM_ID + "/schedule"
    start = season_start()
    for comp in COMPETITIONS:
        for suffix in ("", "?fixture=true"):
            url = base.format(slug=comp["slug"]) + suffix
            try:
                events = fetch_json(url).get("events", [])
            except Exception as e:
                log(f"  {comp['ko']} 일정 실패: {e}")
                continue
            for e in events:
                when = parse_date(e["date"])
                if when and when >= start:
                    matches[e["id"]] = parse_event(e, comp)
    out = sorted(matches.values(), key=lambda m: m["date"])
    log(f"경기: {len(out)}개")
    return out


def collect_standings(slug):
    data = fetch_json(f"https://site.api.espn.com/apis/v2/sports/soccer/{slug}/standings")
    entries = data["children"][0]["standings"]["entries"]
    rows = []
    for e in entries:
        stats = {s["name"]: s.get("displayValue") for s in e["stats"]}
        rows.append({
            "rank": int(stats.get("rank") or 0),
            "team": team_ko(e["team"]["displayName"]),
            "short": team_short(e["team"]["displayName"]),
            "logo": logo_of(e["team"]),
            "p": stats.get("gamesPlayed"), "w": stats.get("wins"), "d": stats.get("ties"),
            "l": stats.get("losses"), "gf": stats.get("pointsFor"), "ga": stats.get("pointsAgainst"),
            "gd": stats.get("pointDifferential"), "pts": stats.get("points"),
            "me": e["team"]["id"] == ESPN_TEAM_ID,
        })
    rows.sort(key=lambda r: r["rank"])
    return rows


def ucl_zone(rank):
    if rank <= 8:
        return "16강 직행권"
    if rank <= 24:
        return "플레이오프권"
    return "탈락권"


# ESPN 라운드 이름 → 짧은 키. "quarterfinal"·"semifinal" 안에 "final"이 들어 있어서 순서가 중요하다.
ROUND_PATTERNS = [
    ("league phase", "LP"), ("playoff", "PO"), ("round of 16", "R16"), ("quarterfinal", "QF"),
    ("semifinal", "SF"), ("final", "F"), ("first round", "R1"), ("second round", "R2"),
    ("third round", "R3"), ("fourth round", "R4"), ("fifth round", "R5"),
]
ROUND_KO = {
    "LP": "리그 페이즈", "PO": "플레이오프", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승",
    "R1": "1라운드", "R2": "2라운드", "R3": "3라운드", "R4": "4라운드", "R5": "5라운드",
}
ROUND_SHORT = {"LP": "리그", "PO": "PO", "R16": "16강", "QF": "8강", "SF": "4강", "F": "결승",
               "R1": "1R", "R2": "2R", "R3": "3R", "R4": "4R", "R5": "5R"}
# 대회별 단계 (리버풀이 처음 참가하는 라운드 이전 단계는 실제 경기 기록을 보고 잘라낸다)
LADDERS = {
    "uefa.champions": ["LP", "PO", "R16", "QF", "SF", "F"],
    "eng.league_cup": ["R1", "R2", "R3", "R4", "QF", "SF", "F"],
    "eng.fa": ["R3", "R4", "R5", "QF", "SF", "F"],
}
DEFAULT_ENTRY = {"eng.league_cup": "R3", "eng.fa": "R3"}
UCL_LEAGUE_GAMES = 8


def round_key(name):
    low = (name or "").lower()
    for pattern, key in ROUND_PATTERNS:
        if pattern in low:
            return key
    return None


def tie_outcome(m):
    """끝난 컵 경기 하나로 그 라운드를 통과했는지 판단한다: advanced / out / pending."""
    agg = re.search(r"(?:^|- )(.+?) advances?\b", m.get("note") or "")
    if agg:
        return "advanced" if "liverpool" in agg.group(1).lower() else "out"
    if m.get("leg") == 1:
        return "pending"  # 1차전만 끝남
    return {"W": "advanced", "L": "out"}.get(m["result"], "pending")


def next_info(m):
    lfc_home = m["home"] == TEAM_KO["Liverpool"]
    return {
        "opp": m["away"] if lfc_home else m["home"],
        "opp_logo": m["away_logo"] if lfc_home else m["home_logo"],
        "home": lfc_home,
        "date": m["date"],
        "round": ROUND_KO.get(m["round"], ""),
        "leg": f"{m['leg']}차전" if m.get("leg") else "",
    }


def cup_progress(slug, ms, ucl_me):
    """컵대회 진척도: 단계별 상태(done/current/out/won/skip/todo)와 요약 문구."""
    ladder = LADDERS[slug]
    upcoming = [m for m in ms if m["state"] != "post"]
    done = [m for m in ms if m["state"] == "post"]
    seen = {m["round"] for m in ms if m["round"]}
    state, cur = "not_started", None

    if upcoming:
        state, cur = "alive", upcoming[0]["round"]
    elif done:
        last = done[-1]
        cur = last["round"]
        if cur == "LP":
            # 리그 페이즈가 끝나면 순위로 다음 단계가 정해진다
            if ucl_me and int(ucl_me["p"] or 0) >= UCL_LEAGUE_GAMES:
                if ucl_me["rank"] <= 8:
                    state, cur = "waiting", "R16"
                elif ucl_me["rank"] <= 24:
                    state, cur = "waiting", "PO"
                else:
                    state = "out"
            else:
                state = "alive"
        else:
            outcome = tie_outcome(last)
            if outcome == "out":
                state = "out"
            elif outcome == "advanced" and cur == "F":
                state = "won"
            elif outcome == "advanced" and cur in ladder:
                state, cur = "waiting", ladder[min(ladder.index(cur) + 1, len(ladder) - 1)]
            else:
                state = "alive"

    entry_candidates = [ladder.index(k) for k in seen if k in ladder]
    default_entry = DEFAULT_ENTRY.get(slug)
    if not entry_candidates and default_entry:
        entry_candidates = [ladder.index(default_entry)]
    keys = ladder[min(entry_candidates):] if entry_candidates else ladder
    cur_idx = keys.index(cur) if cur in keys else -1

    steps = []
    for i, k in enumerate(keys):
        if state == "not_started" or i > cur_idx:
            s = "todo"
        elif i < cur_idx:
            s = "skip" if (k == "PO" and k not in seen) else "done"
        else:
            s = {"out": "out", "won": "won"}.get(state, "current")
        steps.append({"label": ROUND_SHORT[k], "state": s})

    cur_ko = ROUND_KO.get(cur, "")
    if state == "won":
        status, tone = "우승!", "good"
    elif state == "out":
        status, tone = (f"{cur_ko}에서 탈락" if cur_ko else "탈락"), "bad"
    elif state == "waiting":
        status, tone = f"{cur_ko} 진출 · 대진 추첨 대기", "good"
    elif state == "alive":
        status, tone = f"{cur_ko} 진행 중", "good"
    else:
        status, tone = "아직 시작 전", "neutral"
    return {
        "state": state,
        "status": status,
        "tone": tone,
        "steps": steps,
        "next": next_info(upcoming[0]) if upcoming else None,
        "alive": state in ("alive", "waiting"),
    }


def competition_status(matches, standings):
    out = []
    for comp in COMPETITIONS:
        ms = [m for m in matches if m["comp_slug"] == comp["slug"]]
        done = [m for m in ms if m["state"] == "post"]
        upcoming = [m for m in ms if m["state"] != "post"]
        item = {"name": comp["ko"], "status": "", "detail": "", "tone": "neutral",
                "steps": None, "next": next_info(upcoming[0]) if upcoming else None, "alive": True}
        if comp["kind"] == "league":
            me = next((r for r in standings.get("pl", []) if r["me"]), None)
            if me:
                item["status"] = f"{me['rank']}위"
                item["detail"] = f"{me['p']}경기 {me['w']}승 {me['d']}무 {me['l']}패 · 승점 {me['pts']}"
                item["tone"] = "good" if me["rank"] <= 4 else "neutral"
        else:
            ucl_me = next((r for r in standings.get("ucl", []) if r["me"]), None)
            item.update(cup_progress(comp["slug"], ms, ucl_me))
            last = done[-1] if done else None
            if comp["kind"] == "ucl" and ucl_me and item["state"] == "alive" and (last or {}).get("round", "LP") == "LP":
                item["status"] = f"리그 페이즈 {ucl_me['rank']}위 · {ucl_zone(ucl_me['rank'])}"
                item["tone"] = {"16강 직행권": "good", "플레이오프권": "neutral", "탈락권": "bad"}[ucl_zone(ucl_me["rank"])]
                item["detail"] = f"{ucl_me['p']}경기 {ucl_me['w']}승 {ucl_me['d']}무 {ucl_me['l']}패 · 승점 {ucl_me['pts']}"
            elif last:
                item["detail"] = f"최근: {last['home']} {last['home_score']}-{last['away_score']} {last['away']}"
                if last.get("note") and "advance" in last["note"]:
                    agg = re.search(r"(\d+-\d+) on aggregate", last["note"])
                    if agg:
                        item["detail"] += f" (합산 {agg.group(1)})"
            elif item["state"] == "not_started" and comp["slug"] == "eng.fa":
                item["detail"] = "프리미어리그 팀은 1월 3라운드부터 참가해요"
        out.append(item)
    return out


# ---------------------------------------------------------------- 조립

def safe(label, fn, default, errors):
    try:
        return fn()
    except Exception as e:
        log(f"{label} 실패: {e}")
        errors.append(label)
        return default


def encrypt(text, code):
    """접속 코드로 페이지 내용을 잠근다 (AES-GCM, 키는 PBKDF2-SHA256으로 만든다).

    페이지는 브라우저 기본 암호 기능(WebCrypto)으로 같은 방식으로 풀어낸다.
    """
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, iv = os.urandom(16), os.urandom(12)
    key = hashlib.pbkdf2_hmac("sha256", code.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    sealed = AESGCM(key).encrypt(iv, text.encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"locked": True, "salt": b64(salt), "iv": b64(iv), "iter": PBKDF2_ITERATIONS, "data": b64(sealed)}


LAST_DATA_FILE = ROOT / "cache" / "last_data.json"  # 직전 전체 업데이트 결과 (카페만 새로 고칠 때 재사용)
FULL_EVERY = dt.timedelta(minutes=50)  # 10분마다 실행되므로, 마지막 전체 업데이트가 이만큼 지났으면 다시 전체 업데이트


def write_page(data):
    """데이터를 (접속 코드로 잠가서) site/index.html 로 쓴다."""
    payload = json.dumps(data, ensure_ascii=False)
    code = os.environ.get("SITE_CODE", "").strip()
    if code:
        payload = json.dumps(encrypt(payload, code))
        log("접속 코드로 잠갔습니다.")
    else:
        log("SITE_CODE가 없어 잠그지 않았습니다 (내 PC 미리보기용).")
    payload = payload.replace("</", "<\\/")
    page = TEMPLATE.read_text(encoding="utf-8").replace("/*__DATA__*/null", payload)
    SITE_DIR.mkdir(exist_ok=True)
    (SITE_DIR / "index.html").write_text(page, encoding="utf-8")
    (SITE_DIR / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")
    LAST_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    LAST_DATA_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def load_last_data():
    if not LAST_DATA_FILE.exists():
        return None
    try:
        return json.loads(LAST_DATA_FILE.read_text(encoding="utf-8"))
    except ValueError:
        return None


def main():
    # 10분마다 실행된다. 직전 전체 업데이트가 최근이면 카페 글만 새로 가져와서 다시 만든다.
    # (레딧·무료 번역은 자주 요청하면 막히므로 1시간에 한 번만)
    last = load_last_data()
    force_full = os.environ.get("FORCE_FULL", "").lower() == "true" or "--full" in sys.argv
    if last and not force_full:
        age = NOW - (parse_date(last.get("generated")) or NOW - FULL_EVERY)
        if age < FULL_EVERY:
            log(f"카페만 새로 고칩니다 (마지막 전체 업데이트 {int(age.total_seconds() // 60)}분 전).")
            cafe, cafe_status = collect_cafe()
            if cafe_status == "ok":
                last["cafe"], last["cafe_status"], last["cafe_checked"] = cafe, cafe_status, NOW.isoformat()
            elif last.get("cafe") is None:
                last["cafe_status"] = cafe_status  # 실패했으면 직전 목록을 그대로 두고, 목록이 아예 없을 때만 상태를 알린다
            write_page(last)
            log(f"완료: 카페만 업데이트 ({cafe_status})")
            return
    full_update()


def full_update():
    translator = Translator()
    errors = []
    # 이적 루머를 먼저 모은다: 레딧 기자 보도가 이번 실행의 첫 레딧 요청이 되고,
    # 일반 레딧 글(뉴스 목록 맨 끝)과는 몇 분 간격이 생겨 레딧이 막을 가능성이 줄어든다.
    transfers, transfer_groups, transfer_errors = collect_items(TRANSFER_SOURCES, translator, "transfer")
    news, groups, news_errors = collect_items(NEWS_SOURCES, translator, "news")
    injuries = safe("부상자", lambda: collect_injuries(translator), None, errors)
    matches = safe("경기 일정", collect_matches, [], errors)
    explain_suspensions(injuries, matches)
    cafe, cafe_status = collect_cafe()
    standings = {
        "pl": safe("프리미어리그 순위", lambda: collect_standings("eng.1"), [], errors),
        "ucl": safe("챔피언스리그 순위", lambda: collect_standings("uefa.champions"), [], errors),
    }
    translator.save()

    data = {
        "generated": NOW.isoformat(),
        "news": news,
        "news_groups": groups,
        "news_errors": news_errors,
        "transfers": transfers,
        "transfer_groups": transfer_groups,
        "transfer_errors": transfer_errors,
        "cafe": cafe,
        "cafe_status": cafe_status,
        "cafe_checked": NOW.isoformat(),
        "injuries": injuries,
        "matches": matches,
        "standings": standings,
        "competitions": competition_status(matches, standings),
        "errors": errors,
    }
    write_page(data)
    journalist = sum(1 for t in transfers if t["group"] == "기자 보도")
    log(f"완료: 뉴스 {len(news)}건, 이적 {len(transfers)}건 (기자 보도 {journalist}건), "
        f"번역 못 한 제목 {translator.skipped}개, 실패한 출처 {news_errors + transfer_errors + errors or '없음'}")


if __name__ == "__main__":
    main()
