"""Lexicon of common English words that double as product names.

When a product's name is also an everyday word ("Notion", "Signal", "Slack",
"Obsidian", "Spark", "Forge"), word-boundary keyword matching cannot tell a
product reference apart from ordinary usage. `is_ambiguous_name` flags those
products so the disambiguator can lean harder on semantic anchor similarity
(Layer 2) and the LLM gate (Layer 3) instead of trusting the keyword hit.

The list is intentionally a flat set of frequent, low-information nouns/verbs/
adjectives — many of which happen to be real product names — rather than an
exhaustive dictionary. A name is "ambiguous" if the whole name, or any single
token within it, is in the set.
"""

from __future__ import annotations

import re

# ~500 common words. Membership here means "this word shows up constantly in
# ordinary English, so a bare keyword match is weak evidence of a product
# reference." Curated to include the common-word product names Sift sees most
# (notion, obsidian, signal, slack, spark, fusion, horizon, pulse, forge, ...).
AMBIGUOUS_WORDS: frozenset[str] = frozenset(
    {
        # common-word product names (the motivating cases)
        "notion", "obsidian", "droid", "signal", "spark", "slack", "fusion",
        "horizon", "pulse", "forge", "arc", "bear", "things", "craft", "loop",
        "stream", "wave", "echo", "prism", "atlas", "nova", "orbit", "vault",
        "anchor", "beacon", "bolt", "canvas", "compass", "ember", "flow",
        "glide", "halo", "ink", "lens", "linear", "mercury", "motion", "nest",
        "notch", "origin", "pitch", "raycast", "relay", "ripple", "scout",
        "shift", "spike", "sprout", "stack", "swift", "tempo", "thread",
        "tide", "torch", "trail", "vector", "vista", "zoom", "drift", "fathom",
        "gather", "grid", "haven", "helix", "ivy", "jolt", "kite", "knot",
        "lattice", "mosaic", "nimbus", "pivot", "quill", "reflect", "sage",
        "scale", "shade", "slate", "sonar", "summit", "tangent", "tilt",
        "verge", "willow", "zephyr",
        # frequent common nouns
        "account", "action", "activity", "address", "amount", "answer", "area",
        "art", "article", "attention", "author", "back", "base", "beginning",
        "behavior", "benefit", "bird", "block", "board", "body", "book", "box",
        "boy", "branch", "brand", "bridge", "building", "business", "button",
        "call", "camera", "campaign", "capital", "card", "care", "career",
        "case", "cause", "cell", "center", "century", "chain", "chair",
        "challenge", "chance", "change", "channel", "chapter", "character",
        "charge", "chart", "check", "child", "choice", "church", "circle",
        "city", "claim", "class", "client", "climate", "clock", "club", "code",
        "coffee", "collection", "college", "color", "column", "comment",
        "community", "company", "concept", "concern", "condition", "connection",
        "content", "context", "control", "conversation", "cost", "country",
        "couple", "course", "court", "cover", "credit", "crew", "cup",
        "culture", "current", "customer", "cycle", "data", "date", "day",
        "deal", "death", "decade", "decision", "degree", "demand", "design",
        "detail", "development", "device", "difference", "direction", "display",
        "distance", "district", "document", "dog", "door", "dot", "draft",
        "drink", "drive", "drop", "ear", "earth", "east", "edge", "editor",
        "education", "effect", "effort", "element", "energy", "engine", "entry",
        "environment", "equipment", "error", "event", "example", "exchange",
        "experience", "expert", "eye", "face", "fact", "factor", "failure",
        "family", "father", "feature", "feedback", "feeling", "field", "figure",
        "file", "film", "finger", "fire", "fish", "floor", "focus", "food",
        "foot", "force", "form", "format", "foundation", "frame", "freedom",
        "friend", "front", "fruit", "function", "fund", "future", "game",
        "garden", "gate", "gift", "girl", "glass", "goal", "gold", "government",
        "grade", "graph", "ground", "group", "growth", "guide", "habit",
        "hair", "hand", "head", "health", "heart", "heat", "height", "help",
        "history", "home", "hope", "hour", "house", "human", "idea", "image",
        "impact", "income", "industry", "information", "instance", "instrument",
        "interest", "interface", "internet", "interview", "island", "issue",
        "item", "job", "journey", "joy", "judge", "jump", "justice", "key",
        "kid", "kind", "king", "kitchen", "knowledge", "lab", "labor", "lake",
        "land", "language", "law", "layer", "leader", "league", "learning",
        "letter", "level", "library", "life", "light", "line", "link", "list",
        "load", "location", "lock", "log", "love", "machine", "magazine",
        "mail", "main", "major", "man", "manager", "map", "march", "mark",
        "market", "mass", "master", "match", "material", "matter", "meal",
        "meaning", "measure", "media", "medium", "meeting", "member", "memory",
        "menu", "message", "metal", "meter", "method", "middle", "mile",
        "mind", "minute", "mirror", "mission", "mode", "model", "moment",
        "money", "month", "mood", "moon", "morning", "mother", "mountain",
        "mouse", "mouth", "move", "movie", "music", "name", "nation", "nature",
        "neck", "need", "network", "news", "night", "node", "north", "note",
        "notice", "number", "object", "ocean", "offer", "office", "oil",
        "option", "order", "organization", "outcome", "output", "owner",
        "page", "pain", "paint", "pair", "panel", "paper", "parent", "park",
        "part", "partner", "party", "passion", "past", "path", "pattern",
        "payment", "peace", "people", "performance", "period", "person",
        "phase", "phone", "photo", "phrase", "picture", "piece", "place",
        "plan", "plane", "planet", "plant", "plastic", "plate", "platform",
        "play", "player", "plus", "point", "police", "policy", "pool", "port",
        "position", "post", "pound", "power", "practice", "present", "press",
        "price", "pride", "primary", "print", "priority", "private", "prize",
        "problem", "process", "product", "profile", "profit", "program",
        "project", "promise", "property", "proposal", "protection", "purpose",
        "quality", "quantity", "queen", "question", "queue", "race", "radio",
        "rain", "range", "rate", "ratio", "reach", "reaction", "reading",
        "reality", "reason", "record", "region", "release", "report",
        "request", "research", "resource", "response", "rest", "result",
        "return", "review", "reward", "rhythm", "rice", "ride", "right",
        "ring", "rise", "risk", "river", "road", "rock", "role", "room",
        "root", "round", "route", "row", "rule", "run", "safety", "sale",
        "salt", "sample", "sand", "scene", "schedule", "scheme", "school",
        "science", "score", "screen", "sea", "search", "season", "seat",
        "second", "section", "sector", "security", "seed", "selection",
        "self", "sense", "series", "service", "session", "set", "setting",
        "shape", "share", "sheet", "ship", "shop", "shoulder", "show", "side",
        "sign", "silver", "site", "situation", "size", "skill", "skin", "sky",
        "sleep", "society", "software", "soil", "solution", "song", "sort",
        "sound", "source", "south", "space", "speed", "spirit", "sport",
        "spot", "spring", "square", "stage", "standard", "star", "start",
        "state", "station", "status", "step", "stick", "stock", "stone",
        "stop", "store", "story", "street", "strength", "stress", "string",
        "structure", "student", "study", "stuff", "style", "subject",
        "success", "summer", "sun", "support", "surface", "survey", "system",
        "table", "talk", "target", "task", "taste", "tax", "tea", "teacher",
        "team", "technology", "television", "temperature", "term", "test",
        "text", "theme", "theory", "thing", "thought", "throat", "time", "tip",
        "title", "tool", "tooth", "top", "topic", "total", "touch", "tour",
        "town", "track", "trade", "traffic", "training", "travel", "treatment",
        "tree", "trend", "trip", "trouble", "truck", "trust", "truth", "type",
        "uncle", "union", "unit", "university", "use", "user", "value",
        "variety", "vehicle", "version", "video", "view", "village", "voice",
        "volume", "wall", "war", "watch", "water", "way", "wealth", "weather",
        "web", "week", "weight", "west", "wheel", "whole", "wife", "wind",
        "window", "wine", "wing", "winter", "wire", "wisdom", "woman", "wood",
        "word", "work", "world", "writer", "writing", "year", "youth", "zone",
    }
)


def _tokens(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.casefold()) if t]


def is_ambiguous_name(name: str) -> bool:
    """True when the product name (whole, or any single token) is a common word.

    "Notion" -> True (whole name). "Notion AI" -> True (token "notion").
    "Factory Droid" -> True (token "droid"). "OpenCode" -> False.
    """
    if not name or not name.strip():
        return False
    normalized = name.casefold().strip()
    if normalized in AMBIGUOUS_WORDS:
        return True
    return any(tok in AMBIGUOUS_WORDS for tok in _tokens(name))
