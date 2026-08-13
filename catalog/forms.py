from django import forms

from .models import Review


class ReviewForm(forms.ModelForm):
    class Meta:
        model = Review
        fields = ["rating", "title", "comment"]
        widgets = {
            "rating": forms.RadioSelect(choices=[(i, i) for i in range(5, 0, -1)]),
            "title": forms.TextInput(attrs={"placeholder": "Sum up your experience"}),
            "comment": forms.Textarea(attrs={"rows": 4, "placeholder": "What did you like or dislike?"}),
        }
