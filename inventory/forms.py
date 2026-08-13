"""Staff-facing forms used by the inventory admin (manual stock adjustment)."""

from django import forms


class StockAdjustmentForm(forms.Form):
    delta = forms.IntegerField(
        help_text="Positive to add stock, negative to remove it (e.g. damage write-off). Can't be zero.",
    )
    note = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        required=False,
        help_text="Why — a stocktake correction, damaged units, etc.",
    )

    def clean_delta(self):
        delta = self.cleaned_data["delta"]
        if delta == 0:
            raise forms.ValidationError("Adjustment can't be zero.")
        return delta
