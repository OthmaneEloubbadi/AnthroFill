"""
gender_engine.py
=================
Shared, dependency-free (no new dataset needed) gender-guessing engine used
by BOTH app.py (the Flask API / index.html + nom_complets.html) and
db_genreFINAL.py (the batch matching script). Put this file next to them.

It plugs into your EXISTING reference_noms2.csv/.xlsx -- it does not need
any external name/gender dataset. Everything below is either:
  (a) a smarter way to read/use the reference data you already have, or
  (b) a general linguistic rule about Arabic/Moroccan naming conventions
      that holds regardless of which names happen to be in your file.

WHAT THIS FIXES / ADDS
-----------------------
1. Majority-vote reference loading
   If "Mohamed" appears in your reference file 40 times as M and once as F
   (typo, bad row, whatever), the old code did `reference[name] = gender`
   in a loop, so whichever row happened to be read LAST silently won.
   Here we count every vote and keep the majority, plus we expose the vote
   ratio as a confidence score.

2. Particles are never treated as first names
   "ben", "bnou", "ibn", "el", "al", "bel", "ould", "ait", "abou", "dit"
   etc. are grammatical connectors in Arabic/Berber names, never given
   names. The old NOM->PRENOM split just grabbed "the last word", which
   breaks the moment the last word is a particle or the family name.

3. Honorifics are a free, high-confidence gender signal
   "Moulay", "Sidi" -> always masculine. "Lalla", "Hajja" -> always
   feminine ("Hajj" alone -> masculine). If one of these shows up in a
   full name, we don't even need the reference file.

4. The "Abd + attribute" rule
   Any name built as Abd/Abdel/Abdul + another word (Abdelkader, Abd
   Errahim, Abdellatif...) is grammatically ALWAYS a boy's name in
   Arabic -- "servant of [an attribute of God]" -- even if that exact
   two-word spelling never appears verbatim in your reference file. This
   alone recovers a large chunk of "unknown name" cases for free.

5. Bidirectional multi-token scanning
   Given a full name string, we don't know if it's stored as
   "Prenom Nom" or "Nom Prenom". So we scan from the FRONT and from the
   BACK (skipping particles), each returning the first token that matches
   the reference. If both ends agree -> high confidence. If they disagree
   -> flagged as ambiguous instead of silently guessing wrong.

6. A "collapsed-letters" phonetic fallback key
   Typos/duplicated-letter transliteration variants (e.g. "abbdelah" vs
   "abdellah") are normalized by collapsing repeated letters
   (re.sub(r'(.)\\1+', r'\\1', ...)) and indexed once at load time, so a
   near-miss spelling can still hit an existing reference entry before
   falling back to rapidfuzz.

Everything here is additive: if you don't call the new methods, behavior
is unchanged.
"""

import re
import unicodedata
from collections import Counter

try:
    from rapidfuzz import fuzz, process
    HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    from difflib import SequenceMatcher, get_close_matches
    HAS_RAPIDFUZZ = False


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize(value):
    """Same normalization contract as app.py's normalize_text /
    db_genreFINAL.py's normaliser -- lowercase, accent-stripped, punctuation
    stripped down to letters/space/hyphen/apostrophe."""
    if value is None:
        return ''
    value = str(value)
    value = value.replace('\ufeff', '').replace('\u200b', '').replace('\xa0', ' ')
    value = value.strip().lower()
    value = unicodedata.normalize('NFKD', value)
    value = ''.join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z\s\-'/\.]", '', value)
    value = re.sub(r'\s+', ' ', value).strip()
    return value


def tokenize(text):
    """Split a normalized name into individual word tokens (splits on
    space / hyphen / apostrophe, unlike a plain .split())."""
    norm = normalize(text)
    if not norm:
        return []
    return [t.strip("'") for t in re.split(r"[\s\-']+", norm) if t.strip("'")]


def collapse_repeats(text):
    """abbdelah -> abdelah, mohammed -> mohamed. Used only as a fallback
    lookup key, never as the primary key, so it can't create false exact
    matches between genuinely different short names."""
    return re.sub(r'(.)\1+', r'\1', text)


# ---------------------------------------------------------------------------
# Linguistic knowledge that needs no dataset at all
# ---------------------------------------------------------------------------

# Connectors / clan-particles that are NEVER given names on their own.
PARTICLES = {
    'ben', 'bnou', 'bno', 'ibn', 'bin', 'bint', 'binte',
    'el', 'al', 'bel', 'lal',
    'ould', 'wald',
    'ait', 'ait-', 'aith',
    'abou', 'abu', 'bou',
    'dit', 'ep', 'epse', 'veuve', 'vve',
    'de', 'du', 'des', 'le', 'la',
}

# Honorific titles that are a strong, free gender signal by themselves.
HONORIFIC_GENDER = {
    'moulay': 'MASCULIN',
    'mouley': 'MASCULIN',
    'sidi': 'MASCULIN',
    'sid': 'MASCULIN',
    'hajj': 'MASCULIN',
    'hadj': 'MASCULIN',
    'lalla': 'FEMININ',
    'lla': 'FEMININ',
    'hajja': 'FEMININ',
    'hadja': 'FEMININ',
}

# "Abd/Abdel/Abdul + attribute" is grammatically always masculine
# ("servant of ..."), regardless of which attribute follows.
ABD_PREFIXES = ('abd', 'abed', 'abde')


def is_abd_compound(token):
    """True if a single token is itself an Abd-compound (e.g. 'abdelkader',
    already glued together)."""
    return any(token.startswith(p) and len(token) > len(p) + 1 for p in ABD_PREFIXES)


def is_abd_start(token):
    """True if this token alone is a bare 'abd'/'abdel'/'abdul' that is
    still waiting for its second half as the NEXT token (e.g. full name
    written as two separate words: 'abd' 'errahim')."""
    return token in {'abd', 'abdel', 'abdul', 'abdal', 'abdu'}


MIN_FUZZY_LEN = 3


# ---------------------------------------------------------------------------
# Reference dataset loading with majority vote + phonetic index
# ---------------------------------------------------------------------------

class GenderReference:
    """Wraps the reference_prenom2 dataset with majority-vote resolution and
    a collapsed-letters phonetic fallback index. Build once, reuse many
    times (loading is the expensive part)."""

    def __init__(self):
        self.votes = {}          # normalized_name -> Counter(gender -> count)
        self.gender = {}         # normalized_name -> majority gender (resolved)
        self.confidence = {}     # normalized_name -> majority ratio (0-1)
        self.collapsed_index = {}  # collapsed_key -> set of normalized_name
        self.names_list = []     # for rapidfuzz candidate pool
        self.variants = {}       # variant -> canonical

    def add_observation(self, raw_name, raw_gender):
        name = normalize(raw_name)
        gender = self._normalize_gender_code(raw_gender)
        if not name or not gender:
            return
        self.votes.setdefault(name, Counter())[gender] += 1

    def add_variant(self, raw_variant, raw_canonical):
        variant = normalize(raw_variant)
        canonical = normalize(raw_canonical)
        if variant and canonical:
            self.variants[variant] = canonical

    def finalize(self):
        """Resolve majority-vote winners and build the phonetic index.
        Call once after every add_observation() call is done."""
        for name, counter in self.votes.items():
            gender, count = counter.most_common(1)[0]
            total = sum(counter.values())
            self.gender[name] = gender
            self.confidence[name] = count / total if total else 0.0

        self.names_list = list(self.gender.keys())

        for name in self.names_list:
            key = collapse_repeats(name)
            self.collapsed_index.setdefault(key, set()).add(name)

    @staticmethod
    def _normalize_gender_code(value):
        if value is None:
            return ''
        norm = normalize(str(value))
        if norm in {'m', 'male', 'masculin', 'masculine'}:
            return 'MASCULIN'
        if norm in {'f', 'female', 'feminin', 'feminine'}:
            return 'FEMININ'
        norm_upper = str(value).strip().upper()
        if norm_upper in {'MASCULIN', 'FEMININ'}:
            return norm_upper
        return ''

    def resolve_variant(self, name):
        current = name
        seen = set()
        while current in self.variants and current not in seen:
            seen.add(current)
            current = self.variants[current]
        return current

    # -- lookups --------------------------------------------------------

    def exact(self, token):
        """Exact / variant-resolved lookup. Returns (gender, confidence,
        match_type, matched_name) or None."""
        if token in self.gender:
            return self.gender[token], self.confidence[token], 'exact reference match', token
        canonical = self.resolve_variant(token)
        if canonical in self.gender:
            return self.gender[canonical], self.confidence[canonical], 'known variant', canonical
        return None

    def collapsed(self, token):
        """Phonetic fallback for typo'd / duplicated-letter spellings.
        Only used when the exact lookup already failed."""
        key = collapse_repeats(token)
        candidates = self.collapsed_index.get(key)
        if not candidates:
            return None
        # If several different reference names collapse to the same key
        # but disagree on gender, this is too risky -- skip it.
        genders = {self.gender[c] for c in candidates}
        if len(genders) != 1:
            return None
        matched_name = next(iter(candidates))
        return (
            self.gender[matched_name],
            self.confidence[matched_name] * 0.9,  # slightly discounted
            'collapsed-letters match',
            matched_name,
        )

    def fuzzy(self, token, threshold=90):
        if not token or len(token) < MIN_FUZZY_LEN or not self.names_list:
            return None
        if HAS_RAPIDFUZZ:
            res = process.extractOne(
                token, self.names_list, scorer=fuzz.ratio, score_cutoff=threshold
            )
            if not res:
                return None
            best_match, score, _ = res
        else:
            matches = get_close_matches(token, self.names_list, n=1, cutoff=threshold / 100.0)
            if not matches:
                return None
            best_match = matches[0]
            score = SequenceMatcher(None, token, best_match).ratio() * 100
        # Guard against risky masc/fem endings flip (e.g. "karim" vs "karima")
        if token.endswith('a') != best_match.endswith('a') and score < 96:
            return None
        return (
            self.gender[best_match],
            self.confidence[best_match] * (score / 100.0),
            f'fuzzy match {score:.0f}%',
            best_match,
        )

    def lookup_token(self, token, allow_fuzzy=True, fuzzy_threshold=90):
        """Full lookup chain for a single token: exact -> collapsed ->
        (optional) fuzzy. Returns (gender, confidence, match_type,
        matched_name) or None."""
        if not token:
            return None
        token = self.resolve_variant(token)
        result = self.exact(token)
        if result:
            return result
        result = self.collapsed(token)
        if result:
            return result
        if allow_fuzzy:
            result = self.fuzzy(token, fuzzy_threshold)
            if result:
                return result
        return None


# ---------------------------------------------------------------------------
# High-level guessing logic (particles, honorifics, Abd-rule, bidirectional
# scan) built on top of a GenderReference instance.
# ---------------------------------------------------------------------------

def _content_tokens(tokens):
    """Tokens with particles removed, keeping their original order."""
    return [t for t in tokens if t not in PARTICLES]


def check_honorific(tokens):
    for token in tokens:
        if token in HONORIFIC_GENDER:
            return HONORIFIC_GENDER[token], 1.0, f"honorific title ('{token}')"
    return None


def check_abd_rule(tokens):
    """Detects the 'Abd + attribute' masculine construct, whether written
    as one glued token ('abdelkader') or two separate tokens
    ('abd', 'elkader')."""
    for token in tokens:
        if is_abd_compound(token):
            return 'MASCULIN', 0.97, f"Abd-compound name rule ('{token}')"
    for i, token in enumerate(tokens[:-1]):
        if is_abd_start(token):
            return 'MASCULIN', 0.97, f"Abd-compound name rule ('{token} {tokens[i + 1]}')"
    return None


def guess_from_tokens(tokens, reference: GenderReference, allow_fuzzy=True, fuzzy_threshold=90):
    """Core bidirectional guesser. `tokens` should already be normalized
    (see tokenize()). Returns a dict:
        {
          'gender': 'MASCULIN' | 'FEMININ' | None,
          'confidence': 0.0-1.0,
          'reason': str,
          'ambiguous': bool,
        }
    `ambiguous=True` means front/back scans disagreed -- caller should NOT
    silently auto-apply this, only surface it as a low-confidence
    suggestion for manual review.
    """
    if not tokens:
        return {'gender': None, 'confidence': 0.0, 'reason': 'no data', 'ambiguous': False}

    honorific_hit = check_honorific(tokens)
    if honorific_hit:
        gender, conf, reason = honorific_hit
        return {'gender': gender, 'confidence': conf, 'reason': reason, 'ambiguous': False}

    abd_hit = check_abd_rule(tokens)
    # We still try the reference first for a token-level exact match, but
    # the abd rule acts as a strong fallback if nothing else fires later.

    content = _content_tokens(tokens)
    if not content:
        content = tokens  # everything was a particle -- fall back to raw tokens

    def scan(order):
        for token in order:
            hit = reference.lookup_token(token, allow_fuzzy=allow_fuzzy, fuzzy_threshold=fuzzy_threshold)
            if hit:
                gender, conf, match_type, matched_name = hit
                return gender, conf, f"{match_type} on token '{token}' ('{matched_name}')"
        return None

    front_hit = scan(content)
    back_hit = scan(list(reversed(content))) if len(content) > 1 else front_hit

    if front_hit and back_hit:
        if front_hit[0] == back_hit[0]:
            gender = front_hit[0]
            conf = max(front_hit[1], back_hit[1])
            return {
                'gender': gender,
                'confidence': min(0.98, conf + 0.05),
                'reason': f"front & back scans agree ({front_hit[2]})",
                'ambiguous': False,
            }
        else:
            # Front/back disagree -- don't guess, but keep both leads visible.
            reason = f"front scan says {front_hit[0]} ({front_hit[2]}), back scan says {back_hit[0]} ({back_hit[2]})"
            if abd_hit:
                return {'gender': abd_hit[0], 'confidence': abd_hit[1], 'reason': abd_hit[2] + ' [overrides conflicting token scan]', 'ambiguous': False}
            return {'gender': None, 'confidence': 0.0, 'reason': reason, 'ambiguous': True}

    single_hit = front_hit or back_hit
    if single_hit:
        gender, conf, reason = single_hit
        return {'gender': gender, 'confidence': conf, 'reason': reason, 'ambiguous': False}

    if abd_hit:
        gender, conf, reason = abd_hit
        return {'gender': gender, 'confidence': conf, 'reason': reason, 'ambiguous': False}

    return {'gender': None, 'confidence': 0.0, 'reason': 'no match (reference, phonetic or fuzzy)', 'ambiguous': False}


def guess_gender(prenom_value=None, nom_value=None, reference: GenderReference = None,
                  allow_fuzzy=True, fuzzy_threshold=90):
    """Convenience entry point covering all real-world layouts:
      - PRENOM filled                       -> scan PRENOM's own tokens
      - PRENOM filled but no match found    -> fall back to scanning NOM
      - PRENOM empty, NOM holds full name   -> bidirectional scan of NOM

    Returns the same dict shape as guess_from_tokens(), plus 'source' set
    to 'prenom' or 'nom' so callers can tell which field the winning
    guess actually came from.
    """
    prenom_tokens = tokenize(prenom_value) if prenom_value else []
    nom_tokens = tokenize(nom_value) if nom_value else []

    if prenom_tokens:
        result = guess_from_tokens(prenom_tokens, reference, allow_fuzzy, fuzzy_threshold)
        if result['gender'] or result['ambiguous']:
            result['source'] = 'prenom'
            return result
        if nom_tokens:
            fallback = guess_from_tokens(nom_tokens, reference, allow_fuzzy, fuzzy_threshold)
            fallback['source'] = 'nom (prenom had no match)'
            return fallback
        result['source'] = 'prenom'
        return result

    if nom_tokens:
        result = guess_from_tokens(nom_tokens, reference, allow_fuzzy, fuzzy_threshold)
        result['source'] = 'nom'
        return result

    return {'gender': None, 'confidence': 0.0, 'reason': 'no data', 'ambiguous': False, 'source': None}
