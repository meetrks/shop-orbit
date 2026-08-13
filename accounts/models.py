"""
Custom, email-based user model for shoporbit.

Django ships with a username/password auth model by default. This project
instead authenticates every account by email address, which is what the
storefront's registration and login forms collect. There's a single
operator running this store — every registered `User` is just a customer;
operator access is `is_staff`/`is_superuser`, inherited from `PermissionsMixin`.
"""

from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils.translation import gettext_lazy as _

from common.models import TimeStampedModel

from .managers import UserManager

phone_number_validator = RegexValidator(
    regex=r"^[0-9]{10}$",
    message=_("Enter a valid 10-digit phone number."),
)


class User(AbstractBaseUser, PermissionsMixin, TimeStampedModel):
    """A customer account. Email is the unique identifier used for login."""

    email = models.EmailField(_("email address"), unique=True)
    full_name = models.CharField(_("full name"), max_length=150)
    phone_number = models.CharField(
        _("phone number"),
        max_length=16,
        blank=True,
        validators=[phone_number_validator],
    )

    is_active = models.BooleanField(_("active"), default=True)
    is_staff = models.BooleanField(_("staff status"), default=False)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["full_name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.full_name} <{self.email}>"

    def get_full_name(self):
        return self.full_name

    def get_short_name(self):
        return self.full_name.split(" ")[0] if self.full_name else self.email


class Address(TimeStampedModel):
    """
    A saved shipping address. A user may keep several (home, work, ...);
    at most one is ever `is_default` at a time — enforced in
    `accounts.services`, not here, since "unset every other default"
    is a multi-row operation a model-level constraint can't express.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="addresses")
    label = models.CharField(_("label"), max_length=50, blank=True, help_text=_("e.g. Home, Work"))
    recipient_name = models.CharField(_("recipient name"), max_length=150)
    phone_number = models.CharField(_("phone number"), max_length=16, validators=[phone_number_validator])
    address_line1 = models.CharField(_("address line 1"), max_length=255)
    address_line2 = models.CharField(_("address line 2"), max_length=255, blank=True)
    city = models.CharField(_("city"), max_length=100)
    state = models.CharField(_("state"), max_length=100)
    postal_code = models.CharField(_("postal code"), max_length=12)
    country = models.CharField(_("country"), max_length=100, default="India")
    is_default = models.BooleanField(_("default address"), default=False)

    class Meta:
        verbose_name_plural = "addresses"
        ordering = ["-is_default", "-created_at"]

    def __str__(self):
        return f"{self.label or self.recipient_name} ({self.city}) — {self.user.email}"
