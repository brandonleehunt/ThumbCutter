"""
Optional Claude-powered headline / description / tag suggestions.

Uses the user's own Anthropic API key (stored locally, never bundled or
hardcoded). Plain HTTPS call using only the standard library.
"""
import json
import re
import urllib.request
import urllib.error

from . import settings

CONFIG_PATH = settings.CONFIG_PATH
API_URL = "https://api.anthropic.com/v1/messages"
DEFAULT_MODEL = "claude-sonnet-5-5"

# Anthropic updates model names over time; if suggestions start failing with a
# "not found" style error, check docs.claude.com for the current model string
# and update it in Settings — no code changes needed.

TAGS_CHAR_LIMIT = 500      # YouTube's total limit for a video's tags
DESCRIPTION_MAX_SENTENCES = 4

HEADLINE_PROMPT = """You are a YouTube packaging strategist. The headlines you write are used as \
big thumbnail text AND as hook titles for short-form cutdowns of this video.

Read the transcript below and write exactly {n} distinct headline ideas, following YouTube best practices:
- Keep each under 60 characters (about 4-8 words) so it never truncates in search or on mobile, \
and stays readable at thumbnail size.
- Front-load the most important keyword or name (the book, person, topic) in the first three words.
- Create a curiosity gap or clear stakes/promise, but only one the video actually delivers on. \
No clickbait and nothing the transcript doesn't support.
- Be specific (names, numbers, outcomes) rather than vague.
- Use words and phrases the speaker actually says in the transcript: the app matches headline \
keywords against transcript lines to find the right clip for each headline.
- Write in normal title/sentence case, not ALL CAPS (the app capitalizes for the thumbnail). \
At most one "?" or "!", no emoji, no hashtags.
- Vary the angle across the {n} ideas (for example: a question, a warning or urgency, a personal \
journey, an honest verdict, a surprising detail). Don't rephrase one hook {n} times.
- Match the creator's own voice.
{avoid_clause}
For each headline also write a short "tag" (2-4 words, may be an empty string): a small badge on \
the thumbnail that adds context without repeating the headline, e.g. "PART 1 OF 3".

Respond with ONLY a JSON array, no markdown fences, no commentary, in this exact shape:
[{{"headline": "...", "tag": "..."}}, ...]

TRANSCRIPT:
{transcript}
"""

DESCRIPTION_PROMPT = """You are a YouTube SEO and packaging expert. Write the description teaser, \
search tags and hashtags for the video in this transcript, following YouTube best practices.

DESCRIPTION
- 3 to 4 sentences TOTAL. Concise and teaser-style: make people want to watch, don't summarize \
everything and don't give away the payoff.
- Sentence 1 is a hook that works the video's main topic keyword into its first ~100 characters \
(only roughly the first 125-150 characters show before "Show more").
- Use keywords naturally. No keyword stuffing, no ALL CAPS, no emoji spam.
- The last sentence is a light call to action, such as a question that invites comments.
- Use only details that are actually in the transcript. Do NOT include links, timestamps, \
sponsors or social handles. The app adds links itself.
- Write in the creator's voice, first person where natural.

TAGS
- 8 to 15 tags, under 450 characters combined.
- The first tag is the exact main topic phrase of the video.
- Mix broad topic tags, specific names/titles, and long-tail phrases people really search \
(for example "<book> review"), plus common misspellings or variants of key names if relevant.
- Short, lowercase, no hashtags, no duplicates, and nothing unrelated to the video.

HASHTAGS
- Exactly 3 relevant hashtags in CamelCase with no spaces, like #BookReview.

Respond with ONLY a JSON object, no markdown fences, no commentary, in this exact shape:
{{"description": "...", "tags": ["...", "..."], "hashtags": ["#...", "#...", "#..."]}}

TRANSCRIPT:
{transcript}
"""


# ---------- settings passthroughs (kept so callers don't change) ----------
def get_api_key():
    return settings.get("api_key", "")


def set_api_key(key):
    settings.set_value("api_key", key.strip())


def get_model():
    return settings.get("model") or DEFAULT_MODEL


def set_model(model):
    settings.set_value("model", model.strip() or DEFAULT_MODEL)


# ---------- plumbing ----------
def _require_key_and_text(transcript_text, api_key, model):
    api_key = (api_key or get_api_key()).strip()
    if not api_key:
        raise RuntimeError("No Anthropic API key set. Open Settings and paste one in first.")
    model = model or get_model()
    text = transcript_text.strip()
    if len(text) > 60000:  # keep the request reasonable for very long videos
        text = text[:60000] + "\n[...transcript truncated...]"
    if not text:
        raise RuntimeError("Transcript is empty — load an .srt file first.")
    return api_key, model, text


def _call_claude(prompt, api_key, model, timeout=60):
    """POST to the Messages API and return the concatenated text blocks."""
    body = json.dumps({
        "model": model,
        "max_tokens": 1500,
        "messages": [{"role": "user", "content": prompt}],
    }).encode("utf-8")
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "ignore")
        hint = ""
        if e.code == 401:
            hint = " (check that your API key in Settings is correct)"
        elif e.code == 404:
            hint = " (check the model name in Settings — it may be outdated)"
        raise RuntimeError(f"Anthropic API error {e.code}{hint}: {detail[:300]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Couldn't reach the Anthropic API: {e.reason}")

    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    raw = "\n".join(parts).strip().strip("`")
    if raw[:4].lower() == "json":
        raw = raw[4:].strip()
    return raw


def _extract_json(raw, open_ch, close_ch):
    try:
        return json.loads(raw)
    except Exception:
        start, end = raw.find(open_ch), raw.rfind(close_ch)
        if start == -1 or end == -1:
            raise RuntimeError("Claude's response wasn't valid JSON:\n" + raw[:500])
        return json.loads(raw[start:end + 1])


# ---------- guardrails applied to whatever the model returns ----------
def limit_sentences(text, max_sentences=DESCRIPTION_MAX_SENTENCES):
    text = re.sub(r"[ \t]+", " ", text.strip())
    sentences = re.split(r"(?<=[.!?])\s+", text)
    if len(sentences) <= max_sentences:
        return text
    return " ".join(sentences[:max_sentences])


def tags_length(tags):
    """Length YouTube counts: commas between tags, plus quote marks around multi-word tags."""
    total = 0
    for i, t in enumerate(tags):
        total += len(t) + (2 if " " in t else 0) + (1 if i else 0)
    return total


def clean_tags(tags, limit=TAGS_CHAR_LIMIT):
    out, seen = [], set()
    for t in tags:
        t = re.sub(r"\s+", " ", str(t).lstrip("#").strip().strip(",")).strip()
        if not t or t.lower() in seen:
            continue
        if tags_length(out + [t]) > limit:
            continue  # skip one that doesn't fit, but keep trying shorter ones after it
        seen.add(t.lower())
        out.append(t)
    return out


def clean_hashtags(tags, max_count=3):
    out = []
    for t in tags:
        t = "#" + re.sub(r"[^\w]", "", str(t))
        if len(t) > 1 and t.lower() not in [x.lower() for x in out]:
            out.append(t)
    return out[:max_count]


# ---------- public API ----------
def suggest_headlines(transcript_text, n=6, api_key=None, model=None, avoid=None, timeout=60):
    """avoid: optional list of existing headlines the new ones shouldn't repeat
    (used for the per-row "reprompt" button)."""
    api_key, model, text = _require_key_and_text(transcript_text, api_key, model)

    avoid = [a for a in (avoid or []) if a.strip()]
    avoid_clause = ""
    if avoid:
        bullet_list = "\n".join(f'  - "{a}"' for a in avoid)
        avoid_clause = ("- Do NOT repeat or closely rephrase any of these existing headlines:\n"
                        f"{bullet_list}\n")

    prompt = HEADLINE_PROMPT.format(n=n, transcript=text, avoid_clause=avoid_clause)
    raw = _call_claude(prompt, api_key, model, timeout)
    items = _extract_json(raw, "[", "]")
    return [(str(it.get("headline", "")).strip(), str(it.get("tag", "")).strip())
            for it in items[:n]]


def suggest_description_and_tags(transcript_text, api_key=None, model=None, timeout=60):
    """Returns (description_teaser, tags, hashtags)."""
    api_key, model, text = _require_key_and_text(transcript_text, api_key, model)
    raw = _call_claude(DESCRIPTION_PROMPT.format(transcript=text), api_key, model, timeout)
    obj = _extract_json(raw, "{", "}")
    description = limit_sentences(str(obj.get("description", "")))
    tags = clean_tags(obj.get("tags", []))
    hashtags = clean_hashtags(obj.get("hashtags", []))
    return description, tags, hashtags
