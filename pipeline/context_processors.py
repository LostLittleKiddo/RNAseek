def session_context(request):
    """Inject session_id into every template context."""
    session_obj = getattr(request, "session_obj", None)
    return {
        "session_id": str(session_obj.session_id) if session_obj else "",
    }
