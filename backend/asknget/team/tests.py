"""Team / Managers: access levels per section, invitations, roles, and the owner's immunity."""

import re
from datetime import timedelta

from django.core import mail
from django.utils import timezone

from config.models import Setting
from core.testing import ApiTestCase, make_category, make_product, make_store, make_user
from team import sections
from team.models import StoreActivity, StoreMember, TeamRole

TEAM = "/api/v1/seller/team"
ME = "/api/v1/seller/me"
ACCEPT = "/api/v1/auth/team/accept"
NIL = "00000000-0000-0000-0000-000000000000"


class TeamTestCase(ApiTestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = make_user(is_seller=True, full_name="Sirosh Bashir")
        cls.store = make_store(owner=cls.owner, name="Huda Beauty")
        cls.store.is_premium = True          # analytics and chat are premium; keep that out of the permission tests
        cls.store.save(update_fields=["is_premium"])
        cls.beauty = make_category("Beauty")
        cls.product = make_product(cls.store, cls.beauty, "BB Cream", 1000, None, stock=5)

    def setUp(self):
        self.owner_token = self.token_for(self.owner)

    def member(self, perms=None, *, role=None, status=StoreMember.Status.ACTIVE, store=None, email=None, **user_extra):
        user = make_user(email=email, full_name=user_extra.pop("full_name", "Team Mate"), **user_extra)
        row = StoreMember.objects.create(
            store=store or self.store, user=user, email=user.email, role=role, permissions=perms or {}, status=status,
            joined_at=timezone.now() if status == StoreMember.Status.ACTIVE else None,
        )
        return row, self.token_for(user)

    def invite(self, email="new.hire@example.com", role="order-manager", **extra):
        with self.captureOnCommitCallbacks(execute=True):
            res = self.post(f"{TEAM}/invites", {"email": email, "role": role, **extra}, self.owner_token)
        return res

    def token_from_mail(self) -> str:
        return re.search(r"token=([\w-]+)", mail.outbox[-1].body).group(1)


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #


class SectionTests(TeamTestCase):
    def test_a_missing_section_is_none_and_manage_implies_view(self):
        access = sections.Access({"orders": "manage", "products": "view"})
        self.assertEqual(access.level("inventory"), "none")
        self.assertTrue(access.allows("orders", "view") and access.allows("orders", "manage"))
        self.assertTrue(access.allows("products", "view") and not access.allows("products", "manage"))
        self.assertFalse(access.allows("inventory", "view"))
        self.assertEqual(access.sections("view"), ["orders", "products"])

    def test_a_typo_cannot_silently_grant_or_deny(self):
        with self.assertRaisesMessage(ValueError, "unknown section"):
            sections.normalise({"order": "manage"})
        with self.assertRaisesMessage(ValueError, "levels must be"):
            sections.normalise({"orders": "admin"})

    def test_reading_needs_view_and_everything_else_needs_manage(self):
        self.assertEqual([sections.required_level(m) for m in ("GET", "HEAD", "OPTIONS")], ["view"] * 3)
        self.assertEqual([sections.required_level(m) for m in ("POST", "PATCH", "PUT", "DELETE")], ["manage"] * 4)


# --------------------------------------------------------------------------- #
# The matrix: every guarded router x none / view / manage
# --------------------------------------------------------------------------- #

# (section, a read, a write). Each write is sent with an empty body or a fake id: authorisation runs
# before validation, so a 422/404 means "let through" and 403 permission_denied means "stopped".
GUARDED = [
    ("products", ("get", "/api/v1/seller/products"), ("post", "/api/v1/seller/products")),
    ("products", ("get", "/api/v1/seller/delivery-options"), ("post", "/api/v1/seller/delivery-options")),
    ("inventory", ("get", "/api/v1/seller/inventory"), ("post", f"/api/v1/seller/inventory/{NIL}/adjust")),
    ("orders", ("get", "/api/v1/seller/orders"), ("post", f"/api/v1/seller/orders/{NIL}/status")),
    ("customers", ("get", "/api/v1/seller/customers"), ("post", "/api/v1/seller/customers/someone/notes")),
    ("customers", ("get", "/api/v1/seller/chat"), ("post", f"/api/v1/seller/chat/{NIL}/messages")),
    ("dashboard", ("get", "/api/v1/seller/dashboard"), None),
    ("analytics", ("get", "/api/v1/seller/analytics/overview"), None),
]


class PermissionMatrixTests(TeamTestCase):
    def call(self, method, url, token):
        fn = getattr(self, method)
        return fn(url, token=token) if method == "get" else fn(url, {}, token)

    def stopped(self, res):
        return res.status_code == 403 and res.json()["detail"] == "permission_denied"

    def test_none_view_and_manage_on_every_guarded_router(self):
        for section, read, write in GUARDED:
            for level in ("none", "view", "manage"):
                with self.subTest(section=section, level=level, read=read[1]):
                    _, token = self.member({section: level})
                    self.assertEqual(self.stopped(self.call(*read, token)), level == "none", f"read as {level}")
                    if write:
                        self.assertEqual(self.stopped(self.call(*write, token)), level != "manage", f"write as {level}")

    def test_having_one_section_does_not_open_another(self):
        _, token = self.member({"orders": "manage"})
        for section, read, _write in GUARDED:
            if section != "orders":
                self.assertTrue(self.stopped(self.call(*read, token)), read[1])

    def test_the_owner_passes_everywhere(self):
        for section, read, write in GUARDED:
            self.assertFalse(self.stopped(self.call(*read, self.owner_token)), read[1])
            if write:
                self.assertFalse(self.stopped(self.call(*write, self.owner_token)), write[1])

    def test_a_plan_limit_is_a_different_answer_from_a_permission_limit(self):
        self.store.is_premium = False
        self.store.save(update_fields=["is_premium"])
        _, token = self.member({"analytics": "manage"})
        res = self.get("/api/v1/seller/analytics/overview", token)
        self.assertEqual((res.status_code, res.json()["detail"]), (403, "premium_required"))

    def test_the_plan_page_is_readable_by_any_member(self):
        _, token = self.member({})
        self.assertEqual(self.get("/api/v1/seller/plan", token).status_code, 200)

    def test_search_only_returns_what_the_member_may_see(self):
        _, orders_only = self.member({"orders": "view"})
        _, products_only = self.member({"products": "view"})
        self.assertEqual(self.get("/api/v1/seller/search", orders_only, q="BB").json()["products"], [])
        found = self.get("/api/v1/seller/search", products_only, q="BB").json()
        self.assertEqual([p["label"] for p in found["products"]], ["BB Cream"])
        self.assertEqual((found["orders"], found["customers"]), ([], []))

    def test_a_buyer_account_is_still_told_it_is_not_a_seller(self):
        buyer = make_user()
        res = self.get("/api/v1/seller/dashboard", self.token_for(buyer))
        self.assertEqual((res.status_code, res.json()["detail"]), (403, "Seller account required"))


class OwnerOnlyTests(TeamTestCase):
    def test_team_management_is_owner_only_even_with_full_access(self):
        _, token = self.member({s: "manage" for s in sections.SECTIONS})
        for method, url in (("get", TEAM), ("get", f"{TEAM}/summary"), ("get", f"{TEAM}/roles"), ("post", f"{TEAM}/invites")):
            res = self.get(url, token) if method == "get" else self.post(url, {}, token)
            self.assertEqual((res.status_code, res.json()["detail"]), (403, "permission_denied"), url)


# --------------------------------------------------------------------------- #
# Which store, and who
# --------------------------------------------------------------------------- #


class MembershipResolutionTests(TeamTestCase):
    def test_me_tells_the_sidebar_what_to_show(self):
        row, token = self.member({"orders": "manage", "dashboard": "view"}, role=TeamRole.objects.get(key="order-manager"))
        body = self.get(ME, token).json()
        self.assertEqual((body["is_owner"], body["can_manage_team"]), (False, False))
        self.assertEqual(body["store"]["display_name"], "Huda Beauty")
        self.assertEqual(body["sections"], ["dashboard", "orders"])
        self.assertEqual({p["section"]: p["level"] for p in body["permissions"]}["orders"], "manage")

        owner = self.get(ME, self.owner_token).json()
        self.assertEqual((owner["is_owner"], owner["role"], owner["can_manage_team"]), (True, "Owner", True))
        self.assertEqual(owner["sections"], list(sections.SECTIONS))

    def test_a_suspended_invited_or_removed_member_is_turned_away(self):
        for status in (StoreMember.Status.SUSPENDED, StoreMember.Status.INVITED):
            _, token = self.member({"orders": "manage"}, status=status)
            res = self.get("/api/v1/seller/orders", token)
            self.assertEqual((res.status_code, res.json()["detail"]), (403, "no_active_store"), status)   # on a team, but not let in
        row, token = self.member({"orders": "manage"})
        self.assertEqual(self.get("/api/v1/seller/orders", token).status_code, 200)
        row.delete()                                                    # removed: no longer on any team
        res = self.get("/api/v1/seller/orders", token)
        self.assertEqual((res.status_code, res.json()["detail"]), (403, "Seller account required"))

    def test_a_store_that_is_not_active_cannot_be_worked_in(self):
        _, token = self.member({"orders": "manage"})
        self.store.status = "suspended"
        self.store.save(update_fields=["status"])
        self.assertEqual(self.get("/api/v1/seller/orders", token).json()["detail"], "no_active_store")

    def test_x_store_picks_between_stores_and_never_falls_back_silently(self):
        other = make_store(name="Other Co")
        user = make_user(full_name="Both Hats")
        mine = make_store(owner=user, name="My Own Shop")
        StoreMember.objects.create(store=other, user=user, email=user.email, permissions={"dashboard": "view"}, status="active", joined_at=timezone.now())
        token = self.token_for(user)

        self.assertEqual(self.get(ME, token).json()["store"]["id"], str(mine.pk))                      # own store first
        res = self.client.get(ME, headers={"Authorization": f"Bearer {token}", "X-Store": str(other.pk)})
        self.assertEqual((res.json()["store"]["id"], res.json()["is_owner"]), (str(other.pk), False))
        res = self.client.get(ME, headers={"Authorization": f"Bearer {token}", "X-Store": str(self.store.pk)})   # not theirs
        self.assertEqual((res.status_code, res.json()["detail"]), (403, "no_active_store"))
        res = self.client.get(ME, headers={"Authorization": f"Bearer {token}", "X-Store": "not-a-uuid"})
        self.assertEqual(res.status_code, 403)

    def test_last_active_is_touched_at_most_once_a_minute(self):
        row, token = self.member({"dashboard": "view"})
        self.get("/api/v1/seller/dashboard", token)
        first = StoreMember.objects.get(pk=row.pk).last_active_at
        self.assertIsNotNone(first)
        self.get("/api/v1/seller/dashboard", token)
        self.assertEqual(StoreMember.objects.get(pk=row.pk).last_active_at, first)       # within the minute: untouched
        StoreMember.objects.filter(pk=row.pk).update(last_active_at=timezone.now() - timedelta(minutes=2))
        self.get("/api/v1/seller/dashboard", token)
        self.assertGreater(StoreMember.objects.get(pk=row.pk).last_active_at, first - timedelta(seconds=1))


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #


class InviteTests(TeamTestCase):
    def test_inviting_sends_a_link_and_stores_only_its_hash(self):
        res = self.invite("new.hire@example.com", "order-manager")
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual((body["status"], body["role"], body["email"]), ("invited", "Order Manager", "new.hire@example.com"))
        self.assertEqual(len(mail.outbox), 1)
        token = self.token_from_mail()
        self.assertIn("Huda Beauty", mail.outbox[-1].subject)
        row = StoreMember.objects.get(email="new.hire@example.com")
        self.assertNotIn(token, row.invite_token_hash)                      # the raw token is not what is stored
        self.assertEqual(len(row.invite_token_hash), 64)
        self.assertEqual(sections.normalise(row.permissions), sections.normalise(TeamRole.objects.get(key="order-manager").permissions))

    def test_the_invite_lifetime_is_a_setting(self):
        Setting.objects.create(key="team.invite_expiry_days", value=2)
        self.invite()
        row = StoreMember.objects.get(email="new.hire@example.com")
        self.assertAlmostEqual((row.invite_expires_at - timezone.now()).total_seconds(), 2 * 86400, delta=60)

    def test_permissions_can_override_the_role_and_a_role_or_levels_are_required(self):
        self.invite("a@example.com", "order-manager", permissions={"orders": "view"})
        self.assertEqual(StoreMember.objects.get(email="a@example.com").permissions["orders"], "view")
        self.assertEqual(self.post(f"{TEAM}/invites", {"email": "b@example.com"}, self.owner_token).status_code, 422)
        self.assertEqual(self.post(f"{TEAM}/invites", {"email": "b@example.com", "role": "nope"}, self.owner_token).status_code, 422)
        self.assertEqual(self.post(f"{TEAM}/invites", {"email": "b@example.com", "permissions": {"order": "view"}}, self.owner_token).status_code, 422)

    def test_the_owner_and_duplicates_cannot_be_invited(self):
        self.assertEqual(self.invite(self.owner.email).status_code, 409)
        self.assertEqual(self.invite("dup@example.com").status_code, 201)
        self.assertEqual(self.invite("DUP@example.com").status_code, 409)         # case-insensitive

    def test_the_seat_limit_is_a_setting_per_store_market_or_platform(self):
        Setting.objects.create(key="team.max_members", scope="store", store=self.store, value=2)
        self.assertEqual(self.invite("one@example.com").status_code, 201)
        self.assertEqual(self.invite("two@example.com").status_code, 201)
        res = self.invite("three@example.com")
        self.assertEqual((res.status_code, res.json()["detail"]), (409, "member_limit"))
        # a suspended member does not occupy a seat
        StoreMember.objects.filter(email="two@example.com").update(status="suspended")
        self.assertEqual(self.invite("three@example.com").status_code, 201)

    def test_resending_issues_a_new_link_and_kills_the_old_one(self):
        self.invite()
        old = self.token_from_mail()
        member_id = StoreMember.objects.get(email="new.hire@example.com").pk
        with self.captureOnCommitCallbacks(execute=True):
            self.assertEqual(self.post(f"{TEAM}/{member_id}/resend", {}, self.owner_token).status_code, 200)
        new = self.token_from_mail()
        self.assertNotEqual(old, new)
        self.assertEqual(self.post(ACCEPT, {"token": old, "full_name": "X", "password": "Str0ngPassw0rd!"}).status_code, 404)
        self.assertEqual(self.post(ACCEPT, {"token": new, "full_name": "X", "password": "Str0ngPassw0rd!"}).status_code, 200)

    def test_only_a_pending_invitation_can_be_resent(self):
        row, _ = self.member({"orders": "view"})
        self.assertEqual(self.post(f"{TEAM}/{row.pk}/resend", {}, self.owner_token).status_code, 409)


class AcceptTests(TeamTestCase):
    def test_a_new_person_creates_an_account_and_is_signed_in(self):
        self.invite("new.hire@example.com", "order-manager")
        token = self.token_from_mail()
        res = self.post(ACCEPT, {"token": token, "full_name": "New Hire", "password": "Str0ngPassw0rd!"})
        self.assertEqual(res.status_code, 200, res.content)
        body = res.json()
        self.assertEqual((body["created_account"], body["role"], body["store"]["display_name"]), (True, "Order Manager", "Huda Beauty"))
        access = body["tokens"]["access_token"]
        self.assertEqual(self.get("/api/v1/seller/orders", access).status_code, 200)         # works at once
        self.assertEqual(self.get("/api/v1/seller/products", access).status_code, 403)       # but only what the role allows
        member = StoreMember.objects.get(email="new.hire@example.com")
        self.assertEqual((member.status, member.invite_token_hash), ("active", ""))
        self.assertIsNotNone(member.user.email_verified_at)                                  # the link arrived in that inbox
        self.assertFalse(member.user.is_seller)                                              # a member, not an applicant
        self.assertTrue(StoreActivity.objects.filter(action="join_team", store=self.store).exists())

    def test_an_existing_account_must_sign_in_and_accept_as_itself(self):
        existing = make_user(email="already@example.com", full_name="Already Here")
        self.invite("already@example.com")
        token = self.token_from_mail()
        anonymous = self.post(ACCEPT, {"token": token})
        self.assertEqual((anonymous.status_code, anonymous.json()["detail"]), (409, "account_exists"))
        signed_in = self.post(ACCEPT, {"token": token}, self.token_for(existing))
        self.assertEqual((signed_in.status_code, signed_in.json()["created_account"], signed_in.json()["tokens"]), (200, False, None))

    def test_another_account_cannot_use_the_link(self):
        self.invite("invited@example.com")
        token = self.token_from_mail()
        stranger = make_user(email="stranger@example.com")
        res = self.post(ACCEPT, {"token": token}, self.token_for(stranger))
        self.assertEqual(res.status_code, 403)
        self.assertEqual(StoreMember.objects.get(email="invited@example.com").status, "invited")

    def test_a_link_works_once(self):
        self.invite()
        token = self.token_from_mail()
        body = {"token": token, "full_name": "New Hire", "password": "Str0ngPassw0rd!"}
        self.assertEqual(self.post(ACCEPT, body).status_code, 200)
        self.assertEqual(self.post(ACCEPT, body).status_code, 404)

    def test_an_expired_link_says_so(self):
        self.invite()
        token = self.token_from_mail()
        StoreMember.objects.update(invite_expires_at=timezone.now() - timedelta(minutes=1))
        res = self.post(ACCEPT, {"token": token, "full_name": "New Hire", "password": "Str0ngPassw0rd!"})
        self.assertEqual(res.status_code, 410)
        listed = self.get(TEAM, self.owner_token).json()["items"]
        self.assertTrue(next(r for r in listed if r["email"] == "new.hire@example.com")["invite_expired"])

    def test_a_guessed_token_reveals_nothing(self):
        self.assertEqual(self.post(ACCEPT, {"token": "x" * 43, "full_name": "A", "password": "Str0ngPassw0rd!"}).status_code, 404)

    def test_a_new_account_needs_a_name_and_a_strong_password(self):
        self.invite()
        token = self.token_from_mail()
        self.assertEqual(self.post(ACCEPT, {"token": token}).status_code, 422)
        self.assertEqual(self.post(ACCEPT, {"token": token, "full_name": "A", "password": "password"}).status_code, 422)


# --------------------------------------------------------------------------- #
# Roles and editing
# --------------------------------------------------------------------------- #


class RoleAndEditTests(TeamTestCase):
    def test_roles_are_data_and_the_seeded_ones_are_listed(self):
        keys = [r["key"] for r in self.get(f"{TEAM}/roles", self.owner_token).json()]
        self.assertEqual(keys[:2], ["manager", "inventory-manager"])
        self.assertEqual(len(keys), 6)
        TeamRole.objects.filter(key="support-agent").update(is_active=False)
        self.assertNotIn("support-agent", [r["key"] for r in self.get(f"{TEAM}/roles", self.owner_token).json()])
        self.assertEqual(self.invite("a@example.com", "support-agent").status_code, 422)          # a retired role cannot be assigned

    def test_editing_a_role_never_changes_the_access_of_people_already_in_it(self):
        self.invite("early@example.com", "order-manager")
        before = dict(StoreMember.objects.get(email="early@example.com").permissions)
        role = TeamRole.objects.get(key="order-manager")
        role.permissions = {**role.permissions, "transactions": "manage"}
        role.save()
        self.assertEqual(StoreMember.objects.get(email="early@example.com").permissions, before)
        self.invite("late@example.com", "order-manager")
        self.assertEqual(StoreMember.objects.get(email="late@example.com").permissions["transactions"], "manage")

    def test_a_member_reads_custom_once_levels_leave_the_preset(self):
        self.invite("a@example.com", "order-manager")
        row = StoreMember.objects.get(email="a@example.com")
        self.assertEqual(self.get(f"{TEAM}/{row.pk}", self.owner_token).json()["role"], "Order Manager")
        res = self.patch(f"{TEAM}/{row.pk}", {"permissions": {**row.permissions, "products": "manage"}}, self.owner_token)
        self.assertEqual((res.status_code, res.json()["role"], res.json()["role_key"]), (200, "Custom", "order-manager"))

    def test_changing_role_resets_the_levels_unless_levels_are_sent_too(self):
        self.invite("a@example.com", "order-manager")
        row = StoreMember.objects.get(email="a@example.com")
        res = self.patch(f"{TEAM}/{row.pk}", {"role": "inventory-manager"}, self.owner_token)
        levels = {p["section"]: p["level"] for p in res.json()["permissions"]}
        self.assertEqual((res.json()["role"], levels["inventory"], levels["orders"]), ("Inventory Manager", "manage", "none"))

    def test_permissions_changes_apply_on_the_next_request(self):
        row, token = self.member({"orders": "manage"})
        self.assertEqual(self.get("/api/v1/seller/orders", token).status_code, 200)
        self.patch(f"{TEAM}/{row.pk}", {"permissions": {"orders": "none"}}, self.owner_token)
        self.assertEqual(self.get("/api/v1/seller/orders", token).json()["detail"], "permission_denied")

    def test_suspend_and_reinstate(self):
        row, token = self.member({"orders": "manage"})
        self.assertEqual(self.patch(f"{TEAM}/{row.pk}", {"status": "suspended"}, self.owner_token).json()["status"], "suspended")
        self.assertEqual(self.get("/api/v1/seller/orders", token).status_code, 403)
        self.assertEqual(self.patch(f"{TEAM}/{row.pk}", {"status": "active"}, self.owner_token).json()["status"], "active")
        self.assertEqual(self.get("/api/v1/seller/orders", token).status_code, 200)

    def test_a_pending_invitation_cannot_be_suspended(self):
        self.invite()
        row = StoreMember.objects.get(email="new.hire@example.com")
        self.assertEqual(self.patch(f"{TEAM}/{row.pk}", {"status": "suspended"}, self.owner_token).status_code, 409)

    def test_remove_a_member_or_cancel_an_invitation(self):
        row, token = self.member({"orders": "manage"})
        self.assertEqual(self.delete(f"{TEAM}/{row.pk}", self.owner_token).status_code, 204)
        self.assertEqual(self.get("/api/v1/seller/orders", token).status_code, 403)
        self.invite()
        pending = StoreMember.objects.get(email="new.hire@example.com")
        self.assertEqual(self.delete(f"{TEAM}/{pending.pk}", self.owner_token).status_code, 204)
        self.assertEqual(self.post(ACCEPT, {"token": self.token_from_mail(), "full_name": "A", "password": "Str0ngPassw0rd!"}).status_code, 404)

    def test_the_owner_cannot_be_changed_or_removed(self):
        for method, body in (("patch", {"status": "suspended"}), ("delete", None)):
            res = self.patch(f"{TEAM}/owner", body, self.owner_token) if method == "patch" else self.delete(f"{TEAM}/owner", self.owner_token)
            self.assertEqual(res.status_code, 409, method)

    def test_another_stores_member_is_not_found(self):
        other_owner = make_user(is_seller=True)
        other = make_store(owner=other_owner, name="Other Co")
        row, _ = self.member({"orders": "view"}, store=other)
        for res in (self.get(f"{TEAM}/{row.pk}", self.owner_token), self.patch(f"{TEAM}/{row.pk}", {"status": "suspended"}, self.owner_token),
                    self.delete(f"{TEAM}/{row.pk}", self.owner_token)):
            self.assertEqual(res.status_code, 404)


class ListAndDetailTests(TeamTestCase):
    def setUp(self):
        super().setUp()
        manager, agent = TeamRole.objects.get(key="manager"), TeamRole.objects.get(key="support-agent")
        self.manager, _ = self.member(manager.permissions, role=manager, full_name="Sarah Lewis", email="lewis.sarah@example.com")
        self.agent, _ = self.member(agent.permissions, role=agent, full_name="Olivia Brown", email="olivia@example.com")
        StoreMember.objects.filter(pk=self.manager.pk).update(last_active_at=timezone.now() - timedelta(minutes=12))
        self.invite("mia@example.com", "finance-manager")

    def test_the_owner_leads_the_table_and_the_cards_add_up(self):
        body = self.get(TEAM, self.owner_token).json()
        self.assertEqual(body["items"][0]["role"], "Owner")
        self.assertEqual([r["email"] for r in body["items"]][1:], ["lewis.sarah@example.com", "olivia@example.com", "mia@example.com"])
        self.assertEqual(self.get(f"{TEAM}/summary", self.owner_token).json(), {"total": 4, "active": 3, "pending_invites": 1, "managers": 1})

    def test_filters(self):
        def emails(**params):
            return [r["email"] for r in self.get(TEAM, self.owner_token, **params).json()["items"]]

        self.assertEqual(emails(status="invited"), ["mia@example.com"])
        self.assertEqual(emails(role="support-agent"), ["olivia@example.com"])
        self.assertEqual(emails(q="sarah"), ["lewis.sarah@example.com"])
        self.assertEqual(emails(last_active="today"), [self.owner.email, "lewis.sarah@example.com"])   # the owner just logged in
        StoreMember.objects.filter(pk=self.manager.pk).update(last_active_at=timezone.now() - timedelta(days=3))
        self.assertEqual(emails(last_active="today"), [self.owner.email])
        self.assertEqual(emails(last_active="week"), [self.owner.email, "lewis.sarah@example.com"])
        self.assertIn(self.owner.email, emails(status="active"))
        self.assertNotIn(self.owner.email, emails(status="invited"))

    def test_member_details_page(self):
        body = self.get(f"{TEAM}/{self.manager.pk}", self.owner_token).json()
        self.assertEqual((body["name"], body["role"], body["status"]), ("Sarah Lewis", "Manager", "active"))
        levels = {p["section"]: p["level"] for p in body["permissions"]}
        self.assertEqual(list(levels), list(sections.SECTIONS))                                 # the design's eight rows
        self.assertEqual(levels["orders"], "manage")
        self.assertEqual(self.get(f"{TEAM}/owner", self.owner_token).json()["role"], "Owner")

    def test_recent_activity_lists_what_that_person_did(self):
        StoreActivity.objects.create(store=self.store, actor=self.manager.user, action="update_inventory", summary="Updated inventory for Wireless Earbuds Pro")
        StoreActivity.objects.create(store=self.store, actor=self.agent.user, action="export_orders", summary="Exported orders CSV")
        mine = self.get(f"{TEAM}/{self.manager.pk}/activity", self.owner_token).json()
        self.assertEqual([a["summary"] for a in mine], ["Updated inventory for Wireless Earbuds Pro"])
        self.assertEqual(self.get(f"{TEAM}/{self.manager.pk}", self.owner_token).json()["recent_activity"][0]["action"], "update_inventory")


class ActivityLoggingTests(TeamTestCase):
    def mine(self, user):
        return list(StoreActivity.objects.filter(store=self.store, actor=user).order_by("created_at").values_list("action", "summary"))

    def test_a_price_change_is_logged_but_autosaving_other_fields_is_not(self):
        url = f"/api/v1/seller/products/{self.product.pk}"
        self.patch(url, {"short_description": "Lightweight cover"}, self.owner_token)
        self.assertEqual(self.mine(self.owner), [])
        self.patch(url, {"price_cents": 1500}, self.owner_token)
        self.assertEqual(self.mine(self.owner), [("update_price", "Updated price for BB Cream")])

    def test_it_is_attributed_to_the_person_who_did_it(self):
        row, token = self.member({"inventory": "manage"}, full_name="Inventory Ian")
        variant = self.product.variants.first()
        res = self.post(f"/api/v1/seller/inventory/{variant.pk}/adjust", {"set": 9, "note": "recount"}, token)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(self.mine(row.user), [("update_inventory", "Updated inventory for BB Cream")])
        self.assertEqual(self.mine(self.owner), [])
        # ...and it is what the owner sees on that person's Member Details page
        shown = self.get(f"{TEAM}/{row.pk}", self.owner_token).json()["recent_activity"]
        self.assertEqual([a["summary"] for a in shown], ["Updated inventory for BB Cream"])

    def test_exports_and_status_changes_are_logged(self):
        self.get("/api/v1/seller/orders/export.csv", self.owner_token)
        self.get("/api/v1/seller/inventory/export.csv", self.owner_token)
        self.get("/api/v1/seller/customers/export.csv", self.owner_token)
        self.post(f"/api/v1/seller/products/{self.product.pk}/status", {"action": "archive"}, self.owner_token)
        actions = [a for a, _ in self.mine(self.owner)]
        self.assertEqual(actions, ["export_orders", "export_inventory", "export_customers", "product_archive"])

    def test_delivery_option_edits_are_logged(self):
        res = self.post("/api/v1/seller/delivery-options", {"name": "Overnight", "price_cents": 2999}, self.owner_token)
        option_id = res.json()["id"]
        self.patch(f"/api/v1/seller/delivery-options/{option_id}", {"price_cents": 1999}, self.owner_token)
        self.delete(f"/api/v1/seller/delivery-options/{option_id}", self.owner_token)
        self.assertEqual([a for a, _ in self.mine(self.owner)], ["add_delivery_option", "update_delivery_option", "remove_delivery_option"])

    def test_a_refused_action_leaves_no_trace(self):
        _, token = self.member({"inventory": "view"})                          # may look, not change
        self.post(f"/api/v1/seller/inventory/{self.product.variants.first().pk}/adjust", {"set": 1}, token)
        self.assertEqual(StoreActivity.objects.filter(store=self.store, action="update_inventory").count(), 0)

    def test_team_changes_are_recorded_against_the_owner(self):
        self.invite("new.hire@example.com")
        row = StoreMember.objects.get(email="new.hire@example.com")
        self.patch(f"{TEAM}/{row.pk}", {"permissions": {"orders": "view"}}, self.owner_token)
        self.delete(f"{TEAM}/{row.pk}", self.owner_token)
        self.assertEqual([a for a, _ in self.mine(self.owner)], ["invite_member", "update_member", "remove_member"])
