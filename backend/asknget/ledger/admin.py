"""Staff tools. The ledger itself is append-only: entries are never edited or deleted here, only
added (as adjustments) or moved forward (payouts), always through the same service functions the API uses."""

from django import forms
from django.contrib import admin, messages
from django.utils import timezone
from ninja.errors import HttpError

from . import services
from .models import LedgerEntry, Payout


class AdjustmentForm(forms.ModelForm):
    class Meta:
        model = LedgerEntry
        fields = ("store", "amount_cents", "description")
        labels = {"description": "Reason", "amount_cents": "Amount (minor units, signed)"}
        help_texts = {"amount_cents": "+ credits the store, - debits it. 5000 = 50.00 in a two-decimal currency."}

    def clean(self):
        data = super().clean()
        if data.get("amount_cents") == 0:
            raise forms.ValidationError("An adjustment cannot be zero.")
        if not (data.get("description") or "").strip():
            raise forms.ValidationError("Say why: every adjustment needs a reason.")
        return data


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("reference", "created_at", "store", "type", "amount_cents", "currency", "status", "available_at", "order")
    list_filter = ("type", "status", "currency")
    search_fields = ("reference", "store__display_name", "order__order_number", "description")
    date_hierarchy = "created_at"
    list_select_related = ("store", "order")
    form = AdjustmentForm
    autocomplete_fields = ("store",)
    _all = [f.name for f in LedgerEntry._meta.fields if f.name != "id"]

    # Adding shows the three fields of an adjustment; opening an entry shows everything, read-only.
    def get_fields(self, request, obj=None):
        return self._all if obj else AdjustmentForm.Meta.fields

    def get_readonly_fields(self, request, obj=None):
        return self._all if obj else ()

    def has_change_permission(self, request, obj=None):      # history is not editable; adding an adjustment is the only write
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        obj.type = LedgerEntry.Type.ADJUSTMENT
        obj.currency = obj.store.country.currency
        obj.available_at = timezone.now()
        obj.status = LedgerEntry.Status.COMPLETED
        obj.created_by = request.user
        obj.reference = services.new_reference()
        obj.description = obj.description.strip()[:255]
        obj.save()


class PayoutForm(forms.ModelForm):
    class Meta:
        model = Payout
        fields = ("store", "amount_cents", "note")

    def clean(self):
        data = super().clean()
        if data.get("store") and data.get("amount_cents") is not None:
            try:
                services.validate_payout(data["store"], data["amount_cents"])
            except HttpError as exc:
                raise forms.ValidationError(exc.message) from None
        return data


@admin.register(Payout)
class PayoutAdmin(admin.ModelAdmin):
    list_display = ("reference", "created_at", "store", "amount_cents", "currency", "status", "completed_at")
    list_filter = ("status", "currency")
    search_fields = ("reference", "store__display_name")
    autocomplete_fields = ("store",)
    actions = ("mark_completed", "mark_failed")
    form = PayoutForm
    _all = ("reference", "store", "amount_cents", "currency", "status", "note", "created_by", "completed_at")

    def get_fields(self, request, obj=None):
        return self._all if obj else PayoutForm.Meta.fields

    def get_readonly_fields(self, request, obj=None):
        return self._all if obj else ()

    def has_change_permission(self, request, obj=None):
        return obj is None

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        payout = services.create_payout(obj.store, amount_cents=obj.amount_cents, note=obj.note, by=request.user)
        obj.pk, obj.reference, obj.currency, obj.status = payout.pk, payout.reference, payout.currency, payout.status
        obj._state.adding = False

    def _move(self, request, queryset, status):
        moved = 0
        for payout in queryset.select_related("store"):
            try:
                services.set_payout_status(payout, status, request.user)
                moved += 1
            except HttpError as exc:
                self.message_user(request, f"{payout.reference}: {exc.message}", messages.WARNING)
        self.message_user(request, f"{moved} payout(s) marked {status}.")

    @admin.action(description="Mark selected payouts completed")
    def mark_completed(self, request, queryset):
        self._move(request, queryset, "completed")

    @admin.action(description="Mark selected payouts failed (the money becomes available again)")
    def mark_failed(self, request, queryset):
        self._move(request, queryset, "failed")
