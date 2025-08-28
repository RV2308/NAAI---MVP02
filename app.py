import streamlit as st
import requests, json, re
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparse

# =========================
# App config
# =========================
st.set_page_config(page_title="News Agent — Personalized & Actionable", page_icon="🗞️", layout="wide")
IST = timezone(timedelta(hours=5, minutes=30))

# Secrets (Streamlit Cloud → App ▸ Settings ▸ Secrets)
NEWSAPI_KEY = st.secrets["NEWSAPI_KEY"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# =========================
# Utilities & constants
# =========================
TABLOID = {
    "TMZ","Daily Mail","Page Six","The Sun","US Weekly","Radar Online","E! Online",
    "Perez Hilton","Hollywood Life","The Mirror","OK! Magazine"
}
LOW_SIG = [
    "butt","yacht","kissed","wardrobe malfunction","dating rumors",
    "spotted with","baby bump","steamy photos"
]
MAJOR = {
    "Reuters","AP News","BBC News","The Guardian","Financial Times","Bloomberg",
    "The Wall Street Journal","The New York Times","Al Jazeera English","CNBC","Forbes",
    "The Hindu","The Economic Times","Mint","Indian Express","Business Standard","NDTV"
}

LOCAL_DOMAINS = {
    "in": [
        "thehindu.com","indianexpress.com","hindustantimes.com","livemint.com",
        "economictimes.indiatimes.com","business-standard.com","ndtv.com",
        "timesofindia.indiatimes.com","moneycontrol.com","thewire.in","scroll.in",
        "theprint.in","newindianexpress.com","news18.com","deccanherald.com","dnaindia.com"
    ],
    "us": ["nytimes.com","wsj.com","washingtonpost.com","apnews.com","reuters.com","cnn.com","npr.org"],
    "gb": ["bbc.co.uk","theguardian.com","ft.com","telegraph.co.uk","independent.co.uk","sky.com"],
    "au": ["abc.net.au","smh.com.au","theaustralian.com.au","theage.com.au","news.com.au"],
    "sg": ["straitstimes.com","channelnewsasia.com","todayonline.com","businesstimes.com.sg"],
    "ca": ["cbc.ca","ctvnews.ca","theglobeandmail.com","nationalpost.com","financialpost.com"],
}

COUNTRY_KEYWORDS = {
    "in": [
        "india","indian","delhi","mumbai","bengaluru","bangalore","chennai","kolkata","hyderabad",
        "rbi","rupee","parliament","loksabha","lok sabha","rajya sabha","modi","cabinet","supreme court",
        "fssai","gst","uidai","sebi","nirf","iit","iim","msp","monsoon","isro","niti aayog","bharat"
    ],
    "us": ["united states","u.s.","us ","washington","fed","powell","dollar","congress","supreme court"],
    "gb": ["uk ","united kingdom","britain","london","westminster","boe","pound","downing street"],
    "au": ["australia","canberra","rba","aussie","melbourne","sydney"],
    "sg": ["singapore","mas","jurong","ntu","nus","singdollar","sing dollar"],
    "ca": ["canada","ottawa","bank of canada","boc","loonie","toronto","vancouver","quebec"],
}

CATEGORY_QUERIES = {
    "tech": "technology OR AI OR software OR chips OR semiconductors OR startups OR cyber security OR apple OR google OR microsoft",
    "finance": "markets OR stocks OR equities OR bonds OR banking OR fintech OR IPO OR RBI OR SEC OR SEBI OR interest rates",
    "economy": "economy OR GDP OR inflation OR unemployment OR fiscal OR budget OR trade OR monetary policy OR central bank",
    "health": "health OR healthcare OR wellness OR nutrition OR mental health OR covid OR vaccine OR WHO OR medical research",
    "global": "world OR geopolitics OR ceasefire OR climate OR war OR summit OR sanctions OR trade deal"
}

INDIA_RSS = [
    "https://www.thehindu.com/feeder/default.rss",
    "https://indianexpress.com/section/india/feed/",
    "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml",
    "https://www.livemint.com/rss/news",
    "https://economictimes.indiatimes.com/rssfeedsdefault.cms",
    "https://www.business-standard.com/rss/latest.rss",
    "https://feeds.feedburner.com/ndtvnews-top-stories",
    "https://timesofindia.indiatimes.com/rss.cms"
]

def as_ist(iso):
    try:
        return dtparse.parse(iso).astimezone(IST).strftime("%d %b, %H:%M IST")
    except:
        return ""

def domain_of(url: str) -> str:
    try: return url.split("/")[2].replace("www.","")
    except: return ""

def is_low_signal(a):
    src = (a.get("source") or {}).get("name") or ""
    if src in TABLOID: return True
    title = (a.get("title") or "").lower()
    return any(k in title for k in LOW_SIG)

def shape(arts):
    out, seen = [], set()
    for a in arts:
        title = (a.get("title") or "").strip()
        url = a.get("url")
        src = (a.get("source") or {}).get("name") or a.get("source") or "Source"
        if not title or not url: continue
        k = title + url
        if k in seen or is_low_signal(a): continue
        seen.add(k)
        out.append({
            "title": title,
            "url": url,
            "source": src,
            "published": a.get("publishedAt") or a.get("pubDate") or a.get("published") or "",
            "image": a.get("urlToImage"),
            "desc": (a.get("description") or a.get("summary") or a.get("content") or "")[:1000]
        })
    now_utc = datetime.now(timezone.utc)
    def score(item):
        s = 0
        if item["source"] in MAJOR: s += 1.0
        try:
            dt = dtparse.parse(item["published"]) or now_utc
            if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
            hrs = (now_utc - dt.astimezone(timezone.utc)).total_seconds()/3600
            if hrs <= 24: s += 1.2
            elif hrs <= 48: s += 0.4
        except: pass
        return s
    out.sort(key=score, reverse=True)
    return out

def apply_exclusions(articles, exclude_kws):
    if not exclude_kws: return articles
    out = []
    for a in articles:
        text = " ".join([a.get("title",""), a.get("desc",""), a.get("source","")]).lower()
        if any(kw in text for kw in exclude_kws): continue
        out.append(a)
    return out

def reorder_prioritize_local(items, country: str, n: int = 2):
    locals_set = set(LOCAL_DOMAINS.get(country, []))
    local, other = [], []
    for it in items:
        (local if domain_of(it["url"]) in locals_set else other).append(it)
    head = local[:n]
    tail = [x for x in items if x not in head]
    return head + tail

def clear_expanded_summaries():
    for k in list(st.session_state.keys()):
        if k.startswith("content_expand_"): del st.session_state[k]

# =========================
# Optional RSS import (fail-safe)
# =========================
try:
    import feedparser
    HAS_FEEDPARSER = True
except Exception:
    HAS_FEEDPARSER = False

def rss_pull(url, limit=25):
    if not HAS_FEEDPARSER:
        return []  # silently skip if not installed
    try:
        feed = feedparser.parse(url)
        items = []
        for e in feed.entries[:limit]:
            title = e.get("title","").strip()
            link  = e.get("link","")
            desc  = (e.get("summary") or e.get("description") or "")[:1000]
            pub   = e.get("published") or e.get("updated") or ""
            items.append({
                "title": title, "url": link, "source": (feed.feed.get("title") or "RSS"),
                "published": pub, "image": None, "desc": desc
            })
        return items
    except Exception:
        return []

# =========================
# Lightweight memory (session)
# =========================
def init_memory():
    st.session_state.setdefault("bookmarks", set())
    st.session_state.setdefault("feedback", [])  # {url, title, label:+1/-1, ts}
init_memory()

def remember_feedback(url, title, label):
    fb = st.session_state["feedback"]
    fb.append({"url": url, "title": title, "label": label, "ts": datetime.now(timezone.utc).isoformat()})
    st.session_state["feedback"] = fb

def toggle_bookmark(url):
    b = st.session_state["bookmarks"]
    if url in b: b.remove(url)
    else: b.add(url)
    st.session_state["bookmarks"] = b

# =========================
# Embeddings (semantic For You)
# =========================
@st.cache_resource(show_spinner=False)
def get_embedder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer("all-MiniLM-L6-v2")

def embed_texts(texts):
    if not texts: return []
    model = get_embedder()
    return model.encode(texts, normalize_embeddings=True).tolist()

def cosine_sim(a, b):
    return sum(x*y for x,y in zip(a,b))

def build_profile_vector(profile):
    likes = [f["title"] for f in st.session_state.get("feedback", []) if f["label"] == +1][-5:]
    parts = [
        f"role: {profile.get('role','')}",
        f"interests: {', '.join(profile.get('interests',[]))}",
        f"country: {profile.get('country','')}",
        f"liked: {', '.join(likes)}"
    ]
    text = " | ".join([p for p in parts if p.strip()])
    vecs = embed_texts([text]) or [[0]*384]
    return vecs[0]

# =========================
# NewsAPI calls
# =========================
@st.cache_data(ttl=180, show_spinner=False)
def news_top(params: dict):
    url = "https://newsapi.org/v2/top-headlines"
    p = {**params, "apiKey": NEWSAPI_KEY}
    p.setdefault("pageSize", 30)
    r = requests.get(url, params=p, timeout=20)
    r.raise_for_status()
    return r.json().get("articles", [])

@st.cache_data(ttl=180, show_spinner=False)
def news_everything(q: str, days: int = 2):
    url = "https://newsapi.org/v2/everything"
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    p = {"q": q, "from": since, "sortBy": "publishedAt", "language": "en", "pageSize": 50, "apiKey": NEWSAPI_KEY}
    r = requests.get(url, params=p, timeout=20)
    r.raise_for_status()
    return r.json().get("articles", [])

# =========================
# Fetchers (For You / Categories / Global / National via RSS blend)
# =========================
@st.cache_data(ttl=240, show_spinner=False)
def fetch_for_you(interests: list[str], country: str | None, profile_vec=None):
    terms = [t.strip() for t in (interests or []) if t.strip()]
    seen, cleaned = set(), []
    for t in terms:
        k = t.lower()
        if k not in seen:
            seen.add(k); cleaned.append(t)
        if len(cleaned) >= 12: break

    pool_raw = []
    if cleaned:
        pool_raw += news_everything(" OR ".join(cleaned), days=2)

    if len(pool_raw) < 12 and country:
        geo = COUNTRY_KEYWORDS.get(country, [])[:8]
        if geo:
            pool_raw += news_everything(" OR ".join((cleaned or []) + geo), days=3)

    if len(pool_raw) < 12:
        pool_raw += news_everything("technology OR business OR startups OR policy OR finance OR education", days=2)

    items = shape(pool_raw)
    if not items: return []

    if profile_vec is None:
        items = reorder_prioritize_local(items, country or "in", n=2)
        return items[:60]

    corpus = [(a["title"] + " " + (a.get("desc") or "")) for a in items]
    art_vecs = embed_texts(corpus)

    scored = []
    for a, v in zip(items, art_vecs):
        try: s = cosine_sim(profile_vec, v)
        except: s = 0.0
        boost = 0.0
        if a["source"] in MAJOR: boost += 0.05
        try:
            dt = dtparse.parse(a["published"])
            hrs = (datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds()/3600
            if hrs <= 24: boost += 0.08
        except: pass
        if any(f["url"] == a["url"] and f["label"] == +1 for f in st.session_state.get("feedback", [])):
            boost += 0.1
        scored.append((s+boost, a))

    scored.sort(key=lambda x: x[0], reverse=True)
    ranked = [a for _,a in scored]
    ranked = reorder_prioritize_local(ranked, country or "in", n=2)
    return ranked[:60]

@st.cache_data(ttl=240, show_spinner=False)
def fetch_category(category: str, country: str):
    pool = []
    if category == "tech":
        try: pool += news_top({"category":"technology", "country": country})
        except: pass
    if category == "health":
        try: pool += news_top({"category":"health", "country": country})
        except: pass
    if category == "finance":
        try: pool += news_top({"category":"business", "country": country})
        except: pass
    if category == "economy":
        try:
            pool += news_top({"category":"business", "country": country})
            pool += news_top({"category":"general",  "country": country})
        except: pass

    q = CATEGORY_QUERIES.get(category, "")
    if q:
        pool += news_everything(q, days=2)

    items = shape(pool)

    # Blend India RSS if needed
    if country == "in" and len(items) < 25 and category in ("tech","finance","economy","health"):
        rss_items = []
        for u in INDIA_RSS:
            rss_items.extend(rss_pull(u, limit=15))
        items = shape(items + rss_items)

    items = reorder_prioritize_local(items, country, n=2)
    return items[:60]

@st.cache_data(ttl=240, show_spinner=False)
def fetch_global(country: str):
    pool = []
    pool += news_everything(CATEGORY_QUERIES["global"], days=2)
    pool += news_everything("india OR europe OR china OR middle east OR us OR africa", days=2)
    items = shape(pool)
    items = reorder_prioritize_local(items, country, n=2)
    return items[:60]

# =========================
# OpenAI (teaser / expand / clarify)
# =========================
def openai_chat(messages, temperature=0.25, model="gpt-4o-mini"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {"model": model, "messages": messages, "temperature": temperature}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

@st.cache_data(ttl=3600, show_spinner=False)
def teaser_summary(title: str, snippet: str, source: str, level: str, time_str: str) -> str:
    try:
        if level == "basic":
            style = "Use very simple words and short sentences. Define any jargon briefly. 30–50 words."
        elif level == "high":
            style = "Be crisp and technical if needed; include one precise term. 30–50 words."
        else:
            style = "Be clear and neutral. 30–50 words."

        system = (
            "You write brief teasers for news cards.\n"
            "RULES:\n"
            "• 30–50 words total, 1–2 sentences.\n"
            "• Use ONLY the provided title and snippet; do not invent facts.\n"
            "• No bullet points. No fluff.\n"
        )
        user = {"title": title, "source": source, "time": time_str, "snippet": snippet, "style": style}
        msgs = [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(user)}]
        text = openai_chat(msgs, temperature=0.3, model="gpt-4o-mini")
        words = text.split()
        return (" ".join(words[:55]) + "…") if len(words) > 55 else text
    except Exception:
        base = snippet or title
        words = base.split()
        return " ".join(words[:45]) + ("…" if len(words) > 45 else "")

def derive_persona(profile: dict) -> str:
    role = (profile.get("role") or "").lower()
    interests = ", ".join(profile.get("interests", []))
    hints = []
    if any(k in role for k in ["student","mba","law","bba","llb"]):
        hints.append("wants case-study angles, regulation, compliance and career-relevant examples")
    if any(k in role for k in ["product","analyst","manager","strategy"]):
        hints.append("cares about user impact, unit economics, KPI movement, operational risk")
    if any(k in role for k in ["marketing","social","brand"]):
        hints.append("cares about brand-safety, platform policy, targeting constraints, creative angles")
    if any(k in role for k in ["finance","invest","bank","fintech"]):
        hints.append("cares about rates, liquidity, credit risk, regulatory changes")
    if any(k in role for k in ["founder","startup"]):
        hints.append("cares about GTM, TAM, regulatory barriers, hiring, runway")
    if not hints: hints.append("prefers actionable, concrete insights")
    return f"User role: {profile.get('role','')}. Interests: {interests}. This user {', and '.join(hints)}."

def compute_context_hints(profile: dict, article: dict) -> list[str]:
    role = (profile.get("role") or "").lower()
    interests = [i.lower() for i in profile.get("interests", [])]
    text = " ".join([article.get("title",""), article.get("desc",""), article.get("source","")]).lower()
    hints = []
    if any(k in text for k in ["employment","jobs","hiring","unemployment","payroll","labour","labor"]):
        if any(k in role+str(interests) for k in ["mobility","road","safety","automotive","helmet","abs"]):
            hints += [
                "Employment ↑ → daily commuting ↑ → two-wheeler & rideshare usage ↑ → road exposure ↑",
                "Road exposure ↑ → accident frequency/severity ↑ → demand for helmets/ABS/safety gear ↑",
            ]
        hints += [
            "Employment ↑ → disposable income ↑ → F&B / leisure / quick-commerce spend ↑",
            "Employment ↑ → hiring pressure ↑ → wages ↑ → margin pressure unless pricing/productivity adjust"
        ]
    if any(k in text for k in ["inflation","cpi","wpi","prices","rbi","repo","rate hike","policy rate"]):
        hints += [
            "Rates ↑ → EMI ↑ → discretionary demand ↓; working capital cost ↑",
            "Edible oil/sugar/grains ↑ → F&B margin squeeze unless pricing/pack-size changes"
        ]
    if any(k in text for k in ["election","regulation","regulatory","bill","parliament","supreme court"]):
        hints += ["Policy uncertainty ↑ → ad-spend mix shifts; compliance updates; state-wise enforcement variance"]
    if any(k in text for k in ["instagram","meta","youtube","tiktok","ads policy","brand safety","content moderation"]):
        hints += ["Platform policy change → creative/targeting constraints → campaign refresh & brand-safety checks"]
    if any(k in text for k in ["heatwave","flood","monsoon","climate","rainfall","el niño","la niña"]):
        hints += ["Weather anomaly → footfall/logistics disruption; cold-chain stress; agri output variance"]
    if any(k in text for k in ["fssai","food safety","hygiene","contamination","recall"]):
        hints += ["Tighter standards → SOP audits & staff training → vendor QA and labeling compliance"]
    uniq = []
    for h in hints:
        if h not in uniq: uniq.append(h)
    return uniq[:6]

def expand_summary(article, profile, level):
    bounds = {"basic": (170,240), "normal": (160,220), "high": (230,320)}
    lo, hi = bounds.get(level, (160,220))
    persona = derive_persona(profile)
    context_hints = compute_context_hints(profile, article)

    style_line = {
        "basic": "Use short sentences and simple words; define any jargon in parentheses.",
        "normal": "Be clear and concrete; avoid filler.",
        "high": "Be concise but analytical; use domain terms and simple micro-econ where relevant."
    }[level]

    system = (
        "You are an executive news analyst. Be specific and pragmatic.\n"
        "RULES:\n"
        "• Use ONLY the provided title/description/source/time. No invented numbers or quotes.\n"
        "• Prefer concrete verbs; avoid vague hedging unless you add the mechanism.\n"
        "• Include at least one causal chain using A → B → C.\n"
        "• Anchor analysis in the USER's role & interests. If the link is indirect, explain why it still matters (lesson/case/analogy).\n"
        f"• Keep total length between {lo}–{hi} words.\n"
        "• If the article lacks detail, say 'Detail not in source:' once and keep analysis proportional.\n"
    )

    liked = [f["title"] for f in st.session_state.get("feedback", []) if f["label"] == +1][-5:]
    disliked = [f["title"] for f in st.session_state.get("feedback", []) if f["label"] == -1][-5:]

    template = {
        "What happened": "Factual 1–2 lines based on title/description only.",
        "Why it matters to YOU": "Advisory tone. Include both Opportunity and Risk bullets tied to persona.",
        "Mechanism chain": "At least one 2–3 step cause→effect chain touching the user's world.",
        "What to watch next": "3 concrete leading indicators (data, events, prices, platform changes).",
        "Decision checklist": "2–3 specific actions for the user's role (who/what/when).",
        "Assumptions & unknowns": "1–2 assumptions; 1 unknown to verify.",
        "Confidence": "High/Medium/Low + one-line reason."
    }

    user = {
        "PERSONA_BRIEF": persona,
        "USER": {"name": profile.get("name"), "role": profile.get("role"), "interests": profile.get("interests", [])},
        "ARTICLE": {
            "title": article.get("title"), "source": article.get("source"),
            "time": as_ist(article.get("published")), "snippet": article.get("desc"), "url": article.get("url")
        },
        "DERIVED_CONTEXT_HINTS": context_hints,
        "PREFERENCES": {"recent_likes": liked, "recent_dislikes": disliked},
        "STYLE": style_line, "STRUCTURE": template
    }
    messages = [{"role":"system","content":system}, {"role":"user","content":json.dumps(user)}]
    return openai_chat(messages, temperature=0.23, model="gpt-4o-mini")

def clarify(article, profile, level, question=None):
    q = question or "Explain step-by-step HOW and WHY this news could affect me over the next 6–12 months."
    system = "Answer with a causal chain, tailored to the user's role/interests. Use ONLY provided article info."
    if level == "basic": system += " Use simple language; define jargon."
    if level == "high": system += " Include policy/market mechanisms if relevant."
    user = {"QUESTION": q, "USER": {"role": profile["role"], "interests": profile["interests"]},
            "ARTICLE": {"title": article["title"], "snippet": article["desc"], "source": article["source"]}}
    msgs = [{"role":"system","content":system},{"role":"user","content":json.dumps(user)}]
    return openai_chat(msgs, temperature=0.3)

# =========================
# Styles (UI polish)
# =========================
st.markdown("""
<style>
:root {
  --card-bg: #ffffff;
  --card-br: 12px;
  --muted: #6b7280;
  --accent: #2f6feb;
}
.block-container { padding-top: 1rem; }
.header-title { font-size: 1.6rem; font-weight: 800; margin-bottom: .2rem; }
.header-sub { color: var(--muted); margin-bottom: 1.0rem; }
.card { background: var(--card-bg); border: 1px solid #eee; border-radius: var(--card-br); 
        padding: 14px 16px; margin-bottom: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.03); }
.title { font-weight: 700; font-size: 1.05rem; }
.meta { color: #666; font-size: 0.9rem; margin-top: 2px; }
.chips { margin-top: 6px; }
.chip { display: inline-block; padding: 2px 8px; border: 1px solid #e5e7eb; border-radius: 999px; font-size: 0.75rem; color: #374151; margin-right: 6px; }
.teaser { color: #222; margin: 8px 0 10px 0; line-height: 1.5; }
.btnrow { margin-top: 6px; }
a.btnlink { text-decoration: none; border: 1px solid #e5e7eb; padding: 6px 10px; border-radius: 8px; font-size: 0.85rem; }
a.btnlink:hover { border-color: var(--accent); color: var(--accent); }
hr.sep { border: none; border-top: 1px dashed #eee; margin: 10px 0; }
</style>
""", unsafe_allow_html=True)

# =========================
# Session state & onboarding
# =========================
def init_state():
    st.session_state.setdefault("profile", {
        "name": "", "role": "", "interests": [], "reading_level": "normal", "country": "in",
    })
    st.session_state.setdefault("onboarded", False)
    st.session_state.setdefault("exclude_str", "celebrity,gossip,TMZ")
init_state()

def reading_preview(level: str):
    if level == "basic":
        return ("**Basic** — short sentences, everyday words.\n"
                "Example: *Prices went up slowly. This can change what people buy and what companies charge.*")
    if level == "high":
        return ("**High** — denser detail and terms.\n"
                "Example: *Core inflation plateaued; policy may stay restrictive; watch FMCG input pass-through.*")
    return ("**Normal** — balanced tone.\n"
            "Example: *Inflation held steady, which can influence interest rates and household spending.*")

def show_onboarding():
    st.markdown('<div class="header-title">Tell us about you</div>', unsafe_allow_html=True)
    st.markdown('<div class="header-sub">We personalize headlines, tone and actions to your world.</div>', unsafe_allow_html=True)
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Your name", placeholder="e.g., Ananya")
        role = st.text_input("Work/Study", placeholder="e.g., Social media manager in F&B; MBA student (business & law)")
        country = st.selectbox("Country for local prioritization", ["in","us","gb","sg","au","ca"], index=0)
    with col2:
        interests_str = st.text_area("Interests (comma separated)",
                                     placeholder="e.g., RBI policy, startups, food safety, climate, football")
        st.write("**Choose your reading level** (see previews):")
        lvl = st.radio("Reading level", ["basic","normal","high"], horizontal=True, label_visibility="collapsed")
        st.info(reading_preview(lvl))
    if st.button("Personalize my news →", type="primary"):
        st.session_state.profile.update({
            "name": (name or "").strip() or "Reader",
            "role": (role or "").strip() or "Professional/Student",
            "country": country,
            "interests": [i.strip() for i in (interests_str or "").split(",") if i.strip()],
            "reading_level": lvl
        })
        st.session_state.onboarded = True
        st.rerun()

def render_list(articles, profile, tab_name: str):
    if not articles:
        st.info("No articles available right now. Try refreshing in a minute (the free NewsAPI tier can rate-limit).")
        return
    for idx, a in enumerate(articles):
        base = f"{tab_name}_{idx}_{abs(hash(a['url']))}"
        btn_key      = f"btn_expand_{base}"
        content_key  = f"content_expand_{base}_{profile['reading_level']}"
        clarify_qkey = f"clar_q_{base}"
        clarify_btn  = f"clar_btn_{base}"

        if content_key not in st.session_state: st.session_state[content_key] = None

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f'<div class="title">{a["title"]}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="meta">{a["source"]} • {as_ist(a["published"])}</div>', unsafe_allow_html=True)
            st.markdown('<div class="chips"><span class="chip">Readable</span><span class="chip">Actionable</span></div>', unsafe_allow_html=True)

            teaser = teaser_summary(a["title"], a.get("desc") or "", a["source"], profile["reading_level"], as_ist(a["published"]))
            st.markdown(f'<div class="teaser">{teaser}</div>', unsafe_allow_html=True)

            c1, c2, _ = st.columns([1.2,1.2,2])
            with c1:
                if st.button("🔍 Expand analysis", key=btn_key):
                    with st.spinner("Personalizing…"):
                        st.session_state[content_key] = expand_summary(a, profile, profile["reading_level"])
            with c2:
                st.markdown(f'<a class="btnlink" href="{a["url"]}" target="_blank">↗ Read original</a>', unsafe_allow_html=True)

            if st.session_state[content_key]:
                st.markdown("<hr class='sep'/>", unsafe_allow_html=True)
                st.markdown(st.session_state[content_key])
                with st.expander("How? Why? Ask for a causal explanation", expanded=False):
                    q = st.text_input("Ask a question (optional):", key=clarify_qkey, value="")
                    if st.button("Answer", key=clarify_btn):
                        with st.spinner("Thinking…"):
                            ans = clarify(a, profile, profile["reading_level"], question=q or None)
                            st.write(ans)

                c_like, c_dislike, c_save = st.columns([1,1,1])
                with c_like:
                    if st.button("👍 Useful", key=f"like_{base}"):
                        remember_feedback(a["url"], a["title"], +1); st.success("Noted")
                with c_dislike:
                    if st.button("👎 Not for me", key=f"dislike_{base}"):
                        remember_feedback(a["url"], a["title"], -1); st.info("We’ll show fewer like this")
                with c_save:
                    if st.button(("🔖 Saved" if a["url"] in st.session_state["bookmarks"] else "🔖 Save"), key=f"save_{base}"):
                        toggle_bookmark(a["url"])

            st.markdown("</div>", unsafe_allow_html=True)

# =========================
# MAIN
# =========================
if not st.session_state.onboarded:
    show_onboarding()
    st.stop()

with st.sidebar:
    st.header("Your profile")
    p = st.session_state.profile
    p["name"] = st.text_input("Name", value=p["name"])
    p["role"] = st.text_input("Work/Study", value=p["role"])
    p["country"] = st.selectbox("Local preference (country)", ["in","us","gb","sg","au","ca"],
                                index=["in","us","gb","sg","au","ca"].index(p["country"]))
    interests_str = st.text_area("Interests (comma separated)", value=", ".join(p["interests"]), height=90)
    p["interests"] = [i.strip() for i in interests_str.split(",") if i.strip()]

    old_level = p["reading_level"]
    p["reading_level"] = st.radio("Reading level", ["basic","normal","high"],
                                  index=["basic","normal","high"].index(p["reading_level"]), horizontal=True)
    if p["reading_level"] != old_level: clear_expanded_summaries()
    st.caption("Change level → teasers + expansions adapt to the new level.")

    exclude_str = st.text_input("Exclude topics (comma separated)",
                                value=st.session_state.get("exclude_str", "celebrity,gossip,TMZ"))
    st.session_state["exclude_str"] = exclude_str
    EXCLUDE_KWS = [w.strip().lower() for w in exclude_str.split(",") if w.strip()]

    st.session_state.profile = p

st.markdown('<div class="header-title">🗞️ Your personalized briefing</div>', unsafe_allow_html=True)
st.markdown('<div class="header-sub">Depth on demand • Local-first • Actionable next steps</div>', unsafe_allow_html=True)

tabs = st.tabs(["✨ For You", "💻 Tech", "💸 Finance", "📈 Economy", "🩺 Health & Wellness", "🌍 Global"])

with tabs[0]:
    try:
        prof_vec = build_profile_vector(st.session_state.profile)
        data = fetch_for_you(st.session_state.profile["interests"], st.session_state.profile["country"], profile_vec=prof_vec)
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="foryou")
    except Exception as e:
        st.error(f"Failed to load For You: {e}")

with tabs[1]:
    try:
        data = fetch_category("tech", st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="tech")
    except Exception as e:
        st.error(f"Failed to load Tech: {e}")

with tabs[2]:
    try:
        data = fetch_category("finance", st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="finance")
    except Exception as e:
        st.error(f"Failed to load Finance: {e}")

with tabs[3]:
    try:
        data = fetch_category("economy", st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="economy")
    except Exception as e:
        st.error(f"Failed to load Economy: {e}")

with tabs[4]:
    try:
        data = fetch_category("health", st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="health")
    except Exception as e:
        st.error(f"Failed to load Health & Wellness: {e}")

with tabs[5]:
    try:
        data = fetch_global(st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="global")
    except Exception as e:
        st.error(f"Failed to load Global: {e}")

st.caption(f"Generated at {datetime.now(IST).strftime('%d %b %Y, %H:%M IST')} • MVP demo")
