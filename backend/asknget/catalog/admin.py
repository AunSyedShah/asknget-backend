from django.contrib import admin

from .models import (
    Brand,
    Category,
    CategoryRequest,
    Product,
    ProductContentSection,
    ProductImage,
    ProductOptionType,
    ProductOptionValue,
    ProductVariant,
    Review,
    ReviewMedia,
    WishlistItem,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "parent", "slug", "product_counts", "sort_order", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}


class ProductImageInline(admin.TabularInline):
    model = ProductImage
    extra = 0


class ProductContentSectionInline(admin.TabularInline):
    model = ProductContentSection
    extra = 0


class ProductOptionTypeInline(admin.TabularInline):
    model = ProductOptionType
    extra = 0


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 0
    filter_horizontal = ("option_values",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "store", "category", "brand", "price_cents", "compare_at_price_cents",
                    "stock_status", "rating_avg", "status")
    list_filter = ("country", "status", "stock_status", "is_featured", "is_best_seller", "is_new_arrival", "category")
    search_fields = ("name", "slug", "store__display_name")
    prepopulated_fields = {"slug": ("name",)}
    inlines = [ProductImageInline, ProductContentSectionInline, ProductOptionTypeInline, ProductVariantInline]


class ProductOptionValueInline(admin.TabularInline):
    model = ProductOptionValue
    extra = 0


@admin.register(ProductOptionType)
class ProductOptionTypeAdmin(admin.ModelAdmin):
    list_display = ("product", "name", "sort_order")
    inlines = [ProductOptionValueInline]


class ReviewMediaInline(admin.TabularInline):
    model = ReviewMedia
    extra = 0


@admin.register(Review)
class ReviewAdmin(admin.ModelAdmin):
    list_display = ("product", "reviewer_name", "rating", "status", "is_verified_purchase", "created_at")
    list_filter = ("status", "rating")
    search_fields = ("product__name", "reviewer_name", "reviewer_email")
    inlines = [ReviewMediaInline]


@admin.register(WishlistItem)
class WishlistItemAdmin(admin.ModelAdmin):
    list_display = ("user", "product", "created_at")
    search_fields = ("user__email", "product__name")


@admin.register(CategoryRequest)
class CategoryRequestAdmin(admin.ModelAdmin):
    """Read here; decide through the admin API (it creates the category and tells the seller)."""

    list_display = ("name", "parent", "store", "status", "created_at", "reviewed_at")
    list_filter = ("status",)
    search_fields = ("name", "store__display_name")
    readonly_fields = [f.name for f in CategoryRequest._meta.fields]

    def has_add_permission(self, request):
        return False
