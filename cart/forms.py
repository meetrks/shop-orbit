"""Forms used during cart management and checkout."""

from crispy_forms.helper import FormHelper
from crispy_forms.layout import Layout
from django import forms

from accounts.models import Address
from common.constants import INDIAN_STATE_CHOICES
from fulfillment.services import is_pincode_serviceable, pincode_block_reason

from .models import Coupon, Order


class AddToCartForm(forms.Form):
    quantity = forms.IntegerField(min_value=1, initial=1)


class CheckoutForm(forms.ModelForm):
    """
    Collects shipping details and an optional coupon code. Payment itself
    happens after this form is submitted, via Razorpay Checkout (see
    `cart.views.checkout`) — this form only freezes the cart into an
    `Order` in `AWAITING_PAYMENT` status.

    Coupon validation needs the buyer and cart subtotal, which aren't
    available at form-class-definition time, so both are passed in via
    `__init__` and consulted in `clean_coupon_code`.
    """

    # Rendered hidden: the visible coupon input lives in a separate "Apply
    # coupon" form in the checkout sidebar (see cart/views.py checkout and
    # templates/cart/checkout.html) so a shopper can preview the discount
    # before filling in shipping details. This field just carries whatever
    # code was resolved there through to the real order submission, where
    # it's re-validated regardless (never trust a value from an initial=).
    coupon_code = forms.CharField(required=False, widget=forms.HiddenInput())

    # We only ship within India, so the country isn't a real choice — shown
    # read-only rather than editable. `disabled=True` also makes Django
    # ignore any posted value and always use this initial, so it can't be
    # tampered with via the request.
    shipping_country = forms.CharField(initial="India", disabled=True)
    shipping_state = forms.ChoiceField(choices=INDIAN_STATE_CHOICES)

    # A buyer with saved addresses picks one of these instead of retyping
    # the manual fields above; picking "Enter a new address" (the un-set,
    # empty-string state of this field) leaves it None and the manual
    # fields apply instead — see clean(). Rendered by hand in the
    # checkout template (address cards, not a bare <select>), so excluded
    # from the crispy Layout below like coupon_code's own hand-rendered
    # counterpart.
    saved_address = forms.ModelChoiceField(queryset=Address.objects.none(), required=False, empty_label=None)
    save_address = forms.BooleanField(required=False, initial=True, label="Save this address for next time")

    # Required only when no saved_address is chosen — see clean(). Kept
    # required=True on the model/at the DB level; a stale/removed
    # requirement here would silently let an order save with blank
    # shipping fields when neither path fills cleaned_data.
    _CONDITIONALLY_REQUIRED_FIELDS = [
        "shipping_full_name",
        "shipping_phone_number",
        "shipping_address_line1",
        "shipping_city",
        "shipping_state",
        "shipping_postal_code",
    ]

    class Meta:
        model = Order
        fields = [
            "shipping_full_name",
            "shipping_phone_number",
            "shipping_address_line1",
            "shipping_address_line2",
            "shipping_city",
            "shipping_state",
            "shipping_postal_code",
            "shipping_country",
            "coupon_code",
            "customer_note",
        ]
        widgets = {
            "customer_note": forms.Textarea(attrs={"rows": 3}),
        }
        labels = {
            "customer_note": "Delivery notes (optional)",
        }

    def __init__(self, *args, user=None, subtotal=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.subtotal = subtotal
        self.coupon = None
        self.fields["saved_address"].queryset = user.addresses.all() if user is not None else Address.objects.none()
        for field_name in self._CONDITIONALLY_REQUIRED_FIELDS:
            self.fields[field_name].required = False
        self.helper = FormHelper()
        self.helper.form_tag = False
        self.helper.label_class = "block text-gray-700 text-sm font-semibold mb-1"
        self.helper.field_class = "mb-2"
        self.helper.layout = Layout(
            "shipping_full_name",
            "shipping_phone_number",
            "shipping_address_line1",
            "shipping_address_line2",
            "shipping_city",
            "shipping_state",
            "shipping_postal_code",
            "shipping_country",
            "customer_note",
            "coupon_code",
        )

    def clean(self):
        cleaned_data = super().clean()
        saved_address = cleaned_data.get("saved_address")

        if saved_address:
            if not is_pincode_serviceable(saved_address.postal_code):
                self.add_error("saved_address", pincode_block_reason(saved_address.postal_code))
            cleaned_data["shipping_full_name"] = saved_address.recipient_name
            cleaned_data["shipping_phone_number"] = saved_address.phone_number
            cleaned_data["shipping_address_line1"] = saved_address.address_line1
            cleaned_data["shipping_address_line2"] = saved_address.address_line2
            cleaned_data["shipping_city"] = saved_address.city
            cleaned_data["shipping_state"] = saved_address.state
            cleaned_data["shipping_postal_code"] = saved_address.postal_code
            cleaned_data["shipping_country"] = saved_address.country
        else:
            field_labels = {
                "shipping_full_name": "Full name",
                "shipping_phone_number": "Phone number",
                "shipping_address_line1": "Address line 1",
                "shipping_city": "City",
                "shipping_state": "State",
                "shipping_postal_code": "Postal code",
            }
            for field_name, label in field_labels.items():
                if not cleaned_data.get(field_name):
                    self.add_error(field_name, f"{label} is required.")

        return cleaned_data

    def clean_coupon_code(self):
        code = self.cleaned_data["coupon_code"].strip().upper()
        if not code:
            return code

        try:
            coupon = Coupon.objects.get(code=code)
        except Coupon.DoesNotExist:
            raise forms.ValidationError("That coupon code isn't valid.")

        error = coupon.validate_for(self.user, self.subtotal)
        if error:
            raise forms.ValidationError(error)

        self.coupon = coupon
        return code

    def clean_shipping_postal_code(self):
        postal_code = self.cleaned_data["shipping_postal_code"].strip()
        # A saved address is validated separately, in clean() — this
        # per-field clean runs before `saved_address` is resolved (it's
        # not in Meta.fields, so it's cleaned last), and leftover text in
        # the now-hidden manual field shouldn't block a saved-address
        # checkout. `self.data` (raw POST) is checked rather than
        # cleaned_data for exactly that ordering reason.
        if self.data.get("saved_address"):
            return postal_code
        if postal_code and not is_pincode_serviceable(postal_code):
            raise forms.ValidationError(pincode_block_reason(postal_code))
        return postal_code
