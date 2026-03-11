import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("pipeline", "0004_analysissubmission_input_data_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisjob",
            name="step_progress",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="analysisjob",
            name="created_at",
            field=models.DateTimeField(
                auto_now_add=True,
                default=django.utils.timezone.now,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="analysisjob",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
