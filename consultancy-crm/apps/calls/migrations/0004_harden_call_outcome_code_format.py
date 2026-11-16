import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("calls", "0003_calloutcome_code_immutable")]

    operations = [
        migrations.AlterField(
            model_name="calloutcome",
            name="code",
            field=models.CharField(
                max_length=50,
                unique=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="Use 1–50 uppercase letters, digits, or underscores; start with a letter.",
                        regex=r"\A[A-Z][A-Z0-9_]{0,49}\Z",
                    ),
                ],
            ),
        ),
        migrations.RemoveConstraint(
            model_name="calloutcome",
            name="call_outcome_code_format",
        ),
        migrations.AddConstraint(
            model_name="calloutcome",
            constraint=models.CheckConstraint(
                condition=models.Q(("code__regex", r"\A[A-Z][A-Z0-9_]{0,49}\Z")),
                name="call_outcome_code_format",
            ),
        ),
    ]
