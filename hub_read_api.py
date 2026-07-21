import datetime
import os
from collections import Counter
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Header, HTTPException, Response, status
from supabase import create_client


router = APIRouter(
    prefix="/api/hub/v1/posta",
    tags=["ECCOMI HUB - sola lettura"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()

hub_supabase = (
    create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    if SUPABASE_URL and SUPABASE_SERVICE_KEY
    else None
)

ROW_LIMIT = 1000
RECENT_LIMIT = 12
ROME_TZ = ZoneInfo("Europe/Rome")

EXCLUDED_STATES = {"BOZZA_CHECKOUT", "NON_PAGATO"}
COMPLETED_STATES = {"COMPLETATO"}
SENT_STATES = {
    "INVIATO_POSTE",
    "PRESA_IN_CARICO_POSTEL",
    "CONSEGNATO",
    "COMPLETATO",
}
MANUAL_STATES = {"LAVORAZIONE_MANUALE", "RICEVUTO_MANUALE"}
POSTA_SCOPES = {"posta", "eccomi-posta", "eccomi_posta"}


def _text(value) -> str:
    return str(value or "").strip()


def _upper(value) -> str:
    return _text(value).upper()


def _user_id(user) -> str:
    if user is None:
        return ""
    if isinstance(user, dict):
        return _text(user.get("id"))
    return _text(getattr(user, "id", ""))


def _parse_datetime(value):
    raw = _text(value)
    if not raw:
        return None

    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)

    return parsed


def _is_error(state_value) -> bool:
    state_name = _upper(state_value)

    return (
        state_name.startswith("ERRORE_")
        or state_name == "INDIRIZZO_DA_VERIFICARE"
    )


def _require_hub_user(authorization: str | None) -> dict:
    if hub_supabase is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Collegamento ECCOMI HUB non configurato.",
        )

    prefix = "Bearer "

    if not authorization or not authorization.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Accesso ECCOMI HUB richiesto.",
        )

    access_token = authorization[len(prefix):].strip()

    if not access_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione ECCOMI HUB non valida.",
        )

    try:
        auth_result = hub_supabase.auth.get_user(access_token)
        user_id = _user_id(getattr(auth_result, "user", None))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione ECCOMI HUB scaduta o non valida.",
        )

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sessione ECCOMI HUB non valida.",
        )

    try:
        result = (
            hub_supabase
            .table("hub_profiles")
            .select("user_id,role,active,ecosystem_keys")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Non è stato possibile verificare il ruolo ECCOMI HUB.",
        )

    profiles = result.data or []

    if not profiles or profiles[0].get("active") is not True:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Profilo ECCOMI HUB non abilitato.",
        )

    profile = profiles[0]
    role = _text(profile.get("role")).lower()

    scopes = {
        _text(item).lower()
        for item in (profile.get("ecosystem_keys") or [])
        if _text(item)
    }

    if role == "ceo":
        return profile

    if role == "manager" and scopes.intersection(POSTA_SCOPES):
        return profile

    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Il ruolo non è autorizzato ai dati di Eccomi Posta.",
    )


@router.get("/summary")
def get_posta_summary(
    response: Response,
    authorization: str | None = Header(default=None),
):
    _require_hub_user(authorization)

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"

    try:
        result = (
            hub_supabase
            .table("pratiche")
            .select(
                "id,order_name,shopify_order_name,tipo_servizio,stato,"
                "ultimo_evento,created_at,updated_at"
            )
            .order("updated_at", desc=True)
            .limit(ROW_LIMIT)
            .execute()
        )
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="I dati di Eccomi Posta non sono momentaneamente disponibili.",
        )

    fetched_rows = result.data or []

    rows = [
        row
        for row in fetched_rows
        if _upper(row.get("stato")) not in EXCLUDED_STATES
    ]

    today_rome = datetime.datetime.now(ROME_TZ).date()
    by_service = Counter()

    completed = 0
    sent = 0
    errors = 0
    manual = 0
    created_today = 0
    recent = []

    for row in rows:
        state_name = _upper(row.get("stato")) or "DA_VERIFICARE"
        service_name = _upper(row.get("tipo_servizio")) or "ALTRO"

        by_service[service_name] += 1

        if state_name in COMPLETED_STATES:
            completed += 1

        if state_name in SENT_STATES:
            sent += 1

        if _is_error(state_name):
            errors += 1

        if state_name in MANUAL_STATES:
            manual += 1

        created_at = _parse_datetime(row.get("created_at"))

        if created_at and created_at.astimezone(ROME_TZ).date() == today_rome:
            created_today += 1

        if len(recent) < RECENT_LIMIT:
            practice_id = _text(row.get("id"))

            order_name = (
                _text(row.get("shopify_order_name"))
                or _text(row.get("order_name"))
                or (
                    f"Pratica {practice_id[:8]}"
                    if practice_id
                    else "Pratica"
                )
            )

            recent.append({
                "id": practice_id,
                "order_name": order_name,
                "service": service_name,
                "status": state_name,
                "last_event": (
                    _text(row.get("ultimo_evento"))
                    or state_name
                ),
                "created_at": _text(row.get("created_at")),
                "updated_at": _text(row.get("updated_at")),
            })

    total = len(rows)

    return {
        "source": "eccomi-posta-backend",
        "safe_read_only": True,
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "sample_limited": len(fetched_rows) >= ROW_LIMIT,
        "summary": {
            "total": total,
            "open": max(total - completed, 0),
            "completed": completed,
            "sent": sent,
            "errors": errors,
            "manual": manual,
            "created_today": created_today,
        },
        "by_service": dict(sorted(by_service.items())),
        "recent": recent,
    }
