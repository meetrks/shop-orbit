"""Staff-facing forms used by the payments admin (refund initiation)."""

from decimal import Decimal

from django import forms


class RefundForm(forms.Form):
    amount = forms.DecimalField(
        max_digits=10,
        decimal_places=2,
        min_value=Decimal("0.01"),
        help_text="Full or partial amount to refund, in rupees.",
    )
    reason = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}), required=False)
