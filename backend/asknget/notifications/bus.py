"""emit(): the one call business code makes when something happens.

    from notifications import bus
    bus.emit("order.placed", order, actor=user)

Call it inside the transaction that made the change. It records the Event, writes each audience's in-app notifications and the
outbox rows for e-mail in that same transaction (so a rollback leaves nothing behind), and sends the e-mail once the transaction
commits. `deliver_notifications` retries anything that failed. Who is told, and how, is `NotificationRule` (admin-editable) over
`catalog.DEFAULTS`; what they are told is the kind's `NotificationTemplate`.
"""

import logging
from datetime import timedelta

from django.core.mail import send_mail
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from markets.i18n import pick

from . import catalog
from .defaults import CATEGORY_SECTION, EMAIL_SUBJECTS, TEMPLATES
from .models import Delivery, Event, Notification, NotificationRule, NotificationTemplate

log = logging.getLogger(__name__)

RETRY_AFTER = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30), timedelta(hours=2), timedelta(hours=12)]
STALE_SENDING = timedelta(minutes=15)          # a process that died mid-send: try again


# --------------------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------------------

RULE_FIELDS = ("is_active", "in_app", "email", "realtime", "user_can_opt_out", "default_on", "staff_permission", "min_amount_cents")


def rules_for(event: str, market_id) -> dict[str, catalog.Default]:
    """The rule that applies to each audience of `event` in `market_id`: that market's row, else the every-market row, else the
    catalogue default. One query."""
    rows = NotificationRule.objects.filter(event=event).filter(Q(market_id=market_id) | Q(market__isnull=True))
    by = {(r.audience, r.market_id is not None): r for r in rows}
    result = {}
    for audience, default in catalog.DEFAULTS[event].items():
        row = by.get((audience, True)) or by.get((audience, False))
        if row is None:
            result[audience] = default
        else:
            values = {f: getattr(row, f) for f in RULE_FIELDS}
            values["staff_permission"] = values["staff_permission"] or default.staff_permission
            result[audience] = catalog.Default(**values)
    return result


# --------------------------------------------------------------------------------------
# Emitting
# --------------------------------------------------------------------------------------


def emit(name: str, subject, *, actor=None, **payload) -> Event | None:
    """Tell everyone the rules say should know that `name` happened to `subject`. Returns the Event (None if nobody is told)."""
    spec = catalog.EVENTS[name]
    market_id = spec.market(subject)
    rules = rules_for(name, market_id)
    targets = []
    for target in spec.build(subject, **payload):
        rule = rules.get(target.audience)
        if rule is None or not rule.is_active or not (rule.in_app or rule.email):
            continue
        if rule.min_amount_cents and (target.amount_cents or 0) < rule.min_amount_cents:
            continue
        targets.append((target, rule))
    if not targets:
        return None

    event = Event.objects.create(
        name=name, actor=actor if getattr(actor, "pk", None) else None, market_id=market_id, subject_type=spec.subject_type,
        subject_id=str(subject.pk), payload=_jsonable(payload),
    )
    known = {t.kind: t for t in NotificationTemplate.objects.filter(kind__in={t.kind for t, _ in targets})}
    country = _country(market_id) if any(rule.email for _, rule in targets) else None
    rows, outbox, rule_of = [], [], {}
    teams = _store_teams({t.store_id for t, rule in targets if rule.email and t.audience == catalog.SELLER})
    for target, rule in targets:
        template, default = known.get(target.kind), TEMPLATES.get(target.kind)
        if template is not None and not template.is_active:
            continue                                    # an admin switched this wording off
        category = template.category if template else default[0]
        if rule.in_app and not (target.audience == catalog.CUSTOMER and target.user is None):     # a guest has no bell
            rows.append(row := Notification(
                audience=target.audience, user=target.user if target.audience == catalog.CUSTOMER else None, store_id=target.store_id,
                market_id=target.market_id if target.audience == catalog.STAFF else None,
                permission=rule.staff_permission if target.audience == catalog.STAFF else "", event=event, category=category,
                kind=target.kind, params=_jsonable(target.params), link_type=target.link[0] if target.link else "",
                link_id=str(target.link[1]) if target.link else "",
            ))
            rule_of[row.pk] = rule
        if rule.email and target.mail:
            for user, address, language in recipients(target, rule, category, teams):
                subject_line, body = render_email(target.kind, template, target.params, language, country)
                outbox.append(Delivery(
                    key=f"{event.pk}:email:{address.lower()}:{target.kind}", event=event, channel="email", user=user, to=address,
                    subject=subject_line[:255], body=body,
                ))
    Notification.objects.bulk_create(rows)
    Delivery.objects.bulk_create(outbox, ignore_conflicts=True)
    if outbox:
        transaction.on_commit(lambda: _deliver_quietly(event.pk))
    if rows:
        transaction.on_commit(lambda: _push_quietly(rows, rule_of))
    return event


def _push_quietly(rows, rule_of) -> None:
    """After commit: the live bell. Best effort; the database already has everything."""
    from . import realtime

    try:
        realtime.published(rows, rule_of)
    except Exception:
        log.exception("pushing %s notifications failed; clients catch up on their next refresh", len(rows))


def _deliver_quietly(event_id) -> None:
    """Runs after the change is saved: a mail problem must not turn a saved order into an error page. What is not sent now is
    retried by `deliver_notifications`."""
    try:
        deliver(event_id=event_id)
    except Exception:
        log.exception("sending the e-mail of event %s failed; deliver_notifications will retry", event_id)


def _country(market_id):
    from markets.models import Country

    return Country.objects.filter(pk=market_id).first() if market_id else None


def _jsonable(value):
    """Payload values that are model instances become their primary key; dicts and lists are walked."""
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "pk"):
        return str(value.pk)
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


# --------------------------------------------------------------------------------------
# Who gets the e-mail
# --------------------------------------------------------------------------------------


def recipients(target: catalog.Target, rule, category: str, teams: dict | None = None) -> list[tuple]:
    """(user or None, address, language) for each person this target's e-mail goes to. `teams` caches each store's people for one
    event (an order tells the same store several things)."""
    if target.audience == catalog.CUSTOMER:
        if target.user is not None:
            return [(target.user, target.user.email, target.user.language)] if target.user.email else []
        return [(None, target.email, "")] if target.email else []
    if target.audience == catalog.SELLER:
        if teams is None or target.store_id not in teams:
            teams = _store_teams({target.store_id})
        section = CATEGORY_SECTION.get(category)
        return [(user, address, language) for user, address, language, access in teams[target.store_id]
                if access is None or section is None or access.allows(section, "view")]
    return [(u, u.email, u.language) for u in staff_with(rule.staff_permission, target.market_id)]


def _store_teams(store_ids) -> dict:
    """{store_id: [(user, address, language, access)]}: the owner (access None = everything) and every active member, for all the
    stores at once (two queries however many stores an order has)."""
    if not store_ids:
        return {}
    from sellers.models import Store
    from team.models import StoreMember

    teams = {s.pk: [(s.owner, s.owner.email, s.owner.language, None)] for s in Store.objects.filter(pk__in=store_ids).select_related("owner")}
    members = StoreMember.objects.filter(store_id__in=store_ids, status=StoreMember.Status.ACTIVE, user__isnull=False).select_related("user")
    for m in members:
        teams[m.store_id].append((m.user, m.user.email, m.user.language, m.access()))
    return {pk: [p for p in people if p[1]] for pk, people in teams.items()}


def staff_with(permission: str, market_id):
    """Active staff who hold `permission` (through a role or directly) and work in `market_id` (or in every market)."""
    from accounts.models import User

    holds = Q(is_superuser=True)
    if permission:
        holds |= Q(groups__permissions__codename=permission, groups__permissions__content_type__app_label="staff")
        holds |= Q(user_permissions__codename=permission, user_permissions__content_type__app_label="staff")
    works_there = Q(is_superuser=True) | Q(staff_profile__isnull=True) | Q(staff_profile__markets__isnull=True)
    if market_id:
        works_there |= Q(staff_profile__markets=market_id)
    return User.objects.filter(is_staff=True, is_active=True).filter(holds).filter(works_there).distinct().order_by("email")


# --------------------------------------------------------------------------------------
# Wording
# --------------------------------------------------------------------------------------


class _Params(dict):
    def __missing__(self, key):
        return ""                               # a placeholder with no value reads as empty, never as an error


def fill(text: str, params: dict) -> str:
    try:
        return text.format_map(_Params(params))
    except (ValueError, IndexError, KeyError, AttributeError):
        return text                             # an admin's stray brace must not break a notification


def prepared(params: dict, language=None, country=None) -> dict:
    """Per-language values picked for the reader; `amount` built from amount_cents + currency."""
    out = {k: (pick(v, language, country) if isinstance(v, dict) else v) for k, v in params.items()}
    if "amount_cents" in out and "currency" in out and out["amount_cents"] is not None:
        out["amount"] = f"{out['currency']} {out['amount_cents'] / 100:,.2f}"
    return out


def render_email(kind: str, template, params: dict, language: str, country) -> tuple[str, str]:
    default = TEMPLATES.get(kind)
    title_map = template.title if template else (default[1] if default else {})
    body_map = template.body if template else (default[2] if default else {})
    subject_map = (template.email_subject if template else None) or EMAIL_SUBJECTS.get(kind) or title_map
    email_map = (template.email_body if template else None) or body_map
    values = prepared(params, language or None, country)
    subject_line = fill(pick(subject_map, language or None, country, default=kind), values)
    body = fill(pick(email_map, language or None, country), values)
    return subject_line, f"{body}\n\nThe asknget team"


# --------------------------------------------------------------------------------------
# Sending
# --------------------------------------------------------------------------------------


def deliver(*, event_id=None, limit: int = 200) -> dict:
    """Send what is due. Safe to run from several places at once: each row is claimed with a conditional update, so only one
    sender ever sends it (works on SQLite and Postgres, no row locks needed)."""
    now = timezone.now()
    Delivery.objects.filter(status=Delivery.Status.SENDING, updated_at__lt=now - STALE_SENDING).update(status=Delivery.Status.PENDING)
    due = Delivery.objects.filter(status=Delivery.Status.PENDING).filter(Q(next_attempt_at__isnull=True) | Q(next_attempt_at__lte=now))
    if event_id is not None:
        due = due.filter(event_id=event_id)
    counts = {"sent": 0, "failed": 0, "retry": 0}
    for pk in list(due.order_by("created_at").values_list("pk", flat=True)[:limit]):
        claimed = Delivery.objects.filter(pk=pk, status=Delivery.Status.PENDING).update(
            status=Delivery.Status.SENDING, attempts=F("attempts") + 1, updated_at=timezone.now()
        )
        if not claimed:
            continue                                    # someone else is sending it
        d = Delivery.objects.get(pk=pk)
        try:
            send_mail(d.subject, d.body, None, [d.to], fail_silently=False)
        except Exception as exc:                       # any provider error: retry later, then give up
            log.warning("delivery %s to %s failed (attempt %s): %s", d.pk, d.to, d.attempts, exc)
            gave_up = d.attempts >= len(RETRY_AFTER)
            Delivery.objects.filter(pk=pk).update(
                status=Delivery.Status.FAILED if gave_up else Delivery.Status.PENDING, last_error=str(exc)[:500],
                next_attempt_at=None if gave_up else timezone.now() + RETRY_AFTER[d.attempts - 1], updated_at=timezone.now(),
            )
            counts["failed" if gave_up else "retry"] += 1
            continue
        Delivery.objects.filter(pk=pk).update(status=Delivery.Status.SENT, sent_at=timezone.now(), last_error="", updated_at=timezone.now())
        counts["sent"] += 1
    return counts
