from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("access", "0001_initial")]
    operations = [
        migrations.AddField(
            model_name="faceprofile",
            name="photo",
            field=models.BinaryField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="faceprofile",
            name="photo_taken_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="verification",
            name="captured_image",
            field=models.BinaryField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="verification",
            name="registered_image",
            field=models.BinaryField(null=True, blank=True, editable=False),
        ),
        migrations.AddField(
            model_name="verification",
            name="captured_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.AddField(
            model_name="verification",
            name="registered_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
