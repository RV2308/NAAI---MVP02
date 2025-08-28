import streamlit as st
import requests, json
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparse

# -----------------------------
# App config
# -----------------------------
st.set_page_config(page_title="News Agent MVP", page_icon="📰", layout="wide")
IST = timezone(timedelta(hours=5, minutes=30))

# Secrets (set these in Streamlit Cloud → App ▸ Settings ▸ Secrets)
NEWSAPI_KEY = st.secrets["NEWSAPI_KEY"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# -----------------------------
# Helpers (utilities)
# -----------------------------
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

def as_ist(iso):
    try:
        return dtparse.parse(iso).astimezone(IST).strftime("%d %b, %H:%M IST")
    except:
        return ""

def is_low_signal(a):
    src = (a.get("source") or {}).get("name") or ""
    if src in TABLOID:
        return True
    title = (a.get("title") or "").lower()
    return any(k in title for k in LOW_SIG)

def shape(arts):
    out, seen = [], set()
    for a in arts:
        title = (a.get("title") or "").strip()
        url = a.get("url")
        src = (a.get("source") or {}).get("name") or "Source"
        if not title or not url:
            continue
        k = title + url
        if k in seen or is_low_signal(a):
            continue
        seen.add(k)
        out.append({
            "title": title,
            "url": url,
            "source": src,
            "published": a.get("publishedAt") or "",
            "image": a.get("urlToImage"),
            "desc": (a.get("description") or a.get("content") or "")[:900]
        })
    # sort by outlet quality + recency
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
    if not exclude_kws:
        return articles
    out = []
    for a in articles:
        text = " ".join([a.get("title",""), a.get("desc",""), a.get("source","")]).lower()
        if any(kw in text for kw in exclude_kws):
            continue
        out.append(a)
    return out

def clear_expanded_summaries():
    """Wipe cached expanded text so reading-level changes take effect."""
    for k in list(st.session_state.keys()):
        if k.startswith("content_expand_"):
            del st.session_state[k]

# -----------------------------
# NewsAPI calls (Top & Everything)
# -----------------------------
@st.cache_data(ttl=180, show_spinner=False)
def news_top(params: dict):
    """Top-headlines: supports country/category/sources/q. No 'language' here."""
    url = "https://newsapi.org/v2/top-headlines"
    p = {**params, "apiKey": NEWSAPI_KEY}
    p.setdefault("pageSize", 30)
    r = requests.get(url, params=p, timeout=20)
    r.raise_for_status()
    return r.json().get("articles", [])

@st.cache_data(ttl=180, show_spinner=False)
def news_everything(q: str, days: int = 2):
    """Everything: supports language and date."""
    url = "https://newsapi.org/v2/everything"
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    p = {"q": q, "from": since, "sortBy": "publishedAt", "language": "en", "pageSize": 50, "apiKey": NEWSAPI_KEY}
    r = requests.get(url, params=p, timeout=20)
    r.raise_for_status()
    return r.json().get("articles", [])

@st.cache_data(ttl=180, show_spinner=False)
def fetch_national(country: str):
    # Primary: country top-headlines
    pool = []
    try:
        pool += news_top({"category":"general",    "country": country})
        pool += news_top({"category":"business",   "country": country})
        pool += news_top({"category":"technology", "country": country})
    except Exception:
        pass

    items = shape(pool) if pool else []

    # Fallback: restrict by local domains so it stays national
    if len(items) < 6:
        local_domains_map = {
            "in": [
                "thehindu.com","indianexpress.com","hindustantimes.com","livemint.com",
                "economictimes.indiatimes.com","business-standard.com","ndtv.com",
                "timesofindia.indiatimes.com","moneycontrol.com","thewire.in","scroll.in"
            ],
            "us": ["nytimes.com","wsj.com","washingtonpost.com","apnews.com","reuters.com","cnn.com","npr.org"],
            "gb": ["bbc.co.uk","theguardian.com","ft.com","telegraph.co.uk","independent.co.uk","sky.com"],
            "au": ["abc.net.au","smh.com.au","theaustralian.com.au","theage.com.au","news.com.au"],
            "sg": ["straitstimes.com","channelnewsasia.com","todayonline.com","businesstimes.com.sg"],
            "ca": ["cbc.ca","ctvnews.ca","theglobeandmail.com","nationalpost.com","financialpost.com"],
        }
        domains = local_domains_map.get(country, [])
        try:
            backup = news_everything("economy OR policy OR parliament OR election OR business OR technology", days=2)
            shaped = shape(backup)
            if domains:
                def domain_of(url: str):
                    try:
                        return url.split("/")[2].replace("www.","")
                    except:
                        return ""
                shaped = [a for a in shaped if domain_of(a["url"]) in domains]
            items = shaped or items
        except Exception:
            pass

    return items[:60]

@st.cache_data(ttl=180, show_spinner=False)
def fetch_global():
    pool = []
    pool += news_everything("world OR economy OR inflation OR election OR ceasefire OR climate")
    pool += news_everything("india OR europe OR china OR middle east OR us OR africa")
    return shape(pool)[:60]

@st.cache_data(ttl=180, show_spinner=False)
def fetch_for_you(interests: list[str]):
    q = " OR ".join(interests[:12]) if interests else "technology OR business OR education OR finance"
    return shape(news_everything(q))[:40]

# -----------------------------
# Teaser (30–50 words) per reading level
# -----------------------------
@st.cache_data(ttl=3600, show_spinner=False)
def teaser_summary(title: str, snippet: str, source: str, level: str, time_str: str) -> str:
    """
    Returns a 30–50 word teaser that varies by reading level.
    Cached per (title, snippet, source, level, time_str) to keep cost low.
    Falls back to a trimmed snippet if LLM call fails.
    """
    try:
        if level == "basic":
            style = "Use very simple words and short sentences. Define any jargon briefly. 30–50 words."
        elif level == "high":
            style = "Be crisp and technical if needed; you can include a key term. 30–50 words."
        else:
            style = "Be clear and neutral. 30–50 words."

        system = (
            "You write brief teasers for news cards.\n"
            "RULES:\n"
            "• 30–50 words total, 1–2 sentences.\n"
            "• Use ONLY the provided title and snippet; do not invent facts.\n"
            "• No bullet points. No fluff.\n"
        )
        user = {
            "title": title,
            "source": source,
            "time": time_str,
            "snippet": snippet,
            "style": style
        }
        msgs = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(user)}
        ]
        text = openai_chat(msgs, temperature=0.3, model="gpt-4o-mini")
        words = text.split()
        if len(words) > 55:
            text = " ".join(words[:55]) + "…"
        return text
    except Exception:
        base = snippet or title
        words = base.split()
        return " ".join(words[:45]) + ("…" if len(words) > 45 else "")

# -----------------------------
# LLM (OpenAI) for expansion / clarify
# -----------------------------
def openai_chat(messages, temperature=0.25, model="gpt-4o-mini"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {"model": model, "messages": messages, "temperature": temperature}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def compute_context_hints(profile: dict, article: dict) -> list[str]:
    """
    Derive pragmatic, domain-specific angles based on user role/interests + article text.
    Keep it tiny & fast: just string matching → curated hints for the prompt.
    """
    role = (profile.get("role") or "").lower()
    interests = [i.lower() for i in profile.get("interests", [])]
    text = " ".join([
        article.get("title",""), article.get("desc",""), article.get("source","")
    ]).lower()

    hints = []

    # Employment / macro → mobility/consumption/supply chain
    if any(k in text for k in ["employment", "jobs", "hiring", "unemployment", "payroll"]) \
       or "labour" in text or "labor" in text:
        if any(k in role+str(interests) for k in ["mobility","road","safety","automotive","helmet","abs"]):
            hints += [
                "Employment ↑ → daily commuting ↑ → two-wheeler & rideshare usage ↑ → road exposure ↑",
                "Road exposure ↑ → accident frequency/severity ↑ → demand for helmets/ABS/road-safety gear ↑",
            ]
        hints += [
            "Employment ↑ → disposable income ↑ → discretionary consumption ↑ (F&B, quick-commerce, dining out)",
            "Employment ↑ → hiring/retention pressure ↑ → wages ↑ → margin pressure unless prices/productivity adjust"
        ]

    # Inflation / RBI / policy
    if any(k in text for k in ["inflation","cpi","wpi","prices","rbi","repo","rate hike","policy rate"]):
        hints += [
            "Rates ↑ → EMI ↑ → discretionary demand ↓; working capital cost ↑",
            "Commodity/input prices ↑ (edible oil, sugar, grains) → F&B margin squeeze unless pricing/pack-size changes"
        ]

    # Elections / regulation
    if any(k in text for k in ["election","model code","regulation","regulatory","bill","parliament","supreme court"]):
        hints += [
            "Policy uncertainty ↑ → ad-spend mix shifts; compliance updates; state-wise enforcement variance"
        ]

    # Platforms / ads / social
    if any(k in text for k in ["instagram","meta","youtube","tiktok","ads policy","brand safety","content moderation"]):
        hints += [
            "Platform policy change → creative/targeting constraints → campaign refresh & brand-safety checks"
        ]

    # Climate / weather
    if any(k in text for k in ["heatwave","flood","monsoon","climate","rainfall","el niño","la niña"]):
        hints += [
            "Weather anomaly → footfall/supply disruption risk; cold-chain/logistics stress; agri output variance"
        ]

    # Food / FSSAI
    if any(k in text for k in ["fssai","food safety","hygiene","contamination","recall"]):
        hints += [
            "Tightening standards → SOP audit & staff training → vendor QA and labeling compliance"
        ]

    uniq = []
    for h in hints:
        if h not in uniq: uniq.append(h)
    return uniq[:6]

def expand_summary(article, profile, level):
    """
    Pro-style prompt: forces mechanism chains, opportunities/risks, watchlist, and role-specific actions.
    Uses only provided info + derived context hints; no outside facts.
    """
    bounds = {"basic": (160,230), "normal": (150,210), "high": (220,320)}
    lo, hi = bounds.get(level, (150,210))

    context_hints = compute_context_hints(profile, article)

    if level == "basic":
        style_line = "Use short sentences and simple words; define any jargon in parentheses."
    elif level == "high":
        style_line = "Be concise but analytical; use domain terms and simple micro-econ where relevant."
    else:
        style_line = "Be clear and concrete; avoid filler."

    system = (
        "You are an executive news analyst. You MUST be specific and pragmatic.\n"
        "CRITICAL RULES:\n"
        "• Use ONLY the provided title/description/source/time. Do NOT invent numbers or quotes.\n"
        "• Prefer concrete verbs over vague hedging (avoid 'might/could' unless you add the mechanism).\n"
        "• Always include a causal chain with arrows like A → B → C.\n"
        "• Tie analysis to the user's role and interests.\n"
        f"• Keep total length between {lo}–{hi} words.\n"
        "• If the article lacks detail, say 'Detail not in source:' once and keep analysis proportional.\n"
    )

    template = {
        "What happened": "Factual 1–2 lines based on title/description only.",
        "Why it matters to YOU": "Write as if advising the user. Include both Opportunity and Risk bullets.",
        "Mechanism chain": "At least one 2–3 step cause→effect chain touching the user's world.",
        "What to watch next": "3 concrete leading indicators (data points, events, prices, platform changes).",
        "Decision checklist": "2–3 specific actions for the user's role (who/what/when).",
        "Assumptions & unknowns": "1–2 assumptions; 1 unknown to verify.",
        "Confidence": "High/Medium/Low + one-line reason."
    }

    user = {
        "USER": {
            "name": profile.get("name"),
            "role": profile.get("role"),
            "interests": profile.get("interests", [])
        },
        "ARTICLE": {
            "title": article.get("title"),
            "source": article.get("source"),
            "time": as_ist(article.get("published")),
            "snippet": article.get("desc"),
            "url": article.get("url")
        },
        "DERIVED_CONTEXT_HINTS": context_hints,
        "STYLE": style_line,
        "STRUCTURE": template
    }

    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps(user)}
    ]
    return openai_chat(messages, temperature=0.25, model="gpt-4o-mini")

def clarify(article, profile, level, question=None):
    q = question or "Explain step-by-step HOW and WHY this news could affect me over the next 6–12 months."
    system = "Answer clearly with a causal chain, tailored to user role/interests. Use ONLY provided article info."
    if level == "basic": system += " Use simple language; define jargon."
    if level == "high": system += " Include policy/market mechanisms if relevant."
    user = {
        "QUESTION": q,
        "USER": {"role": profile["role"], "interests": profile["interests"]},
        "ARTICLE": {"title": article["title"], "snippet": article["desc"], "source": article["source"]}
    }
    msgs = [{"role":"system","content":system},{"role":"user","content":json.dumps(user)}]
    return openai_chat(msgs, temperature=0.3)

# -----------------------------
# Styles
# -----------------------------
st.markdown("""
<style>
.block-container { padding-top: 1rem; }
.card { border: 1px solid #eee; border-radius: 14px; padding: 12px 14px; margin-bottom: 10px; }
.title { font-weight: 700; }
.meta { color: #666; font-size: 0.9rem; }
.teaser { color: #2b2b2b; margin: 6px 0 10px 0; line-height: 1.35; }
</style>
""", unsafe_allow_html=True)

# -----------------------------
# Session state (profile + onboarding flag)
# -----------------------------
def init_state():
    st.session_state.setdefault("profile", {
        "name": "",
        "role": "",
        "interests": [],
        "reading_level": "normal",
        "country": "in",
    })
    st.session_state.setdefault("onboarded", False)
    st.session_state.setdefault("exclude_str", "celebrity,gossip,TMZ")
init_state()

# -----------------------------
# Reading level previews (like font-size demo)
# -----------------------------
def reading_preview(level: str):
    if level == "basic":
        return ("**Basic** — short sentences, everyday words.\n"
                "Example: *Prices went up slowly last month. This can change what people buy and what companies charge.*")
    if level == "high":
        return ("**High** — denser detail and terms.\n"
                "Example: *Core inflation plateaued, implying policy rates may stay restrictive; watch pass-through into FMCG input costs.*")
    return ("**Normal** — balanced tone.\n"
            "Example: *Inflation held steady, which can influence interest rates and household spending in the near term.*")

# -----------------------------
# Onboarding form (Work/Study, Interests, Reading level, Country)
# -----------------------------
def show_onboarding():
    st.header("Tell us about you")
    col1, col2 = st.columns(2)
    with col1:
        name = st.text_input("Your name", placeholder="e.g., Ananya")
        role = st.text_input("Work/Study", placeholder="e.g., MBA student (business & law)")
        country = st.selectbox("Country for National news", ["in","us","gb","sg","au","ca"], index=0)
    with col2:
        interests_str = st.text_area("Interests (comma separated)",
                                     placeholder="e.g., RBI policy, startups, food safety, climate, football")
        st.write("**Choose your reading level** (see previews):")
        lvl = st.radio("Reading level", ["basic","normal","high"], horizontal=True, label_visibility="collapsed")
        st.info(reading_preview(lvl))

    if st.button("Get my personalized news ➜", type="primary"):
        st.session_state.profile.update({
            "name": (name or "").strip() or "Reader",
            "role": (role or "").strip() or "Professional/Student",
            "country": country,
            "interests": [i.strip() for i in (interests_str or "").split(",") if i.strip()],
            "reading_level": lvl
        })
        st.session_state.onboarded = True
        st.rerun()

# -----------------------------
# Article list renderer (30–50 word teaser + Expand + Clarify) — collision-proof keys
# -----------------------------
def render_list(articles, profile, tab_name: str):
    """
    Renders a list with teaser, Expand (profile summary), Clarify.
    Keys are unique per TAB + INDEX + URL + reading level.
    Widget keys and stored-content keys are different to avoid collisions.
    """
    if not articles:
        st.info("No articles available right now. Try switching tabs or refreshing in a minute (the free NewsAPI tier can rate-limit).")
        return

    for idx, a in enumerate(articles):
        base = f"{tab_name}_{idx}_{abs(hash(a['url']))}"
        btn_key      = f"btn_expand_{base}"                                # button widget key
        content_key  = f"content_expand_{base}_{profile['reading_level']}" # store expanded text
        clarify_qkey = f"clar_q_{base}"
        clarify_btn  = f"clar_btn_{base}"

        if content_key not in st.session_state:
            st.session_state[content_key] = None

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f'<div class="title">{a["title"]}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="meta">{a["source"]} • {as_ist(a["published"])}</div>', unsafe_allow_html=True)

            # 30–50 word teaser that respects reading level
            teaser = teaser_summary(
                title=a["title"],
                snippet=a.get("desc") or "",
                source=a["source"],
                level=profile["reading_level"],
                time_str=as_ist(a["published"])
            )
            st.markdown(f'<div class="teaser">{teaser}</div>', unsafe_allow_html=True)

            c1, c2 = st.columns([1,3])
            with c1:
                if st.button("Expand", key=btn_key):
                    with st.spinner("Personalizing…"):
                        st.session_state[content_key] = expand_summary(a, profile, profile["reading_level"])
            with c2:
                st.markdown(f"[Read original]({a['url']})")

            if st.session_state[content_key]:
                st.markdown(st.session_state[content_key])
                with st.expander("How? Why? (ask for causal explanation)", expanded=False):
                    q = st.text_input("Ask a question (optional):", key=clarify_qkey, value="")
                    if st.button("Clarify", key=clarify_btn):
                        with st.spinner("Thinking…"):
                            ans = clarify(a, profile, profile["reading_level"], question=q or None)
                            st.write(ans)

            st.markdown("</div>", unsafe_allow_html=True)

# -----------------------------
# MAIN UI
# -----------------------------
if not st.session_state.onboarded:
    show_onboarding()
    st.stop()

# Sidebar lets users tweak profile anytime
with st.sidebar:
    st.header("Your profile")
    p = st.session_state.profile
    p["name"] = st.text_input("Name", value=p["name"])
    p["role"] = st.text_input("Work/Study", value=p["role"])
    p["country"] = st.selectbox("Country (National tab)", ["in","us","gb","sg","au","ca"],
                                index=["in","us","gb","sg","au","ca"].index(p["country"]))
    interests_str = st.text_area("Interests (comma separated)", value=", ".join(p["interests"]), height=90)
    p["interests"] = [i.strip() for i in interests_str.split(",") if i.strip()]

    # Reading level with auto-invalidate of expanded summaries
    old_level = p["reading_level"]
    p["reading_level"] = st.radio(
        "Reading level",
        ["basic","normal","high"],
        index=["basic","normal","high"].index(p["reading_level"]),
        horizontal=True
    )
    if p["reading_level"] != old_level:
        clear_expanded_summaries()
    st.caption("Change level → teasers + expansions adapt to the new level.")

    # Exclude topics
    exclude_str = st.text_input("Exclude topics (comma separated)",
                                value=st.session_state.get("exclude_str", "celebrity,gossip,TMZ"))
    st.session_state["exclude_str"] = exclude_str
    EXCLUDE_KWS = [w.strip().lower() for w in exclude_str.split(",") if w.strip()]

    st.session_state.profile = p

st.title("📰 News Agent — personalized & depth-on-demand")

tabs = st.tabs(["🇮🇳 National", "🌍 Global", "✨ For You"])

with tabs[0]:
    try:
        data = fetch_national(st.session_state.profile["country"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="national")
    except Exception as e:
        st.error(f"Failed to load National feed: {e}")

with tabs[1]:
    try:
        data = fetch_global()
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="global")
    except Exception as e:
        st.error(f"Failed to load Global feed: {e}")

with tabs[2]:
    try:
        data = fetch_for_you(st.session_state.profile["interests"])
        data = apply_exclusions(data, EXCLUDE_KWS)
        render_list(data, st.session_state.profile, tab_name="foryou")
    except Exception as e:
        st.error(f"Failed to load For You feed: {e}")

st.caption(f"Generated at {datetime.now(IST).strftime('%d %b %Y, %H:%M IST')} • MVP demo")
