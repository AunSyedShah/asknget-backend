from datetime import datetime
from typing import Literal, Optional

from ninja import Schema
from pydantic import Field

from accounts.schemas import TokenOut
from core.validators import EmailStr, NonEmptyStr, PasswordStr

Level = Literal["none", "view", "manage"]


class RoleOut(Schema):
    key: str
    name: str
    description: str
    permissions: dict[str, Level]
    counts_as_manager: bool


class PermissionOut(Schema):
    section: str
    label: str
    level: Level


class ActivityOut(Schema):
    id: str
    action: str
    summary: str
    created_at: datetime


class MemberRowOut(Schema):
    """One row of the Team Members table. The owner is synthesised as the first row (`id: "owner"`)."""

    id: str
    name: str
    email: str
    role: str = Field(description="Role name, 'Owner', or 'Custom' once levels were changed from the preset")
    role_key: Optional[str]
    status: Literal["active", "invited", "suspended"]
    is_owner: bool
    invite_expired: bool = False
    last_active_at: Optional[datetime]


class MemberDetailOut(MemberRowOut):
    phone: str
    joined_at: Optional[datetime]
    permissions: list[PermissionOut]
    recent_activity: list[ActivityOut]


class TeamSummaryOut(Schema):
    total: int
    active: int
    pending_invites: int
    managers: int


class InviteIn(Schema):
    email: EmailStr
    role: Optional[str] = Field(None, description="A role key from GET /seller/team/roles")
    permissions: Optional[dict[str, Level]] = Field(None, description="Overrides the role's levels, section by section")


class MemberPatchIn(Schema):
    role: Optional[str] = Field(None, description="Switching role resets the levels to that role's, unless permissions are sent too")
    permissions: Optional[dict[str, Level]] = None
    status: Optional[Literal["active", "suspended"]] = None


class StoreBriefOut(Schema):
    id: str
    display_name: str
    slug: str
    country: str
    is_premium: bool


class MeOut(Schema):
    """`GET /seller/me` — who is working, in which store, and what the sidebar may show."""

    store: StoreBriefOut
    is_owner: bool
    role: str
    permissions: list[PermissionOut]
    sections: list[str] = Field(description="The sections this person can at least view: render exactly these in the sidebar")
    can_manage_team: bool


class AcceptIn(Schema):
    token: NonEmptyStr
    full_name: Optional[NonEmptyStr] = Field(None, description="Only when the invited e-mail has no account yet")
    password: Optional[PasswordStr] = Field(None, description="Only when the invited e-mail has no account yet")


class AcceptOut(Schema):
    store: StoreBriefOut
    role: str
    created_account: bool
    tokens: Optional[TokenOut] = Field(None, description="Present when an account was just created, so the invitee is signed in")
