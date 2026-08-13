"""
Sitemap classes for the storefront's browsable catalog — products and the
three-tier taxonomy (department/category/subcategory). Static pages (home,
policies, contact) live in pages.sitemaps since they're not catalog concerns.
"""

from django.contrib.sitemaps import Sitemap

from .models import Category, Department, Product, Subcategory


class ProductSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return Product.objects.filter(is_active=True).order_by("id")

    def lastmod(self, product):
        return product.updated_at


class DepartmentSitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Department.objects.all().order_by("id")


class CategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Category.objects.all().order_by("id")


class SubcategorySitemap(Sitemap):
    changefreq = "weekly"
    priority = 0.6

    def items(self):
        return Subcategory.objects.all().order_by("id")
