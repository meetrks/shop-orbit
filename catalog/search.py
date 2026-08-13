"""
Search-relevance logic shared by every product-listing view (see
catalog.views.product_list/department_detail/category_detail/
subcategory_detail). `Product.search_vector` is kept up to date by a
signal in catalog.signals — this module only ever reads it.
"""

from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db.models import F, Q

from .models import Subcategory

# Below this trigram similarity, a title is treated as "not a match" for
# typo-tolerance purposes — tuned to catch a handful of misspelled
# characters without pulling in unrelated short titles.
TRIGRAM_SIMILARITY_THRESHOLD = 0.15


def _matching_subcategory_ids(query):
    """
    Subcategories whose own name, or their Category's/Department's name,
    matches `query` — computed live against these tiny taxonomy tables
    rather than baked into each product's stored search_vector, so
    renaming a taxonomy node doesn't require reindexing every product
    under it.
    """
    return Subcategory.objects.filter(
        Q(name__icontains=query) | Q(category__name__icontains=query) | Q(category__department__name__icontains=query)
    ).values_list("pk", flat=True)


def search_products(queryset, query):
    """
    Ranks `queryset` (already scoped to a taxonomy branch and whatever
    else the caller filtered by) by relevance to `query`, combining:
    - full-text search over the weighted `search_vector` (title/keywords/description)
    - trigram similarity on title, for typo tolerance
    - exact-ish SKU/variant-SKU matches
    - live taxonomy-name matches (see `_matching_subcategory_ids`)
    """
    search_query = SearchQuery(query, search_type="websearch")
    matching_subcategory_ids = list(_matching_subcategory_ids(query))

    queryset = (
        queryset.annotate(similarity=TrigramSimilarity("title", query))
        .annotate(rank=SearchRank("search_vector", search_query) + F("similarity"))
        .filter(
            Q(search_vector=search_query)
            | Q(similarity__gte=TRIGRAM_SIMILARITY_THRESHOLD)
            | Q(sku__icontains=query)
            | Q(variants__sku__icontains=query)
            | Q(subcategory_id__in=matching_subcategory_ids)
        )
        .distinct()
    )

    return queryset.order_by("-rank", "-created_at", "pk")
