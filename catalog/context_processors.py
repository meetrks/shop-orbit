"""
Site-wide context processor that feeds the navigation bar's
Department -> Category -> Subcategory dropdown cascade on every page.
"""

from .models import Department


def taxonomy_nav(request):
    departments = Department.objects.prefetch_related("categories__subcategories").all()
    return {"nav_departments": departments}
