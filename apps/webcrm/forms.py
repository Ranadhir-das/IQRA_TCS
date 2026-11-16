"""Forms for the IQRA TCS Admin Web CRM interface."""

from django import forms

from apps.accounts.models import User
from apps.leads.models import Lead, StagedLeadImport


class AdminWebLoginForm(forms.Form):
    """Validate admin web login credentials without leaking account existence."""

    GENERIC_AUTH_ERROR = "Invalid administrator username or password."

    username = forms.CharField(
        max_length=150,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter admin username",
                "autocomplete": "username",
                "autofocus": True,
            }
        ),
    )
    password = forms.CharField(
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "Enter password",
                "autocomplete": "current-password",
            }
        ),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.authenticated_user = None

    def clean(self):
        cleaned_data = super().clean()
        username = (cleaned_data.get("username") or "").strip()
        password = cleaned_data.get("password") or ""
        if not username or not password:
            return cleaned_data

        try:
            user = User.objects.get(username__iexact=username)
        except User.DoesNotExist:
            # Perform equivalent password-hashing work to mitigate username timing side-channels
            User().set_password(password)
            raise forms.ValidationError(self.GENERIC_AUTH_ERROR) from None

        if not user.check_password(password):
            raise forms.ValidationError(self.GENERIC_AUTH_ERROR)

        if not user.is_active or not user.is_admin:
            raise forms.ValidationError(self.GENERIC_AUTH_ERROR)

        self.authenticated_user = user
        return cleaned_data


class LeadForm(forms.Form):
    """Form for creating and editing leads via AdminLeadSerializer."""

    name = forms.CharField(
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    phone = forms.CharField(
        max_length=32,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    alternate_phone = forms.CharField(
        max_length=32,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    email = forms.EmailField(
        max_length=254,
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control"}),
    )
    course_interest = forms.CharField(
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    enquiry_details = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3}),
    )
    status = forms.ChoiceField(
        choices=Lead.Status.choices,
        initial=Lead.Status.NEW,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    assigned_to = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="— Unassigned —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    assignment_reason = forms.CharField(
        max_length=1000,
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Optional reason when changing assignment",
            }
        ),
    )
    internal_notes = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 3,
                "placeholder": "Admin-only internal notes (never visible to employees)",
            }
        ),
    )
    is_archived = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    def __init__(self, *args, is_edit: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        if is_edit:
            self.fields.pop("assigned_to", None)
            self.fields.pop("assignment_reason", None)
        else:
            self.fields["assigned_to"].queryset = User.objects.filter(
                role=User.Role.EMPLOYEE,
                is_active=True,
                is_superuser=False,
            ).order_by("username")


class LeadImportUploadForm(forms.Form):
    """Upload form for staged CSV/XLSX lead import preview."""

    file = forms.FileField(
        widget=forms.ClearableFileInput(
            attrs={"class": "form-control", "accept": ".csv,.xlsx"}
        )
    )
    duplicate_policy = forms.ChoiceField(
        choices=StagedLeadImport.DuplicatePolicy.choices,
        initial=StagedLeadImport.DuplicatePolicy.SKIP,
        widget=forms.Select(attrs={"class": "form-select"}),
    )
