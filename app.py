import streamlit as st
import requests, json
from datetime import datetime, timedelta, timezone
from dateutil import parser as dtparse

# -----------------------------
# App config
# -----------------------------
st.set_page_config(page_title="News Agent MVP", page_icon="📰", layout="wide")
IST = timezone(timedelta(hours=5, minutes=30))

# Secrets (set these in Streamlit Cloud → Advanced settings → Secrets)
NEWSAPI_KEY = st.secrets["NEWSAPI_KEY"]
OPENAI_API_KEY = st.secrets["OPENAI_API_KEY"]

# -----------------------------
# Helpers
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
        pool += news_top({"category":"general", "country": country})
        pool += news_top({"category":"business","country": country})
        pool += news_top({"category":"technology","country": country})
    except Exception:
        pass

    items = shape(pool) if pool else []

    # Fallback if the country feed is empty or too small (rate-limit/sparse)
    if len(items) < 5:
        try:
            backup = news_everything("India OR New Delhi OR RBI OR Parliament OR Supreme Court", days=2)
            items = shape(backup)
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
# LLM (OpenAI) for expansion / clarify
# -----------------------------
def openai_chat(messages, temperature=0.25, model="gpt-4o-mini"):
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}"}
    payload = {"model": model, "messages": messages, "temperature": temperature}
    r = requests.post(url, headers=headers, json=payload, timeout=60)
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()

def expand_summary(article, profile, level):
    bounds = {"basic": (130,200), "normal": (110,150), "high": (180,260)}
    lo, hi = bounds.get(level, (110,150))
    system = (
        "You are a concise, accurate news aide.\n"
        f"Write between {lo}–{hi} words. Use ONLY the provided title/description/source/time—no fabrication.\n"
        "Structure:\n"
        "What happened — crisp factual recap.\n"
        "Why it matters to YOU — tailor to the user's work/study & interests.\n"
        "Expected impact — • Work/Study • Social\n"
        "Decision checklist — 2–3 concrete follow-ups.\n"
        "Confidence — High/Med/Low.\n"
    )
    if level == "basic":
        system += " Keep language simple and define terms briefly."
    elif level == "high":
        system += " Add context, frameworks, and regulatory/market nuance where relevant."
    user = {
        "USER": {"name": profile["name"], "role": profile["role"], "interests": profile["interests"]},
        "ARTICLE": {"title": article["title"], "source": article["source"], "time": as_ist(article["published"]),
                    "snippet": article["desc"], "url": article["url"]},
        "READING_LEVEL": level
    }
    msgs = [{"role":"system","content":system}, {"role":"user","content":json.dumps(user)}]
    return openai_chat(msgs, temperature=0.25)

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
.one-liner { color: #333; margin-top: 4px; }
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
# Article list renderer (1-line + Expand + Clarify)
# -----------------------------
def render_list(articles, profile, tab_name: str):
    """
    Renders a list of articles with: 1-line snippet, Expand (profile summary), Clarify.
    Keys are made unique per TAB + INDEX + URL + reading level.
    We also keep widget keys and stored-content keys different to avoid collisions.
    """
    if not articles:
        st.info("No articles available right now. Try switching tabs or refreshing in a minute (NewsAPI can rate-limit free keys).")
        return

    for idx, a in enumerate(articles):
        one = (a["desc"] or a["title"]).split(".")[0]

        # Unique keys per row & level
        base = f"{tab_name}_{idx}_{abs(hash(a['url']))}"
        btn_key      = f"btn_expand_{base}"                           # button widget key
        content_key  = f"content_expand_{base}_{profile['reading_level']}"  # where we store expanded text
        clarify_exp  = f"exp_clar_{base}"
        clarify_qkey = f"clar_q_{base}"
        clarify_btn  = f"clar_btn_{base}"

        # init content slot if needed
        if content_key not in st.session_state:
            st.session_state[content_key] = None

        with st.container():
            st.markdown('<div class="card">', unsafe_allow_html=True)
            st.markdown(f'<div class="title">{a["title"]}</div>', unsafe_allow_html=True)
            st.markdown(f'<div class="meta">{a["source"]} • {as_ist(a["published"])}</div>', unsafe_allow_html=True)
            st.markdown(
                f'<div class="one-liner">{one[:160]}{"…" if len(one)>160 else ""}</div>',
                unsafe_allow_html=True
            )

            c1, c2 = st.columns([1,3])
            with c1:
                if st.button("Expand", key=btn_key):
                    with st.spinner("Personalizing…"):
                        st.session_state[content_key] = expand_summary(a, profile, profile["reading_level"])
            with c2:
                st.markdown(f"[Read original]({a['url']})")

            # Show expanded content
            if st.session_state[content_key]:
                st.markdown(st.session_state[content_key])

                # Clarify block (unique keys)
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
    p["reading_level"] = st.radio("Reading level", ["basic","normal","high"],
                                  index=["basic","normal","high"].index(p["reading_level"]), horizontal=True)
    st.session_state.profile = p

st.title("📰 News Agent — personalized & depth-on-demand")

tabs = st.tabs(["🇮🇳 National", "🌍 Global", "✨ For You"])

with tabs[0]:
    try:
        data = fetch_national(st.session_state.profile["country"])
        render_list(data, st.session_state.profile, tab_name="national")
    except Exception as e:
        st.error(f"Failed to load National feed: {e}")

with tabs[1]:
    try:
        data = fetch_global()
        render_list(data, st.session_state.profile, tab_name="global")
    except Exception as e:
        st.error(f"Failed to load Global feed: {e}")

with tabs[2]:
    try:
        data = fetch_for_you(st.session_state.profile["interests"])
        render_list(data, st.session_state.profile, tab_name="foryou")
    except Exception as e:
        st.error(f"Failed to load For You feed: {e}")

st.caption(f"Generated at {datetime.now(IST).strftime('%d %b %Y, %H:%M IST')} • MVP demo")
