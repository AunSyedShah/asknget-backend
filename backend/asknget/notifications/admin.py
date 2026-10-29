from django import forms
from django.contrib import admin, messages

from . import catalog
from .bus import RULE_FIELDS
from .models import Delivery, Event, Notification, NotificationRule, NotificationTemplate, RuleChange


@admin.register(NotificationTemplate)
class NotificationTemplateAdmin(admin.ModelAdmin):
    list_display = ("kind", "category", "english_title", "is_active", "updated_at")
    list_filter = ("category", "is_active")
    list_editable = ("is_active",)
    search_fields = ("kind", "description")
    readonly_fields = ("kind",)

    @admin.display(description="Title (English)")
    def english_title(self, obj):
        return (obj.title or {}).get("en", "")

    def has_add_permission(self, request):
        return False             # kinds exist because code emits them; new wording is an edit, not a new row

    def has_delete_permission(self, request, obj=None):
        return False             # switch a kind off with `is_active` instead


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("created_at", "audience", "store", "user", "market", "category", "kind")
    list_filter = ("audience", "category", "kind")
    search_fields = ("store__display_name", "user__email", "kind")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class RuleForm(forms.ModelForm):
    event = forms.ChoiceField(choices=[(e, e) for e in catalog.EVENTS])

    class Meta:
        model = NotificationRule
        fields = "__all__"

    def clean(self):
        data = super().clean()
        event, audience = data.get("event"), data.get("audience")
        if event and audience and audience not in catalog.DEFAULTS.get(event, {}):
            told = ", ".join(catalog.DEFAULTS[event])
            raise forms.ValidationError(f"{event} has nothing to say to {audience}; it can be set for: {told}.")
        permission = data.get("staff_permission")
        if permission:
            from staff.permissions import CATALOGUE

            if permission not in CATALOGUE:
                raise forms.ValidationError({"staff_permission": f"Not a staff permission. One of: {', '.join(CATALOGUE)}"})
        return data


@admin.register(NotificationRule)
class NotificationRuleAdmin(admin.ModelAdmin):
    """Who is told about each event, and how. No row = the default in notifications/catalog.py; a market's row beats the row for
    every market. Every change is recorded (Rule changes)."""

    form = RuleForm
    list_display = ("event", "audience", "market", "is_active", "in_app", "email", "realtime", "user_can_opt_out", "staff_permission", "min_amount_cents")
    list_filter = ("audience", "market", "is_active", "email")
    search_fields = ("event",)

    def save_model(self, request, obj, form, change):
        before = {f: form.initial.get(f) for f in RULE_FIELDS} if change else {}
        super().save_model(request, obj, form, change)
        after = {f: getattr(obj, f) for f in RULE_FIELDS}
        diff = {f: [before.get(f), after[f]] for f in RULE_FIELDS if not change or before.get(f) != after[f]}
        if diff:
            RuleChange.objects.create(rule=obj, rule_label=str(obj)[:140], user=request.user, changes=diff)

    def delete_model(self, request, obj):
        RuleChange.objects.create(rule=None, rule_label=f"deleted: {obj}"[:140], user=request.user,
                                  changes={f: [getattr(obj, f), None] for f in RULE_FIELDS})
        super().delete_model(request, obj)


@admin.register(RuleChange)
class RuleChangeAdmin(admin.ModelAdmin):
    list_display = ("created_at", "rule_label", "user", "changes")
    search_fields = ("rule_label", "user__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "name", "market", "subject_type", "subject_id", "actor")
    list_filter = ("name", "market")
    search_fields = ("subject_id", "actor__email")
    date_hierarchy = "created_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = ("created_at", "to", "subject", "status", "attempts", "sent_at", "last_error")
    list_filter = ("status", "channel")
    search_fields = ("to", "subject")
    date_hierarchy = "created_at"
    actions = ["resend"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.action(description="Send again")
    def resend(self, request, queryset):
        from .bus import deliver

        n = queryset.exclude(status=Delivery.Status.SENDING).update(status=Delivery.Status.PENDING, next_attempt_at=None, attempts=0)
        result = deliver()
        self.message_user(request, f"{n} queued again: {result['sent']} sent, {result['retry']} will be retried, {result['failed']} failed.", messages.INFO)
