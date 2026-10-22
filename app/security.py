import secrets

from fastapi import Header, HTTPException, Request


def check_token(
    expected: str,
    actual: str | None
) -> None:

    if not actual or not secrets.compare_digest(
        expected, actual
    ):
        raise HTTPException(
            status_code=401,
            detail="Unauthorized"
        )


def admin_guard(
    request: Request,
    x_admin_token: str | None = Header(default=None)
):
    check_token(
        request.app.state.settings.admin_token,
        x_admin_token
    )


def event_guard(
    request: Request,
    x_event_token: str | None = Header(default=None)
):
    check_token(
        request.app.state.settings.event_token,
        x_event_token
    )