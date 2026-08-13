"""Registration, authentication, and profile forms for the accounts app."""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from common.constants import INDIAN_STATE_CHOICES
from fulfillment.services import is_pincode_serviceable, pincode_block_reason

from .models import Address

User = get_user_model()


class EmailLoginForm(AuthenticationForm):
    """Login form that swaps the default `username` field for `email`."""

    username = forms.EmailField(
        label=_("Email address"),
        widget=forms.EmailInput(attrs={"autofocus": True, "placeholder": "you@example.com"}),
    )
    password = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••"}),
    )

    error_messages = {
        "invalid_login": _("Please enter a correct email address and password. Both fields are case sensitive."),
        "inactive": _("This account has been deactivated."),
    }


class RegistrationForm(forms.ModelForm):
    """Self-service registration form — every new account is a customer."""

    password1 = forms.CharField(
        label=_("Password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••"}),
        help_text=_("Use at least 8 characters, avoiding common passwords."),
    )
    password2 = forms.CharField(
        label=_("Confirm password"),
        strip=False,
        widget=forms.PasswordInput(attrs={"placeholder": "••••••••"}),
    )

    class Meta:
        model = User
        fields = ["full_name", "email", "phone_number"]

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise ValidationError(_("An account with this email already exists."))
        return email

    def clean(self):
        cleaned_data = super().clean()
        password1 = cleaned_data.get("password1")
        password2 = cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            self.add_error("password2", _("The two password fields did not match."))
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class ProfileForm(forms.ModelForm):
    """Lets a logged-in user maintain their name and phone number. Shipping
    addresses are managed separately — see `AddressForm` — since a user can
    keep several."""

    class Meta:
        model = User
        fields = [
            "full_name",
            "phone_number",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Without an explicit helper, {% crispy form %} falls back to a
        # bare FormHelper(), which defaults form_tag=True — that renders
        # crispy's own <form> around the fields, nested inside the
        # template's own <form>. Browsers can't parse nested forms and
        # silently close the outer one early, stranding the real submit
        # button outside any form (it stops submitting). Every other
        # ModelForm rendered via {% crispy form %} in this codebase sets
        # this explicitly for the same reason.
        self.helper = FormHelper()
        self.helper.form_tag = False


class AddressForm(forms.ModelForm):
    """Add/edit one of a user's saved shipping addresses."""

    # We only ship within India, so the country isn't a real choice — shown
    # read-only rather than editable, same as at checkout.
    country = forms.CharField(initial="India", disabled=True)
    state = forms.ChoiceField(choices=INDIAN_STATE_CHOICES)

    class Meta:
        model = Address
        fields = [
            "label",
            "recipient_name",
            "phone_number",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "is_default",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.label_class = "block text-gray-700 text-sm font-semibold mb-1"
        self.helper.field_class = "mb-2"
        self.helper.layout = Layout(
            "label",
            "recipient_name",
            "phone_number",
            "address_line1",
            "address_line2",
            "city",
            "state",
            "postal_code",
            "country",
            "is_default",
        )

    def clean_postal_code(self):
        postal_code = self.cleaned_data["postal_code"].strip()
        if postal_code and not is_pincode_serviceable(postal_code):
            raise ValidationError(pincode_block_reason(postal_code))
        return postal_code
