"""
Site-wide branding, company, and SEO defaults, exposed to every template as
`site` and `seo_defaults`. Backed entirely by settings sourced from `.env`
(see config/settings.py) so re-branding the storefront never requires
editing template or Python code — only environment variables.
"""

import re

from django.conf import settings
from django.templatetags.static import static


def site_config(request):
    logo_url = static(settings.SITE_LOGO_PATH) if settings.SITE_LOGO_PATH else ""
    whatsapp_number = settings.WHATSAPP_NUMBER or re.sub(r"\D", "", settings.COMPANY_PHONE)
    og_image_url = (
        request.build_absolute_uri(static(settings.SEO_DEFAULT_OG_IMAGE_PATH))
        if settings.SEO_DEFAULT_OG_IMAGE_PATH
        else ""
    )

    address_parts = [
        settings.COMPANY_ADDRESS_LINE1,
        settings.COMPANY_ADDRESS_LINE2,
        ", ".join(
            part for part in [settings.COMPANY_CITY, settings.COMPANY_STATE, settings.COMPANY_POSTAL_CODE] if part
        ),
        settings.COMPANY_COUNTRY,
    ]
    company_address_lines = [part for part in address_parts if part]

    site = {
        "name": settings.SITE_NAME,
        "legal_name": settings.SITE_LEGAL_NAME,
        "tagline": settings.SITE_TAGLINE,
        "domain": settings.SITE_DOMAIN,
        "scheme": settings.SITE_SCHEME,
        "logo_url": logo_url,
        "initials": "".join(word[0] for word in settings.SITE_NAME.split()[:2]).upper(),
        "company_address_lines": company_address_lines,
        "company_phone": settings.COMPANY_PHONE,
        "company_email": settings.COMPANY_EMAIL,
        "company_support_hours": settings.COMPANY_SUPPORT_HOURS,
        "company_gst_number": settings.COMPANY_GST_NUMBER,
        "grievance_officer_name": settings.GRIEVANCE_OFFICER_NAME,
        "grievance_officer_email": settings.GRIEVANCE_OFFICER_EMAIL,
        "grievance_officer_phone": settings.GRIEVANCE_OFFICER_PHONE,
        "grievance_response_days": settings.GRIEVANCE_RESPONSE_DAYS,
        "ga_measurement_id": settings.GA_MEASUREMENT_ID,
        "google_site_verification": settings.GOOGLE_SITE_VERIFICATION,
        "whatsapp_number": whatsapp_number,
        "whatsapp_default_message": f"Hi! I have a question about a product on {settings.SITE_NAME}.",
    }

    seo_defaults = {
        "title": settings.SEO_DEFAULT_TITLE,
        "description": settings.SEO_DEFAULT_DESCRIPTION,
        "keywords": settings.SEO_DEFAULT_KEYWORDS,
        "og_image_url": og_image_url,
        "twitter_handle": settings.SEO_TWITTER_HANDLE,
    }

    return {"site": site, "seo_defaults": seo_defaults}
