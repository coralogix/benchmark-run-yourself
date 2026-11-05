import re
import unicodedata

_WORD = re.compile(r"[a-z0-9]+")
STOPWORDS = {"the", "a", "an", "and", "of", "with", "for", "in", "x"}


def truthy(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def slugify(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")


def clean_postcode(pc):
    # strips ALL whitespace, unlike utils.normalise_postcode
    return re.sub(r"\s", "", (pc or "")).upper()


def tokenise(text, drop_stopwords=True):
    words = _WORD.findall((text or "").lower())
    if drop_stopwords:
        words = [w for w in words if w not in STOPWORDS]
    return words


def truncate(s, n=80):
    if s is None:
        return ""
    return s if len(s) <= n else s[: n - 1] + "…"


def mask_email(email):
    if not email or "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return name[:1] + "***@" + domain


def humanise_category(cat):
    # "health_beauty" -> "Health Beauty"
    return (cat or "").replace("_", " ").title()


def category_terms(cat):
    """Search terms for a category: 'health_beauty' -> 'health beauty'."""
    return (cat or "").replace("_", " ").strip()


def short_id(s, n=8):
    return (s or "")[:n]
