from django import forms
from django.contrib import admin
from django.utils.html import format_html

from . import registry
from .models import CommissionRule, Setting


class SettingForm(forms.ModelForm):
    # Only registered keys can be chosen, each with its label and default, so nobody
    # types a key that nothing reads.
    key = forms.ChoiceField(choices=())

    class Meta:
        model = Setting
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["key"].choices = [("", "---------")] + [
            (d.key, f"{d.label} — {d.key} (default: {d.default})") for d in sorted(registry.REGISTRY.values(), key=lambda d: d.key)
        ]


@admin.register(Setting)
class SettingAdmin(admin.ModelAdmin):
    form = SettingForm
    list_display = ("label", "key", "scope", "applies_to", "value", "default", "updated_by", "updated_at")
    list_filter = ("scope", "country")
    search_fields = ("key", "note")
    autocomplete_fields = ("store",)
    readonly_fields = ("help_text", "updated_by")
    fields = ("key", "help_text", "scope", "country", "store", "value", "note", "updated_by")

    @admin.display(description="Setting")
    def label(self, obj):
        d = registry.REGISTRY.get(obj.key)
        return d.label if d else format_html("<em>unregistered: {}</em>", obj.key)

    @admin.display(description="Applies to")
    def applies_to(self, obj):
        return obj.target

    @admin.display(description="Default")
    def default(self, obj):
        d = registry.REGISTRY.get(obj.key)
        return d.default if d else "—"

    @admin.display(description="What it does")
    def help_text(self, obj):
        d = registry.REGISTRY.get(obj.key) if obj else None
        return d.help if d else "Choose a setting and save to see what it does."

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(CommissionRule)
class CommissionRuleAdmin(admin.ModelAdmin):
    list_display = ("percent", "scope", "applies_to", "effective_from", "is_active", "note")
    list_filter = ("scope", "is_active", "country")
    list_editable = ("is_active",)
    search_fields = ("note",)
    autocomplete_fields = ("store", "category")
    ordering = ("scope", "-effective_from")

    @admin.display(description="Rate", ordering="rate_bps")
    def percent(self, obj):
        return f"{obj.rate_bps / 100:g} %"

    @admin.display(description="Applies to")
    def applies_to(self, obj):
        return obj.target
