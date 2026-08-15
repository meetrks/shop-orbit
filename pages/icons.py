"""
Small library of inline SVG icons for the homepage's Trust Strip and Why
AVR Collections sections (see pages.models.HomeTrustStripItem /
HomeValuePropItem). Unlike catalog.icons.get_category_icon, which
keyword-matches free-text subcategory names, these are picked by staff
from a fixed dropdown (ICON_CHOICES) — so lookup here is by exact key,
with the same "never build markup from untrusted input" property that
keeps mark_safe() safe: `key` only ever selects one of this module's own
hardcoded constants below, never contributes to the SVG string itself.
"""

from django.utils.safestring import mark_safe

_ICON_CLASS = "w-8 h-8"

_SHIELD_CHECK = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 3L19 6V11C19 15.4 16 19.2 12 20.5C8 19.2 5 15.4 5 11V6L12 3Z"
          stroke="#0284C7" stroke-width="1.5" stroke-linejoin="round"/>
    <path d="M9 12L11 14L15.5 9.5" stroke="#0284C7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

_LOCK = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <rect x="5" y="11" width="14" height="9" rx="2" stroke="#0284C7" stroke-width="1.5"/>
    <path d="M8 11V8C8 5.8 9.8 4 12 4C14.2 4 16 5.8 16 8V11"
          stroke="#0284C7" stroke-width="1.5" stroke-linecap="round"/>
    <circle cx="12" cy="15.5" r="1.5" fill="#0284C7"/>
</svg>"""

_RETURN_ARROW = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M5 9C6.5 6 9.2 4 12.5 4C17.2 4 21 7.8 21 12.5C21 17.2 17.2 21 12.5 21C8.6 21 5.3 18.3 4.3 14.7"
          stroke="#0284C7" stroke-width="1.5" stroke-linecap="round"/>
    <path d="M5 4V9H10" stroke="#0284C7" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

_TRUCK = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <rect x="2" y="7" width="12" height="9" rx="1" stroke="#0284C7" stroke-width="1.5"/>
    <path d="M14 10H18L21 13V16H14V10Z" stroke="#0284C7" stroke-width="1.5" stroke-linejoin="round"/>
    <circle cx="7" cy="18" r="1.6" fill="#0284C7"/>
    <circle cx="17.5" cy="18" r="1.6" fill="#0284C7"/>
</svg>"""

_GEM = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M5 9L12 4L19 9L12 20L5 9Z" stroke="#C6A15B" stroke-width="1.5" stroke-linejoin="round"/>
    <path d="M5 9H19M9 9L12 4L15 9M9 9L12 20M15 9L12 20" stroke="#C6A15B" stroke-width="1.2" stroke-linejoin="round"/>
</svg>"""

_HEART = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 20S3 14 3 8.5C3 5.8 5.1 4 7.5 4C9.2 4 10.6 5 12 6.8
             C13.4 5 14.8 4 16.5 4C18.9 4 21 5.8 21 8.5C21 14 12 20 12 20Z"
          fill="#F9A8D4" stroke="#EC4899" stroke-width="1.2"/>
</svg>"""

_SPARKLE = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 3L13.8 9.2L20 11L13.8 12.8L12 19L10.2 12.8L4 11L10.2 9.2L12 3Z" fill="#C6A15B"/>
</svg>"""

_TAG = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M12 4H19V11L11 19C10.4 19.6 9.4 19.6 8.8 19L4 14.2C3.4 13.6 3.4 12.6 4 12L12 4Z"
          stroke="#0284C7" stroke-width="1.5" stroke-linejoin="round"/>
    <circle cx="15.5" cy="7.5" r="1.3" fill="#0284C7"/>
</svg>"""

_HEADSET = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <path d="M4 13V11C4 6.6 7.6 3 12 3C16.4 3 20 6.6 20 11V13"
          stroke="#0284C7" stroke-width="1.5" stroke-linecap="round"/>
    <rect x="3" y="13" width="4" height="6" rx="1.5" stroke="#0284C7" stroke-width="1.5"/>
    <rect x="17" y="13" width="4" height="6" rx="1.5" stroke="#0284C7" stroke-width="1.5"/>
    <path d="M19 19V20C19 21.1 18.1 22 17 22H14" stroke="#0284C7" stroke-width="1.5" stroke-linecap="round"/>
</svg>"""

_MEDAL = f"""<svg viewBox="0 0 24 24" class="{_ICON_CLASS}" fill="none">
    <circle cx="12" cy="14" r="6" stroke="#C6A15B" stroke-width="1.5"/>
    <path d="M9.5 3L7 9.5L11 9L9.5 3Z" fill="#C6A15B"/>
    <path d="M14.5 3L17 9.5L13 9L14.5 3Z" fill="#C6A15B"/>
    <path d="M9.5 14L11.5 16L15 12"
          stroke="#C6A15B" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>"""

_ICONS_BY_KEY = {
    "shield-check": _SHIELD_CHECK,
    "lock": _LOCK,
    "return-arrow": _RETURN_ARROW,
    "truck": _TRUCK,
    "gem": _GEM,
    "heart": _HEART,
    "sparkle": _SPARKLE,
    "tag": _TAG,
    "headset": _HEADSET,
    "medal": _MEDAL,
}

ICON_CHOICES = [
    ("shield-check", "Shield (quality / guarantee)"),
    ("lock", "Lock (secure payments)"),
    ("return-arrow", "Return arrow (easy returns)"),
    ("truck", "Truck (fast shipping)"),
    ("gem", "Gem (certified / hallmark)"),
    ("heart", "Heart (handcrafted / loved)"),
    ("sparkle", "Sparkle (premium / quality)"),
    ("tag", "Price tag (affordable / value)"),
    ("headset", "Headset (support)"),
    ("medal", "Medal (guarantee / trust)"),
]


def get_named_icon(key):
    svg = _ICONS_BY_KEY.get(key, _SPARKLE)
    # `svg` here is always one of this module's own hardcoded constants
    # above, selected by exact-key lookup — never built from `key` itself
    # or any other untrusted data, so there's nothing here for mark_safe
    # to unsafely pass through.
    return mark_safe(svg)  # nosec B308, B703
