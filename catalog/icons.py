"""
Small library of colored inline SVG icons for the homepage's "Shop by
Category" carousel (see pages.views.home), matched by keyword against a
Subcategory's name. Hand-drawn, dependency-free — no icon font or CDN
load, consistent with static/js/vendor/*'s "everything vendored, nothing
loaded remotely" rule. Falls back to a generic sparkle icon for anything
unmatched, so this never breaks as staff add subcategories the keyword
list doesn't cover yet — it's a cosmetic default, not a hard requirement
that every category be explicitly mapped.
"""

from django.utils.safestring import mark_safe

_ICON_CLASS = "w-10 h-10 sm:w-12 sm:h-12"

_RING = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <circle cx="12" cy="15" r="6" stroke="#C6A15B" stroke-width="2"/>
    <path d="M9 9L12 4L15 9L12 11.5L9 9Z" fill="#0EA5E9"/>
    <path d="M9 9H15L12 11.5L9 9Z" fill="#0284C7"/>
</svg>"""

_NECKLACE = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M4 4C4 10 8 13 12 13C16 13 20 10 20 4" stroke="#C6A15B" stroke-width="1.5" stroke-linecap="round"/>
    <circle cx="12" cy="17" r="3.5" fill="#F472B6"/>
</svg>"""

_EARRING = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <circle cx="12" cy="6" r="2.3" fill="#FBBF24"/>
    <path d="M12 8.3V13.5" stroke="#C6A15B" stroke-width="1.5"/>
    <path d="M8.5 13.5C8.5 16.5 10 19 12 19C14 19 15.5 16.5 15.5 13.5" fill="#F9A8D4" stroke="#EC4899"/>
</svg>"""

_BANGLE = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <circle cx="12" cy="12" r="8" stroke="#C6A15B" stroke-width="2.5"/>
    <circle cx="12" cy="12" r="4" stroke="#F472B6" stroke-width="2"/>
</svg>"""

_HAIR_ACCESSORY = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 12L4 7V17L12 12Z" fill="#F9A8D4"/>
    <path d="M12 12L20 7V17L12 12Z" fill="#EC4899"/>
    <circle cx="12" cy="12" r="1.8" fill="#C6A15B"/>
</svg>"""

_WATCH = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <circle cx="12" cy="12" r="7" stroke="#0284C7" stroke-width="2"/>
    <path d="M12 8V12L15 14" stroke="#0284C7" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M9 3H15M9 21H15" stroke="#94A3B8" stroke-width="2" stroke-linecap="round"/>
</svg>"""

_BAG = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M7 9C7 6 9 4 12 4C15 4 17 6 17 9" stroke="#C6A15B" stroke-width="1.5"/>
    <rect x="4" y="9" width="16" height="11" rx="2" fill="#FBBF24" fill-opacity="0.25"
          stroke="#C6A15B" stroke-width="1.5"/>
</svg>"""

_DRESS = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M9 4H15L14 8L18 20H6L10 8L9 4Z" fill="#F9A8D4" stroke="#EC4899" stroke-width="1.2"/>
    <path d="M9 4C9 4 9.5 6 12 6C14.5 6 15 4 15 4" stroke="#EC4899" stroke-width="1.2"/>
</svg>"""

_CLOTHING = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M9 4L5 7V10H7V20H17V10H19V7L15 4L12 6L9 4Z" fill="#7DD3FC" stroke="#0284C7" stroke-width="1.2"/>
</svg>"""

_SPARKLE_FALLBACK = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 3L13.8 9.2L20 11L13.8 12.8L12 19L10.2 12.8L4 11L10.2 9.2L12 3Z" fill="#C6A15B"/>
</svg>"""

# Checked in order — first keyword match wins. Longer/more specific
# category families first so e.g. "Necklaces & Chains" doesn't fall
# through to a less specific match by accident.
_ICONS_BY_KEYWORD = [
    (["ring"], _RING),
    (["necklace", "chain", "pendant", "mangalsutra"], _NECKLACE),
    (["earring", "stud", "jhumka"], _EARRING),
    (["bangle", "bracelet", "kada"], _BANGLE),
    (["hair", "clip", "pin", "bow"], _HAIR_ACCESSORY),
    (["watch"], _WATCH),
    (["bag", "purse", "clutch", "wallet"], _BAG),
    (["saree", "sari", "dress", "gown", "lehenga"], _DRESS),
    (["kurta", "kurti", "shirt", "top", "cloth"], _CLOTHING),
]


def get_category_icon(name):
    lowered = name.lower()
    svg = _SPARKLE_FALLBACK
    for keywords, candidate in _ICONS_BY_KEYWORD:
        if any(keyword in lowered for keyword in keywords):
            svg = candidate
            break
    # `svg` here is always one of this module's own hardcoded constants
    # above, selected by lookup — never built from `name` (the only
    # caller-supplied input) or any other untrusted data, so there's
    # nothing here for mark_safe to unsafely pass through.
    return mark_safe(svg)  # nosec B308, B703
