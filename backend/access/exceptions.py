from django.db import IntegrityError
from rest_framework.response import Response
from rest_framework.views import exception_handler


def handle(exc, context):
    if isinstance(exc, IntegrityError):
        return Response(
            {
                "detail": "This record conflicts with an existing assignment. Refresh and try again."
            },
            status=409,
        )
    return exception_handler(exc, context)
