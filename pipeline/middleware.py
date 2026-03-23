import uuid

from django.utils import timezone

from pipeline.models import Session

SESSION_COOKIE_NAME = "Session_ID"
SESSION_COOKIE_MAX_AGE = 14 * 24 * 60 * 60  # 14 days in seconds


class AnonymousSessionMiddleware:
    """Assign every visitor a persistent anonymous Session via an HttpOnly cookie.

    On each request:
    1. Read the Session_ID cookie.
    2. If present and valid (exists in DB + not expired), attach `request.session_obj`.
    3. Otherwise, create a new Session row and set the cookie on the response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        session_obj = None
        raw_id = request.COOKIES.get(SESSION_COOKIE_NAME)

        if raw_id:
            try:
                session_obj = Session.objects.get(
                    session_id=uuid.UUID(raw_id),
                    expires_at__gt=timezone.now(),
                )
            except (Session.DoesNotExist, ValueError):
                session_obj = None

        needs_cookie = session_obj is None
        if needs_cookie:
            session_obj = Session.objects.create()

        request.session_obj = session_obj

        response = self.get_response(request)

        if needs_cookie:
            response.set_cookie(
                SESSION_COOKIE_NAME,
                str(session_obj.session_id),
                max_age=SESSION_COOKIE_MAX_AGE,
                httponly=True,
                samesite="Lax",
            )

        return response
