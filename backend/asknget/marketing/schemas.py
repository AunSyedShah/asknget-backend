"""Schemas for banners, newsletter and the contact form."""

from typing import Literal, Optional
from uuid import UUID

from ninja import Field, Schema

from core.validators import EmailStr, NonEmptyStr, PhoneStr

SubjectLit = Literal["general", "order_issue", "returns", "payment", "seller_support", "other"]
PlacementLit = Literal["hero", "top_discount", "category_strip"]


class BannerOut(Schema):
    id: UUID
    placement: PlacementLit
    eyebrow: str
    title: str
    subtitle: str
    badge_text: str
    cta_label: str
    cta_url: str
    image_url: str = Field(alias="image.url")
    sort_order: int


class NewsletterSubscribeIn(Schema):
    """'Stay In the Loop → Subscribe Now'."""

    email: EmailStr
    source: str = Field("landing_footer", max_length=40)


class ContactFormIn(Schema):
    """'Send a Message' on Contact Us, and 'Contact Seller Support' on the application screens."""

    first_name: NonEmptyStr = Field(max_length=80)
    last_name: NonEmptyStr = Field(max_length=80)
    email: EmailStr
    phone: Optional[PhoneStr] = None
    subject: SubjectLit = "general"
    message: NonEmptyStr = Field(max_length=4000, description="'Your Query'")


class ContactCreatedOut(Schema):
    id: UUID
    detail: str


class SiteChoiceOut(Schema):
    value: str
    label: str


class SiteInfoOut(Schema):
    """Footer / Contact Info block — values come from SiteSetting rows."""

    contact_phone: str = ""
    contact_email: str = ""
    address: str = ""
    social: dict = {}
    accepted_payments: list[str] = []
    contact_subjects: list[SiteChoiceOut] = []
