import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from supabase import create_client
from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client, Plugin, xsd
from zeep.plugins import HistoryPlugin
from zeep.transports import Transport
from zeep.wsa import WsAddressingPlugin
from zeep.xsd import AnySimpleType
from lxml import etree
from urllib.parse import urljoin
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO
from pypdf import PdfReader
import datetime
import os
import hashlib
import base64
import requests
import uuid
import inspect
import json
import time
import re
from zeep.helpers import serialize_object
from pydantic import BaseModel
from typing import Optional

app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "eccomi-posta")

POSTE_H2H_USERID = os.getenv("POSTE_H2H_USERID")
POSTE_H2H_PASSWORD = os.getenv("POSTE_H2H_PASSWORD")
POSTE_H2H_CONTRACT_ID = os.getenv("POSTE_H2H_CONTRACT_ID")
POSTE_INVIO_MODE = os.getenv("POSTE_INVIO_MODE", "manual").strip().lower()
POSTE_INVIO_AUTO = POSTE_INVIO_MODE in ["auto", "automatico", "on", "true", "1"]
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_EMAIL = os.getenv(
    "FROM_EMAIL",
    "Eccomi Posta <info@eccomionline.com>"
).strip()

INTERNAL_BCC_EMAIL = os.getenv(
    "INTERNAL_BCC_EMAIL",
    "sales@eccomionline.com"
)

EMAIL_RACCOMANDATA_ENABLED = os.getenv(
    "EMAIL_RACCOMANDATA_ENABLED",
    "false"
).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

ECCOMI_POSTA_CTA_URL = os.getenv(
    "ECCOMI_POSTA_CTA_URL",
    "https://www.eccomionline.com/pages/eccomi-posta"
).strip()

def bool_from_any(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    text = str(value).strip().lower()

    return text in [
        "true",
        "1",
        "yes",
        "si",
        "sì",
        "on",
        "rr",
        "+1€",
        "+1",
        "ricevuta di ritorno",
        "ricevuta ritorno"
    ]


def detect_ricevuta_ritorno(props: dict):
    text = " ".join([
        str(props.get("Ricevuta di ritorno", "")),
        str(props.get("Ricevuta ritorno", "")),
        str(props.get("RR", "")),
        str(props.get("_ricevuta_ritorno", "")),
        str(props.get("ricevuta_ritorno", ""))
    ]).lower()

    return (
        "sì" in text
        or "si" in text
        or "+1" in text
        or "true" in text
        or "ricevuta" in text
        or "ritorno" in text
    )


def get_ricevuta_ritorno_from_order(ordine: dict):
    if bool_from_any(ordine.get("ricevuta_ritorno")):
        return True

    try:
        pdf_url = ordine.get("pdf_url")

        if not pdf_url:
            return False

        pratica_rr = supabase.table("pratiche") \
            .select("ricevuta_ritorno") \
            .eq("pdf_url", pdf_url) \
            .limit(1) \
            .execute()

        if pratica_rr.data:
            return bool_from_any(pratica_rr.data[0].get("ricevuta_ritorno"))

    except Exception as e:
        print("ERRORE LETTURA RR:", str(e))

    return False

POSTE_H2H_ROL_WSDL = os.getenv(
    "POSTE_H2H_ROL_WSDL",
    "https://cewebservices.posteitaliane.it/ROLGC/RolService.WSDL"
)

POSTE_H2H_SERVICE_URL = "https://cewebservices.posteitaliane.it/ROLGC/RolService.svc"
POSTE_H2H_BINDING = "{http://ComunicazioniElettroniche.ROL.WS}BasicHttpBinding_ROLServiceSoap"

# ---------------------------------------------------------
# POSTE H2H RACCOMANDATA - AMBIENTE TEST SEPARATO
# ---------------------------------------------------------

POSTE_H2H_ROL_WSDL_TEST = os.getenv(
    "POSTE_H2H_ROL_WSDL_TEST",
    ""
).strip()

POSTE_H2H_SERVICE_URL_TEST = os.getenv(
    "POSTE_H2H_SERVICE_URL_TEST",
    ""
).strip()

POSTE_H2H_USERID_TEST = os.getenv(
    "POSTE_H2H_USERID_TEST",
    ""
).strip()

POSTE_H2H_PASSWORD_TEST = os.getenv(
    "POSTE_H2H_PASSWORD_TEST",
    ""
).strip()

POSTE_H2H_CONTRACT_ID_TEST = os.getenv(
    "POSTE_H2H_CONTRACT_ID_TEST",
    ""
).strip()

# ---------------------------------------------------------
# POSTE H2H TELEGRAMMA - TOL
# ---------------------------------------------------------

POSTE_H2H_TOL_SERVICE_URL = os.getenv(
    "POSTE_H2H_TOL_SERVICE_URL",
    "https://cewebservices.posteitaliane.it/TelegrammaExtranet/WsTOL.svc"
).strip()

POSTE_H2H_TOL_WSDL = os.getenv(
    "POSTE_H2H_TOL_WSDL",
    ""
).strip()

if not POSTE_H2H_TOL_WSDL:
    if POSTE_H2H_TOL_SERVICE_URL.endswith("?wsdl"):
        POSTE_H2H_TOL_WSDL = POSTE_H2H_TOL_SERVICE_URL
    else:
        POSTE_H2H_TOL_WSDL = POSTE_H2H_TOL_SERVICE_URL.rstrip("/") + "?wsdl"

POSTE_H2H_TOL_USERID = os.getenv(
    "POSTE_H2H_TOL_USERID",
    POSTE_H2H_USERID
)

POSTE_H2H_TOL_PASSWORD = os.getenv(
    "POSTE_H2H_TOL_PASSWORD",
    POSTE_H2H_PASSWORD
)

POSTE_H2H_TOL_CONTRACT_ID = os.getenv(
    "POSTE_H2H_TOL_CONTRACT_ID",
    POSTE_H2H_CONTRACT_ID
)

POSTE_H2H_TOL_CUSTOMER = os.getenv(
    "POSTE_H2H_TOL_CUSTOMER",
    POSTE_H2H_TOL_USERID
)

# ============================================================
# REPORTING
# ============================================================

POSTE_H2H_REPORTING_WSDL = os.getenv(
    "POSTE_H2H_REPORTING_WSDL",
    "https://sptest.posteitaliane.it/Reporting/Reports.svc?wsdl"
)

POSTE_H2H_REPORTING_SERVICE_URL = os.getenv(
    "POSTE_H2H_REPORTING_SERVICE_URL",
    "https://sptest.posteitaliane.it/Reporting/Reports.svc"
)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# ============================================================
# SICUREZZA H2H - BLOCCO ENDPOINT DI TEST IN PRODUZIONE
# ============================================================

def h2h_debug_enabled():
    return os.getenv(
        "POSTE_DEBUG_H2H_ENABLED",
        "false"
    ).strip().lower() in [
        "true",
        "1",
        "yes",
        "si",
        "sì",
        "on"
    ]


def require_h2h_debug_enabled():
    if not h2h_debug_enabled():
        raise HTTPException(
            status_code=403,
            detail="Endpoint H2H di test disattivato in produzione"
        )

# ============================================================
# RUBRICA POSTA - MITTENTI / DESTINATARI SALVATI
# ============================================================

class RubricaPostaPayload(BaseModel):
    shopify_customer_id: Optional[str] = ""
    customer_email: Optional[str] = ""
    tipo: str
    nome: str
    via: str
    civico: Optional[str] = ""
    cap: Optional[str] = ""
    comune: Optional[str] = ""
    provincia: Optional[str] = ""


def clean_text(value):
    return str(value or "").strip()


def clean_email(value):
    return str(value or "").strip().lower()


def clean_tipo_rubrica(value):
    tipo = str(value or "").strip().lower()

    if tipo not in ["mittente", "destinatario"]:
        raise HTTPException(
            status_code=400,
            detail="Tipo non valido. Usa mittente oppure destinatario."
        )

    return tipo


@app.get("/rubrica-posta")
async def rubrica_posta(email: str = "", customer_id: str = ""):
    try:
        email = (email or "").strip().lower()
        customer_id = (customer_id or "").strip()

        print("RUBRICA POSTA REQUEST:", {
            "email": email,
            "customer_id": customer_id
        })

        if not email and not customer_id:
            return {
                "success": True,
                "count": 0,
                "items": [],
                "message": "Email o customer_id assenti"
            }

        query = (
            supabase
            .table("rubrica_posta")
            .select("*")
            .order("created_at", desc=True)
            .limit(200)
            .execute()
        )

        rows = query.data or []

        items = []

        for row in rows:
            row_email = str(row.get("email") or "").strip().lower()
            row_customer_id = str(row.get("customer_id") or "").strip()

            if email and row_email == email:
                items.append(row)
                continue

            if customer_id and row_customer_id == customer_id:
                items.append(row)
                continue

        return {
            "success": True,
            "email": email,
            "customer_id": customer_id,
            "total_rows_checked": len(rows),
            "count": len(items),
            "items": items
        }

    except Exception as e:
        print("ERRORE RUBRICA POSTA:", str(e))

        return {
            "success": False,
            "error": str(e),
            "items": []
        }


@app.post("/rubrica-posta")
def salva_rubrica_posta(payload: RubricaPostaPayload):
    customer_id = clean_text(payload.shopify_customer_id)
    email = clean_email(payload.customer_email)
    tipo = clean_tipo_rubrica(payload.tipo)

    nome = clean_text(payload.nome)
    via = clean_text(payload.via)
    civico = clean_text(payload.civico)
    cap = clean_text(payload.cap)
    comune = clean_text(payload.comune)
    provincia = clean_text(payload.provincia).upper()[:2]

    if not customer_id and not email:
        raise HTTPException(
            status_code=400,
            detail="Cliente non identificato."
        )

    if not nome or not via or not cap or not comune or not provincia:
        raise HTTPException(
            status_code=400,
            detail="Dati rubrica incompleti."
        )

    data = {
        "shopify_customer_id": customer_id,
        "customer_email": email,
        "tipo": tipo,
        "nome": nome,
        "via": via,
        "civico": civico,
        "cap": cap,
        "comune": comune,
        "provincia": provincia
    }

    result = supabase.table("rubrica_posta").insert(data).execute()

    return {
        "success": True,
        "message": "Contatto salvato in rubrica.",
        "item": result.data[0] if result.data else data
    }


@app.delete("/rubrica-posta/{rubrica_id}")
def elimina_rubrica_posta(
    rubrica_id: str,
    customer_id: str = "",
    email: str = ""
):
    customer_id = clean_text(customer_id)
    email = clean_email(email)

    if not customer_id and not email:
        raise HTTPException(
            status_code=400,
            detail="Cliente non identificato."
        )

    query = supabase.table("rubrica_posta").delete().eq("id", rubrica_id)

    if customer_id:
        query = query.eq("shopify_customer_id", customer_id)
    else:
        query = query.eq("customer_email", email)

    result = query.execute()

    return {
        "success": True,
        "message": "Contatto eliminato dalla rubrica.",
        "deleted": result.data or []
    }

def salva_poste_h2h_order(data: dict):
    try:
        res = supabase.table("poste_h2h_orders").insert(data).execute()
        return res.data
    except Exception as e:
        print("ERRORE SALVATAGGIO SUPABASE:", str(e))
        return None
def genera_pdf_cliente_eccomi_posta(
    numero_raccomandata,
    mittente,
    destinatario,
    stato="Accettata da Poste Italiane"
):
    buffer = BytesIO()

    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # HEADER
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, height - 70, "ECCOMI POSTA")

    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, height - 92, "Servizi Postali Digitali")

    c.setLineWidth(1)
    c.line(50, height - 115, width - 50, height - 115)

    # TITOLO
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, height - 155, "Ricevuta di spedizione")

    # BOX TRACKING
    c.roundRect(50, height - 250, width - 100, 75, 8, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(70, height - 200, "Numero Raccomandata")

    c.setFont("Helvetica-Bold", 18)
    c.drawString(70, height - 225, str(numero_raccomandata))

    c.setFont("Helvetica-Bold", 12)
    c.drawString(330, height - 200, "Stato")

    c.setFont("Helvetica", 12)
    c.drawString(330, height - 225, stato)

    # DATI MITTENTE
    y = height - 300

    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, "Mittente")
    y -= 22

    c.setFont("Helvetica", 11)
    for line in str(mittente).split(" - "):
        c.drawString(50, y, line)
        y -= 16

    # DATI DESTINATARIO
    y -= 20
    c.setFont("Helvetica-Bold", 13)
    c.drawString(50, y, "Destinatario")
    y -= 22

    c.setFont("Helvetica", 11)
    for line in str(destinatario).split(" - "):
        c.drawString(50, y, line)
        y -= 16

    # DATA
    y -= 20
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, y, "Data operazione")

    c.setFont("Helvetica", 11)
    c.drawString(
        160,
        y,
        datetime.datetime.now().strftime("%d/%m/%Y %H:%M")
    )

    # ALTRI SERVIZI
    y -= 60

    c.setFont("Helvetica-Bold", 13)
    c.drawString(
        50,
        y,
        "Scopri anche gli altri servizi Eccomi Posta"
    )

    y -= 22

    c.setFont("Helvetica", 10)

    servizi = [
        "Telegramma Online",
        "Raccomandata con ricevuta di ritorno",
        "Visure e certificati",
        "Spedizione buste e pacchi",
        "Servizi postali per aziende"
    ]

    for servizio in servizi:
        c.drawString(65, y, f"• {servizio}")
        y -= 15

    # FOOTER
    c.line(50, 90, width - 50, 90)

    c.setFont("Helvetica", 9)

    c.drawString(
        50,
        70,
        "Eccomi Posta è un servizio digitale di gestione spedizioni."
    )

    c.drawString(
        50,
        55,
        "La presente ricevuta riepiloga la presa in carico della pratica."
    )

    c.drawString(
        50,
        40,
        "www.eccomionline.com"
    )

    c.save()

    pdf = buffer.getvalue()
    buffer.close()

    return pdf

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ForcePosteAddressPlugin(Plugin):
    def egress(self, envelope, http_headers, operation, binding_options):
        fix_wsa_to(envelope)
        return envelope, http_headers


def fix_wsa_to(envelope):
    for el in envelope.xpath("//*[local-name()='To']"):
        el.text = POSTE_H2H_SERVICE_URL
    return envelope


class ForceTelegrammaAddressPlugin(Plugin):
    def egress(self, envelope, http_headers, operation, binding_options):
        fix_telegramma_wsa_to(envelope)
        return envelope, http_headers


def fix_telegramma_wsa_to(envelope):
    for el in envelope.xpath("//*[local-name()='To']"):
        el.text = POSTE_H2H_TOL_SERVICE_URL
    return envelope


def poste_client(timeout=60, extra_plugins=None):
    session = Session()
    session.auth = HTTPBasicAuth(POSTE_H2H_USERID, POSTE_H2H_PASSWORD)
    session.verify = False

    transport = Transport(session=session, timeout=timeout)

    plugins = [
        ForcePosteAddressPlugin()
    ]

    if extra_plugins:
        plugins.extend(extra_plugins)

    client = Client(
        wsdl=POSTE_H2H_ROL_WSDL,
        transport=transport,
        plugins=plugins
    )

    service = client.create_service(
        POSTE_H2H_BINDING,
        POSTE_H2H_SERVICE_URL
    )

    service._binding_options["address"] = POSTE_H2H_SERVICE_URL

    return client, service

class ForcePosteAddressPluginTest(Plugin):
    def egress(self, envelope, http_headers, operation, binding_options):
        for el in envelope.xpath("//*[local-name()='To']"):
            el.text = POSTE_H2H_SERVICE_URL_TEST

        return envelope, http_headers


def poste_client_test(timeout=60, extra_plugins=None):
    """
    Client Poste H2H Raccomandata in ambiente TEST.
    Usa SOLO variabili *_TEST.
    NON tocca produzione.
    """

    if not POSTE_H2H_ROL_WSDL_TEST:
        raise RuntimeError("POSTE_H2H_ROL_WSDL_TEST mancante")

    if not POSTE_H2H_SERVICE_URL_TEST:
        raise RuntimeError("POSTE_H2H_SERVICE_URL_TEST mancante")

    if not POSTE_H2H_USERID_TEST:
        raise RuntimeError("POSTE_H2H_USERID_TEST mancante")

    if not POSTE_H2H_PASSWORD_TEST:
        raise RuntimeError("POSTE_H2H_PASSWORD_TEST mancante")

    if not POSTE_H2H_CONTRACT_ID_TEST:
        raise RuntimeError("POSTE_H2H_CONTRACT_ID_TEST mancante")

    session = Session()
    session.auth = HTTPBasicAuth(
        POSTE_H2H_USERID_TEST,
        POSTE_H2H_PASSWORD_TEST
    )
    session.verify = False

    transport = Transport(session=session, timeout=timeout)

    plugins = [
        ForcePosteAddressPluginTest()
    ]

    if extra_plugins:
        plugins.extend(extra_plugins)

    client = Client(
        wsdl=POSTE_H2H_ROL_WSDL_TEST,
        transport=transport,
        plugins=plugins
    )

    service = client.create_service(
        POSTE_H2H_BINDING,
        POSTE_H2H_SERVICE_URL_TEST
    )

    service._binding_options["address"] = POSTE_H2H_SERVICE_URL_TEST

    return client, service

def telegramma_client(timeout=60, extra_plugins=None):
    session = Session()
    session.auth = HTTPBasicAuth(
    POSTE_H2H_TOL_USERID,
    POSTE_H2H_TOL_PASSWORD
)
    session.verify = False

    transport = Transport(session=session, timeout=timeout)

    plugins = [
        ForceTelegrammaAddressPlugin()
    ]

    if extra_plugins:
        plugins.extend(extra_plugins)

    client = Client(
        wsdl=POSTE_H2H_TOL_WSDL,
        transport=transport,
        plugins=plugins
    )

    return client

def telegramma_service(timeout=60, extra_plugins=None):
    """
    Crea il service Telegramma H2H forzando l'endpoint corretto.
    Serve per chiamate reali tipo Preventivo, Submit, PreConfirm, Confirm.
    """

    client = telegramma_client(
        timeout=timeout,
        extra_plugins=extra_plugins
    )

    for srv in client.wsdl.services.values():
        for port in srv.ports.values():
            binding_name = port.binding.name

            service = client.create_service(
                binding_name,
                POSTE_H2H_TOL_SERVICE_URL
            )

            try:
                service._binding_options["address"] = POSTE_H2H_TOL_SERVICE_URL
            except Exception:
                pass

            return client, service

    raise RuntimeError("Nessun binding Telegramma trovato nel WSDL")


@app.get("/")
def home():
    return {
        "status": "Eccomi Posta Backend OK 🚀",
        "version": "EMAIL_DEBUG_V2_ATTIVO",
        "debug_email": "/debug/email-function"
    }


@app.get("/debug")
@app.get("/debug/")
def debug_index():
    return {
        "success": True,
        "version": "EMAIL_DEBUG_V2_ATTIVO",
        "available_endpoints": [
            "/debug/email-function",
            "/poste/debug/email-function",
            "/poste/h2h/debug-email-function"
        ]
    }

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "eccomi-posta-backend",
        "version": "posta-monitoring-ready"
    }


@app.get("/api/health")
def api_health():
    return {
        "status": "ok",
        "service": "eccomi-posta-backend",
        "version": "posta-monitoring-ready"
    }


@app.get("/debug/email-function")
@app.get("/poste/debug/email-function")
@app.get("/poste/h2h/debug-email-function")
def debug_email_function():
    """
    Verifica configurazione email Raccomandata.
    Non invia email.
    Non chiama Poste.
    """

    fn = globals().get("invia_email_cliente_raccomandata")

    return {
        "success": True,
        "version": "EMAIL_DEBUG_V2_ATTIVO",
        "function_defined": callable(fn),
        "resend_api_key_present": bool(os.getenv("RESEND_API_KEY")),
        "from_email": os.getenv("FROM_EMAIL", ""),
        "email_enabled": os.getenv("EMAIL_RACCOMANDATA_ENABLED", ""),
        "cta_url": os.getenv("ECCOMI_POSTA_CTA_URL", "")
    }

@app.get("/debug/send-email-test")
@app.get("/poste/debug/send-email-test")
def debug_send_email_test(to: str = ""):
    """
    Invia una email di test Eccomi Posta senza chiamare Poste.
    NON invia raccomandate.
    NON genera costi H2H.
    Serve solo per testare Resend + template email cliente.
    """

    to = str(to or "").strip().lower()

    if not to:
        return {
            "success": False,
            "error": "Parametro email mancante. Usa: /debug/send-email-test?to=tuaemail@email.com"
        }

    fn = globals().get("invia_email_cliente_raccomandata")

    if not callable(fn):
        return {
            "success": False,
            "error": "Funzione invia_email_cliente_raccomandata non caricata"
        }

    ordine_test = {
        "id": "TEST-EMAIL-ECCOMI-POSTA",
        "cliente_email": to,
        "shopify_order_name": "#TEST-EMAIL",
        "numero_raccomandata": "619999999999",
        "pdf_ricevuta_cliente_url": "https://www.eccomionline.com/pages/eccomi-posta",
        "pdf_ricevuta_url": "",
        "email_sent": False
    }

    pratica_test = {
        "id": "",
        "cliente_email": to,
        "shopify_order_name": "#TEST-EMAIL",
        "numero_raccomandata": "619999999999",
        "email_sent": False
    }

    result = fn(
        ordine=ordine_test,
        pratica=pratica_test,
        pdf_cliente_url="https://www.eccomionline.com/pages/eccomi-posta"
    )

    return {
        "success": True,
        "test": "EMAIL_TEST_NO_POSTE",
        "to": to,
        "result": result
    }

@app.get("/supabase/test")
def supabase_test():
    try:
        buckets = supabase.storage.list_buckets()

        return {
            "success": True,
            "bucket_env": SUPABASE_BUCKET,
            "buckets": [b.name for b in buckets]
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/reporting/debug-operations")
def poste_reporting_debug_operations():
    try:
        history = HistoryPlugin()

        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_TOL_USERID,
            POSTE_H2H_TOL_PASSWORD
        )
        session.verify = False

        transport = Transport(session=session, timeout=60)

        client = Client(
            wsdl=POSTE_H2H_REPORTING_WSDL,
            transport=transport,
            plugins=[history]
        )

        services = []

        for service_name, service in client.wsdl.services.items():
            for port_name, port in service.ports.items():
                operations = list(port.binding._operations.keys())

                services.append({
                    "service": service_name,
                    "port": port_name,
                    "binding": str(port.binding.name),
                    "operations": operations
                })

        return {
            "success": True,
            "wsdl": POSTE_H2H_REPORTING_WSDL,
            "service_url": POSTE_H2H_REPORTING_SERVICE_URL,
            "services": services
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/poste/h2h/test")
def test_poste_h2h():
    try:
        client, service = poste_client(timeout=30)

        operations = []
        for s in client.wsdl.services.values():
            for port in s.ports.values():
                operations.extend(list(port.binding._operations.keys()))

        return {
            "success": True,
            "service": "Poste H2H Raccomandata Online",
            "userid": POSTE_H2H_USERID,
            "contract_id": POSTE_H2H_CONTRACT_ID,
            "wsdl": POSTE_H2H_ROL_WSDL,
            "service_url": POSTE_H2H_SERVICE_URL,
            "operations": operations
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/poste/h2h/test-raccomandata/check")
def test_raccomandata_h2h_check():
    """
    Verifica ambiente TEST Raccomandata H2H.
    NON invia raccomandate.
    NON genera costi.
    NON finalizza nulla.
    """

    try:
        client, service = poste_client_test(timeout=30)

        operations = []

        for srv in client.wsdl.services.values():
            for port in srv.ports.values():
                operations.extend(list(port.binding._operations.keys()))

        return {
            "success": True,
            "mode": "RACCOMANDATA_TEST",
            "wsdl_test": POSTE_H2H_ROL_WSDL_TEST,
            "service_url_test": POSTE_H2H_SERVICE_URL_TEST,
            "userid_test_present": bool(POSTE_H2H_USERID_TEST),
            "password_test_present": bool(POSTE_H2H_PASSWORD_TEST),
            "contract_id_test_present": bool(POSTE_H2H_CONTRACT_ID_TEST),
            "operations": operations
        }

    except Exception as e:
        return {
            "success": False,
            "mode": "RACCOMANDATA_TEST",
            "step": "ERRORE_CHECK_RACCOMANDATA_TEST",
            "error": str(e),
            "wsdl_test": POSTE_H2H_ROL_WSDL_TEST,
            "service_url_test": POSTE_H2H_SERVICE_URL_TEST,
            "userid_test_present": bool(POSTE_H2H_USERID_TEST),
            "password_test_present": bool(POSTE_H2H_PASSWORD_TEST),
            "contract_id_test_present": bool(POSTE_H2H_CONTRACT_ID_TEST)
        }

@app.get("/poste/h2h/telegramma/operations")
def telegramma_operations():
    """
    Legge il WSDL Telegramma Poste.
    NON invia Telegrammi.
    NON genera costi.
    Serve solo per vedere operazioni e firme disponibili.
    """

    try:
        client = telegramma_client(timeout=30)

        data = {}

        for service_name, srv in client.wsdl.services.items():
            data[service_name] = {}

            for port_name, port in srv.ports.items():
                operations = list(port.binding._operations.keys())

                address = ""

                try:
                    address = port.binding_options.get("address", "")
                except Exception:
                    address = ""

                data[service_name][port_name] = {
                    "address": address,
                    "operations": operations
                }

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "wsdl": POSTE_H2H_TOL_WSDL,
            "service_url": POSTE_H2H_TOL_SERVICE_URL,
            "services": data
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_OPERATIONS",
            "error": str(e),
            "wsdl": POSTE_H2H_TOL_WSDL
        }

@app.get("/poste/h2h/telegramma/signatures")
def telegramma_signatures():
    """
    Mostra le firme dei metodi Telegramma H2H.
    NON invia Telegrammi.
    NON genera costi.
    Serve per capire i parametri corretti di:
    - GetStatus
    - PreConfirm
    - Confirm
    - Submit
    """

    try:
        client = telegramma_client(timeout=30)

        methods_to_check = [
            "GetIdRequest",
            "RecipientsValidation",
            "Submit",
            "GetStatus",
            "PreConfirm",
            "Confirm",
            "Abort",
            "Modify"
        ]

        result = {}

        for service_name, srv in client.wsdl.services.items():
            result[service_name] = {}

            for port_name, port in srv.ports.items():
                operations = port.binding._operations

                result[service_name][port_name] = {}

                for method_name in methods_to_check:
                    operation = operations.get(method_name)

                    if not operation:
                        result[service_name][port_name][method_name] = {
                            "available": False
                        }
                        continue

                    result[service_name][port_name][method_name] = {
                        "available": True,
                        "input": str(operation.input.signature()),
                        "output": str(operation.output.signature())
                    }

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "wsdl": POSTE_H2H_TOL_WSDL,
            "methods": result
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_SIGNATURES",
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/flow-types")
def telegramma_flow_types():
    """
    Mostra i dettagli dei tipi necessari per il flusso finale Telegramma:
    - GetStatusRequest
    - GetStatusResult
    - PreconfirmResult
    - ConfirmOrder
    - ConfirmOrderResult
    - ArrayOfstring

    NON invia Telegrammi.
    NON genera costi.
    """

    try:
        client = telegramma_client(timeout=30)

        types_to_check = [
            ("GetStatusRequest", "Telegramma.WS"),
            ("GetStatusResult", "Telegramma.WS"),
            ("PreconfirmResult", "Telegramma.WS"),
            ("ConfirmOrder", "Telegramma.WS"),
            ("ConfirmOrderResult", "Telegramma.WS"),
            ("ArrayOfstring", ""),
            ("TResult", "GenericSchema"),
        ]

        result = {}

        for type_name, namespace_hint in types_to_check:
            try:
                t = telegramma_find_type(
                    client,
                    type_name,
                    namespace_hint
                )

                result[type_name] = {
                    "success": True,
                    "type": str(t)
                }

            except Exception as ex:
                result[type_name] = {
                    "success": False,
                    "error": str(ex)
                }

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "wsdl": POSTE_H2H_TOL_WSDL,
            "types": result
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_FLOW_TYPES",
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/get-status/{pratica_id}")
def telegramma_get_status_debug(pratica_id: str, guid: str = ""):
    """
    GetStatus Telegramma H2H.
    NON invia Telegrammi.
    NON genera costi.
    Usa GUIDMessage / idRequest per leggere lo stato.
    """

    history = HistoryPlugin()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        guid_message = (
            guid
            or poste_response.get("guid_message")
            or poste_response.get("id_request")
            or pratica.get("id_richiesta")
            or ""
        )

        if not guid_message:
            return {
                "success": False,
                "error": "GUIDMessage/idRequest mancante",
                "pratica_id": pratica_id
            }

        client, service = telegramma_service(
            timeout=60,
            extra_plugins=[history]
        )

        GetStatusRequestType = telegramma_find_type(
            client,
            "GetStatusRequest",
            "Telegramma.WS"
        )

        get_status_request = GetStatusRequestType(
            GUIDMessage=guid_message
        )

        result = service.GetStatus(
            getStatusRequest=get_status_request
        )

        plain_result = make_json_safe(
            zeep_to_plain(result)
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        new_poste_response = dict(poste_response or {})
        new_poste_response["last_get_status"] = {
            "step": "TELEGRAMMA_GET_STATUS",
            "guid_message": guid_message,
            "result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received,
            "checked_at": now_iso
        }

        supabase.table("pratiche") \
            .update({
                "poste_response": new_poste_response,
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return {
            "success": True,
            "step": "TELEGRAMMA_GET_STATUS",
            "pratica_id": pratica_id,
            "guid_message": guid_message,
            "result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_GET_STATUS",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/preconfirm/{pratica_id}")
def telegramma_preconfirm_debug(pratica_id: str, guid: str = "", force: int = 0):
    """
    PreConfirm Telegramma H2H con autoConfirm=true.
    ATTENZIONE:
    - Va chiamato solo dopo Submit OK / GetStatus OK.
    - Per sicurezza, di default lavora solo se la pratica è SUBMIT_POSTE_OK.
    - force=1 permette test tecnico manuale.
    """

    history = HistoryPlugin()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data
        stato = pratica.get("stato")

        if stato != "SUBMIT_POSTE_OK" and force != 1:
            return {
                "success": False,
                "blocked": True,
                "error": "PreConfirm bloccato: consentito solo dopo SUBMIT_POSTE_OK",
                "stato": stato,
                "pratica_id": pratica_id,
                "hint": "Usa force=1 solo per test tecnico controllato"
            }

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        id_request = (
            guid
            or poste_response.get("guid_message")
            or poste_response.get("id_request")
            or pratica.get("id_richiesta")
            or ""
        )

        if not id_request:
            return {
                "success": False,
                "error": "idRequest/GUIDMessage mancante",
                "pratica_id": pratica_id
            }

        client, service = telegramma_service(
            timeout=60,
            extra_plugins=[history]
        )

        ArrayOfstringType = telegramma_find_type(
            client,
            "ArrayOfstring",
            ""
        )

        id_request_array = ArrayOfstringType(
            string=[id_request]
        )

        result = service.PreConfirm(
            idRequest=id_request_array,
            autoConfirm=True,
            forceOrderCreation=True
        )

        plain_result = make_json_safe(
            zeep_to_plain(result)
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        new_poste_response = dict(poste_response or {})
        new_poste_response["last_preconfirm"] = {
            "step": "TELEGRAMMA_PRECONFIRM",
            "id_request": id_request,
            "autoConfirm": True,
            "forceOrderCreation": True,
            "result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received,
            "checked_at": now_iso
        }

        supabase.table("pratiche") \
            .update({
                "poste_response": new_poste_response,
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return {
            "success": True,
            "step": "TELEGRAMMA_PRECONFIRM",
            "pratica_id": pratica_id,
            "id_request": id_request,
            "result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_PRECONFIRM",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/completa-da-submit/{pratica_id}")
def telegramma_completa_da_submit(pratica_id: str, guid: str = ""):
    """
    Completa il flusso Telegramma H2H DOPO un Submit già riuscito.

    NON rifà Submit.
    NON crea un nuovo Telegramma.
    Usa GUIDMessage/idRequest già esistente.

    Flusso:
    - GetStatus
    - se necessario PreConfirm autoConfirm=true
    - GetStatus finale
    - se Printing: aggiorna pratica a INVIATO_POSTE

    Sicurezza:
    - sempre permesso sulla pratica tecnica #1392
    - sulle pratiche reali solo se TELEGRAMMA_H2H_AUTO_ENABLED=true
    - blocca automaticamente se l'ambiente Poste è ancora sptest
    """

    history = HistoryPlugin()

    try:
        # =====================================================
        # SICUREZZA TELEGRAMMA H2H AUTOMATICO
        # =====================================================

        telegramma_auto_enabled = os.getenv(
            "TELEGRAMMA_H2H_AUTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        is_pratica_tecnica = pratica_id == "525aceed-cd97-400e-9a25-49ec102078f1"

        if not is_pratica_tecnica and not telegramma_auto_enabled:
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_COMPLETA_SUBMIT_BLOCCATO",
                "error": "Completamento Telegramma H2H automatico disattivato. Imposta TELEGRAMMA_H2H_AUTO_ENABLED=true su Render.",
                "pratica_id": pratica_id
            }
            
        telegramma_test_send_enabled = os.getenv(
            "TELEGRAMMA_H2H_TEST_SEND_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        if (
            not is_pratica_tecnica
            and "sptest" in str(POSTE_H2H_TOL_SERVICE_URL).lower()
            and not telegramma_test_send_enabled
        ):
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_AMBIENTE_TEST_BLOCCATO",
                "error": "Ambiente Poste TEST rilevato. Per testare su sptest imposta TELEGRAMMA_H2H_TEST_SEND_ENABLED=true.",
                "service_url": POSTE_H2H_TOL_SERVICE_URL,
                "pratica_id": pratica_id
            }

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        guid_message = (
            guid
            or pratica.get("id_richiesta")
            or poste_response.get("guid_message")
            or poste_response.get("id_request")
            or ""
        )

        if not guid_message:
            return {
                "success": False,
                "error": "GUIDMessage/idRequest mancante",
                "pratica_id": pratica_id
            }

        client, service = telegramma_service(
            timeout=90,
            extra_plugins=[history]
        )

        GetStatusRequestType = telegramma_find_type(
            client,
            "GetStatusRequest",
            "Telegramma.WS"
        )

        ArrayOfstringType = telegramma_find_type(
            client,
            "ArrayOfstring",
            ""
        )

        def call_get_status():
            req = GetStatusRequestType(
                GUIDMessage=guid_message
            )

            res = service.GetStatus(
                getStatusRequest=req
            )

            return make_json_safe(
                zeep_to_plain(res)
            )

        def estrai_stato_e_idtelegramma(status_plain):
            try:
                details = (
                    status_plain.get("Status", {})
                    .get("TelgramStatusDetails", {})
                    .get("TelegrammaStatusDetailsType", [])
                )

                if isinstance(details, dict):
                    details = [details]

                if not details:
                    return None, None

                first = details[0] or {}

                return (
                    first.get("State"),
                    first.get("IDTelegramma")
                )

            except Exception:
                return None, None

        # 1. GetStatus iniziale
        status_before = call_get_status()
        state_before, id_telegramma_before = estrai_stato_e_idtelegramma(status_before)

        preconfirm_plain = None
        preconfirm_called = False
        
        stati_finali_ok = ["Printing", "Confirmed"]

        # 2. Se non è già in stato finale, chiama PreConfirm
        if state_before not in stati_finali_ok:
            id_request_array = ArrayOfstringType(
                string=[guid_message]
            )

            preconfirm_result = service.PreConfirm(
                idRequest=id_request_array,
                autoConfirm=True,
                forceOrderCreation=True
            )

            preconfirm_plain = make_json_safe(
                zeep_to_plain(preconfirm_result)
            )

            preconfirm_called = True

            # Anche se PreConfirm restituisce warning/errore,
            # Poste può comunque portare lo stato a Printing.
            time.sleep(2)

        # 3. GetStatus finale
        status_after = call_get_status()
        state_after, id_telegramma_after = estrai_stato_e_idtelegramma(status_after)

        final_state = state_after or state_before
        id_telegramma = id_telegramma_after or id_telegramma_before

        # 4. Recupera dati dal Submit salvato
        submit_result_saved = poste_response.get("submit_result") or {}
        submit_telegramma = submit_result_saved.get("telegramma") or {}

        parti_testo = (
            (submit_telegramma.get("PartiTesto") or {})
            .get("Testo")
            or ""
        )

        numero_accettazione = None

        match_acc = re.search(
            r"Numero Accettazione:\s*([0-9]+)",
            parti_testo,
            flags=re.IGNORECASE
        )

        if match_acc:
            numero_accettazione = match_acc.group(1)

        valorizzazione = submit_telegramma.get("Valorizzazione") or {}
        importo_totale = valorizzazione.get("ImportoTotale")

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        nuovo_stato = "INVIATO_POSTE" if final_state in stati_finali_ok else "SUBMIT_POSTE_OK"

        new_poste_response = dict(poste_response or {})
        new_poste_response["telegramma_flow_complete"] = {
            "step": "TELEGRAMMA_COMPLETA_DA_SUBMIT",
            "guid_message": guid_message,
            "id_request": guid_message,
            "preconfirm_called": preconfirm_called,
            "status_before": status_before,
            "preconfirm_result": preconfirm_plain,
            "status_after": status_after,
            "final_state": final_state,
            "id_telegramma": id_telegramma,
            "numero_accettazione": numero_accettazione,
            "importo_totale": importo_totale,
            "completed_at": now_iso
        }

        update_data = {
            "stato": nuovo_stato,
            "id_richiesta": guid_message,
            "poste_response": new_poste_response,
            "updated_at": now_iso
        }

        if numero_accettazione:
            update_data["numero_raccomandata"] = numero_accettazione
        elif id_telegramma:
            update_data["numero_raccomandata"] = id_telegramma

        supabase.table("pratiche") \
            .update(update_data) \
            .eq("id", pratica_id) \
            .execute()

        return {
            "success": True,
            "step": "TELEGRAMMA_COMPLETA_DA_SUBMIT",
            "pratica_id": pratica_id,
            "guid_message": guid_message,
            "preconfirm_called": preconfirm_called,
            "state_before": state_before,
            "state_after": state_after,
            "final_state": final_state,
            "nuovo_stato": nuovo_stato,
            "id_telegramma": id_telegramma,
            "numero_accettazione": numero_accettazione,
            "importo_totale": importo_totale,
            "status_before": status_before,
            "preconfirm_result": preconfirm_plain,
            "status_after": status_after
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_COMPLETA_DA_SUBMIT",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/telegramma-loading/{pratica_id}", response_class=HTMLResponse)
def dashboard_telegramma_loading(pratica_id: str):
    return f"""
    <!doctype html>
    <html lang="it">
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>Eccomi Posta - Invio Telegramma</title>

        <style>
            body {{
                margin: 0;
                min-height: 100vh;
                font-family: Arial, Helvetica, sans-serif;
                background: linear-gradient(135deg, #fff7ed, #f4f6f9);
                display: flex;
                align-items: center;
                justify-content: center;
                color: #111827;
            }}

            .box {{
                width: min(620px, 92vw);
                background: #ffffff;
                border-radius: 26px;
                padding: 38px 34px;
                box-shadow: 0 24px 70px rgba(15, 23, 42, 0.20);
                text-align: center;
                border: 1px solid #fed7aa;
            }}

            .logo {{
                font-size: 56px;
                margin-bottom: 12px;
            }}

            h1 {{
                margin: 0 0 12px;
                font-size: 30px;
                color: #111827;
            }}

            p {{
                font-size: 16px;
                color: #4b5563;
                line-height: 1.55;
                margin: 0;
            }}

            .loader {{
                width: 58px;
                height: 58px;
                margin: 28px auto 22px;
                border: 6px solid #e5e7eb;
                border-top-color: #f97316;
                border-radius: 50%;
                animation: spin 0.9s linear infinite;
            }}

            @keyframes spin {{
                to {{
                    transform: rotate(360deg);
                }}
            }}

            .log {{
                margin-top: 18px;
                padding: 15px;
                border-radius: 14px;
                background: #f8fafc;
                font-size: 14px;
                color: #374151;
                border: 1px solid #e5e7eb;
            }}

            .ok {{
                background: #ecfdf5;
                color: #166534;
                border-color: #bbf7d0;
            }}

            .error {{
                background: #fee2e2;
                color: #991b1b;
                border-color: #fecaca;
            }}

            .small {{
                margin-top: 18px;
                font-size: 12px;
                color: #9ca3af;
            }}
        </style>
    </head>

    <body>
        <div class="box">
            <div class="logo">📨</div>

            <h1>Eccomi Posta in elaborazione</h1>

            <p>
                Stiamo inviando il Telegramma tramite Poste H2H.<br>
                Attendi qualche secondo, non chiudere questa pagina.
            </p>

            <div class="loader"></div>

            <div id="log" class="log">
                Preparazione invio Telegramma...
            </div>

            <div class="small">
                Ambiente operativo Eccomi Posta
            </div>
        </div>

        <script>
            const praticaId = "{pratica_id}";
            const log = document.getElementById("log");

            async function avviaInvio() {{
                try {{
                    log.textContent = "Connessione con Poste H2H...";

                    const response = await fetch(`/poste/h2h/telegramma/invia-completo/${{praticaId}}`, {{
                        method: "GET",
                        headers: {{
                            "Accept": "application/json"
                        }}
                    }});

                    const data = await response.json();

                    const ok = data && (
                        data.success === true ||
                        data.numero_accettazione ||
                        data.nuovo_stato === "SUBMIT_POSTE_OK" ||
                        data.nuovo_stato === "INVIATO_POSTE"
                    );

                    if (ok) {{
                        log.classList.add("ok");
                        log.textContent = "Telegramma elaborato correttamente. Ritorno alla dashboard...";

                        setTimeout(() => {{
                            window.location.href = "/dashboard/pratiche?telegramma=inviato";
                        }}, 1000);

                        return;
                    }}

                    log.classList.add("error");
                    log.textContent = "Errore invio Telegramma: " + (data.error || data.step || "errore sconosciuto");

                }} catch (err) {{
                    log.classList.add("error");
                    log.textContent = "Errore tecnico: " + err.message;
                }}
            }}

            avviaInvio();
        </script>
    </body>
    </html>
    """

@app.get("/poste/h2h/telegramma/invia-completo/{pratica_id}")
def telegramma_invia_completo(pratica_id: str, variant: str = ""):
    """
    Flusso completo Telegramma H2H:
    - Submit
    - GetStatus
    - PreConfirm autoConfirm=true se necessario
    - GetStatus finale
    - se Printing: pratica INVIATO_POSTE

    Sicurezza:
    - sempre permesso sulla pratica tecnica #1392
    - sulle pratiche reali solo se TELEGRAMMA_H2H_AUTO_ENABLED=true
    - blocca automaticamente se l'ambiente Poste è ancora sptest
    """

    try:
        telegramma_auto_enabled = os.getenv(
            "TELEGRAMMA_H2H_AUTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        is_pratica_tecnica = pratica_id == "525aceed-cd97-400e-9a25-49ec102078f1"

        if not is_pratica_tecnica and not telegramma_auto_enabled:
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_INVIA_COMPLETO_BLOCCATO",
                "error": "Invio automatico Telegramma H2H disattivato. Imposta TELEGRAMMA_H2H_AUTO_ENABLED=true su Render.",
                "pratica_id": pratica_id
            }

        telegramma_test_send_enabled = os.getenv(
            "TELEGRAMMA_H2H_TEST_SEND_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        if (
            not is_pratica_tecnica
            and "sptest" in str(POSTE_H2H_TOL_SERVICE_URL).lower()
            and not telegramma_test_send_enabled
        ):
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_AMBIENTE_TEST_BLOCCATO",
                "error": "Ambiente Poste TEST rilevato. Per testare su sptest imposta TELEGRAMMA_H2H_TEST_SEND_ENABLED=true.",
                "service_url": POSTE_H2H_TOL_SERVICE_URL,
                "pratica_id": pratica_id
            }   
            
        # 1. Submit
        submit_response = _telegramma_submit_poste(
            pratica_id=pratica_id,
            variant=variant
        )

        if not isinstance(submit_response, dict):
            return {
                "success": False,
                "step": "ERRORE_TELEGRAMMA_INVIA_COMPLETO",
                "error": "Risposta Submit non valida",
                "submit_response": str(submit_response)
            }

        if not submit_response.get("success"):
            return {
                "success": False,
                "step": "ERRORE_SUBMIT_NEL_FLUSSO_COMPLETO",
                "submit_response": submit_response
            }

        submit_result = submit_response.get("submit_result") or {}

        submit_result_info = (
            (submit_result.get("SubmitResult") or {}).get("Result")
            or submit_result.get("Result")
            or {}
        )

        poste_res_type = submit_result_info.get("ResType")
        poste_description = submit_result_info.get("Description")

        if poste_res_type not in ["I", "W"]:
            return {
                "success": False,
                "step": "SUBMIT_NON_COMPLETATO",
                "poste_res_type": poste_res_type,
                "poste_description": poste_description,
                "submit_response": submit_response
            }

        guid_message = (
            submit_response.get("guid_message")
            or submit_response.get("id_request")
            or ""
        )

        if not guid_message:
            return {
                "success": False,
                "step": "GUID_MANCANTE_DOPO_SUBMIT",
                "submit_response": submit_response
            }

        time.sleep(2)

        # 2. Completa da Submit fino a Printing
        complete_response = telegramma_completa_da_submit(
            pratica_id=pratica_id,
            guid=guid_message
        )

        if not isinstance(complete_response, dict):
            return {
                "success": False,
                "step": "ERRORE_COMPLETA_DA_SUBMIT",
                "error": "Risposta completamento non valida",
                "complete_response": str(complete_response)
            }

        final_state = complete_response.get("final_state")
        nuovo_stato = complete_response.get("nuovo_stato")

        numero_accettazione = complete_response.get("numero_accettazione")
        id_telegramma = complete_response.get("id_telegramma")
        importo_totale = complete_response.get("importo_totale")

        invio_ok = bool(
            final_state in ["Printing", "Confirmed"]
            or (
                poste_res_type in ["I", "W"]
                and numero_accettazione
                and id_telegramma
            )
        )

        stato_dashboard = "INVIATO_POSTE" if invio_ok else (nuovo_stato or "SUBMIT_POSTE_OK")

        if invio_ok:
            supabase.table("pratiche") \
                .update({
                    "stato": stato_dashboard,
                    "numero_raccomandata": numero_accettazione,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }) \
                .eq("id", pratica_id) \
                .execute()

        return {
            "success": invio_ok,
            "step": "TELEGRAMMA_INVIA_COMPLETO",
            "pratica_id": pratica_id,
            "guid_message": guid_message,
            "submit_res_type": poste_res_type,
            "submit_description": poste_description,
            "final_state": final_state,
            "nuovo_stato": stato_dashboard,
            "numero_accettazione": numero_accettazione,
            "id_telegramma": id_telegramma,
            "importo_totale": importo_totale,
            "submit_response": submit_response,
            "complete_response": complete_response
        }
        
    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_INVIA_COMPLETO",
            "pratica_id": pratica_id,
            "error": str(e)
        }        

def auto_telegramma_post_pagamento(pratica_id: str):
    """
    AUTO TELEGRAMMA POST-PAGAMENTO

    TEST:
    - se POSTE_H2H_TOL_SERVICE_URL contiene sptest
    - richiede AUTO_TELEGRAMMA_TEST_POST_PAGAMENTO_ENABLED=true

    PRODUZIONE:
    - se POSTE_H2H_TOL_SERVICE_URL NON contiene sptest
    - richiede AUTO_TELEGRAMMA_PROD_POST_PAGAMENTO_ENABLED=true
    """

    try:
        service_url = str(POSTE_H2H_TOL_SERVICE_URL or "")
        is_test_env = "sptest" in service_url.lower()

        test_enabled = os.getenv(
            "AUTO_TELEGRAMMA_TEST_POST_PAGAMENTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        prod_enabled = os.getenv(
            "AUTO_TELEGRAMMA_PROD_POST_PAGAMENTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        mode = "TEST" if is_test_env else "PROD"

        if is_test_env and not test_enabled:
            return {
                "success": True,
                "skipped": True,
                "step": "AUTO_TELEGRAMMA_TEST_DISABLED",
                "mode": mode,
                "message": "Auto Telegramma TEST post-pagamento disattivato"
            }

        if not is_test_env and not prod_enabled:
            return {
                "success": False,
                "blocked": True,
                "step": "AUTO_TELEGRAMMA_PROD_BLOCKED",
                "mode": mode,
                "error": "Auto Telegramma PRODUZIONE disattivato",
                "service_url": service_url
            }

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "AUTO_TELEGRAMMA_PRATICA_NON_TROVATA",
                "mode": mode,
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        tipo_servizio = (pratica.get("tipo_servizio") or "").upper()
        stato = pratica.get("stato") or ""
        email_sent = bool_from_any(pratica.get("email_sent"))

        if tipo_servizio != "TELEGRAMMA":
            return {
                "success": True,
                "skipped": True,
                "step": "AUTO_TELEGRAMMA_NON_TELEGRAMMA",
                "mode": mode,
                "tipo_servizio": tipo_servizio
            }

        if stato == "INVIATO_POSTE" and email_sent:
            return {
                "success": True,
                "skipped": True,
                "step": "AUTO_TELEGRAMMA_GIA_COMPLETO",
                "mode": mode,
                "stato": stato,
                "email_sent": email_sent
            }

        preventivo_result = dashboard_telegramma_preventivo(
            pratica_id=pratica_id,
            redirect=0
        )

        if isinstance(preventivo_result, dict) and preventivo_result.get("success") is False:
            return {
                "success": False,
                "step": "AUTO_TELEGRAMMA_ERRORE_PREVENTIVO",
                "mode": mode,
                "preventivo_result": preventivo_result
            }

        invio_result = telegramma_invia_completo(
            pratica_id=pratica_id
        )

        if not isinstance(invio_result, dict) or not invio_result.get("success"):
            return {
                "success": False,
                "step": "AUTO_TELEGRAMMA_ERRORE_INVIO_H2H",
                "mode": mode,
                "invio_result": invio_result
            }

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        pratica = pratica_res.data or pratica

        try:
            pdf_cliente_url = ensure_pdf_cliente_telegramma(pratica)
        except Exception as pdf_err:
            return {
                "success": False,
                "step": "AUTO_TELEGRAMMA_ERRORE_PDF_CLIENTE",
                "mode": mode,
                "error": str(pdf_err)
            }

        email_result = dashboard_invia_email_cliente(pratica_id)

        return {
            "success": True,
            "step": "AUTO_TELEGRAMMA_POST_PAGAMENTO_COMPLETO",
            "mode": mode,
            "pratica_id": pratica_id,
            "pdf_cliente_url": pdf_cliente_url,
            "preventivo_result": preventivo_result,
            "invio_result": invio_result,
            "email_result": str(email_result)
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_AUTO_TELEGRAMMA_POST_PAGAMENTO",
            "pratica_id": pratica_id,
            "error": str(e)
        }


def auto_telegramma_test_post_pagamento(pratica_id: str):
    """
    Compatibilità con il webhook Shopify già esistente.
    Ora usa la funzione unica TEST/PROD.
    """
    return auto_telegramma_post_pagamento(pratica_id)

    
@app.get("/dashboard/pratiche/telegramma-preventivo/{pratica_id}")
def dashboard_telegramma_preventivo(pratica_id: str, redirect: int = 0):
    """
    Recupera il prezzo reale Poste del Telegramma tramite Preventivo.
    NON invia Telegrammi.
    NON finalizza.
    NON genera costo H2H di invio.
    """

    history = HistoryPlugin()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        parole = pratica.get("parole") or 0

        try:
            parole = int(parole)
        except Exception:
            parole = 0

        if parole <= 0:
            testo = pratica.get("testo") or ""
            parole = len([w for w in testo.split() if w.strip()])

        if parole <= 0:
            return {
                "success": False,
                "error": "Numero parole non valido per il preventivo Telegramma",
                "parole": parole,
                "pratica_id": pratica_id
            }

        client, service = telegramma_service(
            timeout=60,
            extra_plugins=[history]
        )

        TOLPricingRequest = client.get_type("ns0:TOLPricingRequest")

        request_preventivo = TOLPricingRequest(
            AnticipazioneTelefonica=False,
            CopiaMittente=False,
            Coupon=None,
            JoikidElettronico=False,
            Jokid=False,
            Nazionale=True,
            NumeroDestinatari=1,
            Parole=parole,
            StatoDestinazione="ITALIA"
        )

        result = service.Preventivo(
            request=request_preventivo
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        plain = zeep_to_plain(result)

        prezzo_totale = None

        try:
            prezzo_totale = float(plain.get("prezzoTotale"))
        except Exception:
            try:
                prezzo_totale = float(result.prezzoTotale)
            except Exception:
                prezzo_totale = None

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        poste_payload = {
            "step": "TELEGRAMMA_PREVENTIVO_POSTE",
            "note": "Preventivo reale Poste Telegramma recuperato",
            "parole": parole,
            "prezzo_totale": prezzo_totale,
            "raw": str(result),
            "preventivo_at": now_iso
        }

        supabase.table("pratiche") \
            .update({
                "stato": "PREZZATA_DA_CONFERMARE",
                "poste_response": poste_payload,
                "xml_sent": xml_sent,
                "xml_received": xml_received,
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()
        
        if redirect == 1:
            return RedirectResponse(
                url="/dashboard/pratiche",
                status_code=303
            )

        return {
            "success": True,
            "step": "TELEGRAMMA_PREVENTIVO_POSTE",
            "pratica_id": pratica_id,
            "parole": parole,
            "prezzo_totale": prezzo_totale,
            "poste_response": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_PREVENTIVO",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/telegramma/signatures")
def telegramma_signatures():
    """
    Legge firme input/output delle operazioni Telegramma.
    NON invia Telegrammi.
    NON genera costi.
    Serve per costruire correttamente Preventivo, Submit, PreConfirm e Confirm.
    """

    try:
        client = telegramma_client(timeout=30)

        wanted_ops = [
            "GetIdRequest",
            "RecipientValidation",
            "RecipientsValidation",
            "Preventivo",
            "Submit",
            "SubmitJokid",
            "PreConfirm",
            "Confirm",
            "GetStatus"
        ]

        result = {}

        for service_name, srv in client.wsdl.services.items():
            for port_name, port in srv.ports.items():
                operations = port.binding._operations

                for op_name in wanted_ops:
                    if op_name in operations:
                        op = operations[op_name]

                        result[op_name] = {
                            "input": str(op.input.signature()),
                            "output": str(op.output.signature())
                        }

                break
            break

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "operations": result
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_SIGNATURES",
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/types")
def telegramma_types():
    """
    Legge i tipi principali del servizio Telegramma.
    NON invia Telegrammi.
    NON genera costi.
    Serve per costruire Preventivo, Submit, PreConfirm e Confirm.
    """

    try:
        client = telegramma_client(timeout=30)

        types_to_check = [
            "ns0:TOLPricingRequest",
            "ns0:TOLPricingResponse",
            "ns0:Telegramma",
            "ns0:SubmitResult",
            "ns0:PreconfirmResult",
            "ns10:ConfirmOrder",
            "ns0:ConfirmOrderResult",
            "ns0:Recipient",
            "ns0:RecipientValidationResult",
            "ns0:GetStatusRequest",
            "ns0:GetStatusResult",
            "ns0:ArrayOfRecipient",
            "ns7:ArrayOfstring"
        ]

        result = {}

        for type_name in types_to_check:
            try:
                result[type_name] = str(client.get_type(type_name))
            except Exception as e:
                result[type_name] = f"ERRORE: {str(e)}"

        namespaces = {}

        try:
            for prefix, namespace in client.namespaces:
                namespaces[str(prefix)] = str(namespace)
        except Exception:
            pass

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "namespaces": namespaces,
            "types": result
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_TYPES",
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/deep-types")
def telegramma_deep_types():
    """
    Legge tutti i tipi disponibili nel WSDL Telegramma Poste.
    NON invia Telegrammi.
    NON genera costi.
    Serve per costruire correttamente Submit / PreConfirm / Confirm.
    """

    try:
        client = telegramma_client(timeout=30)

        keywords = [
            "Telegramma",
            "Mittente",
            "Recipient",
            "Destinat",
            "Testo",
            "Parti",
            "Opzioni",
            "Valorizzazione",
            "Confirm",
            "Order",
            "Submit",
            "TOL"
        ]

        found = []

        for t in client.wsdl.types.types:
            text = str(t)

            if any(k.lower() in text.lower() for k in keywords):
                found.append(text)

        return {
            "success": True,
            "service": "Telegramma H2H Poste",
            "count": len(found),
            "types": found
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_DEEP_TYPES",
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/type-debug/{needle}")
def telegramma_type_debug(needle: str):
    try:
        client, service = telegramma_service()

        matches = []

        def safe_str(value):
            try:
                return str(value)
            except Exception:
                return repr(value)

        def safe_dict(obj):
            out = {}
            try:
                for k, v in getattr(obj, "__dict__", {}).items():
                    out[str(k)] = safe_str(v)
            except Exception as e:
                out["_error"] = str(e)
            return out

        for t in client.wsdl.types.types:
            qname = safe_str(getattr(t, "qname", ""))
            name = safe_str(getattr(t, "name", ""))
            representation = safe_str(t)

            haystack = f"{qname} {name} {representation}".lower()

            if needle.lower() in haystack:
                row = {
                    "class": t.__class__.__name__,
                    "qname": qname,
                    "name": name,
                    "repr": representation,
                    "dict": safe_dict(t),
                }

                for attr in [
                    "_restriction",
                    "restriction",
                    "_base_type",
                    "base_type",
                    "_default_qname",
                    "accepted_types",
                    "_resolved",
                ]:
                    try:
                        value = getattr(t, attr, None)
                        row[attr] = safe_str(value)
                        if value is not None:
                            row[f"{attr}_dict"] = safe_dict(value)
                    except Exception as e:
                        row[attr] = f"ERRORE: {e}"

                matches.append(row)

        return {
            "success": True,
            "needle": needle,
            "count": len(matches),
            "matches": matches,
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TYPE_DEBUG",
            "needle": needle,
            "error": str(e),
        }

@app.get("/poste/h2h/telegramma/get-status-guid/{guid_message}")
def telegramma_get_status_guid(guid_message: str):
    try:
        client, service = telegramma_service()

        GetStatusRequestType = telegramma_find_type(
            client,
            "GetStatusRequest",
            "Telegramma.WS"
        )

        get_status_request = GetStatusRequestType(
            GUIDMessage=guid_message
        )

        result = service.GetStatus(
            getStatusRequest=get_status_request
        )

        plain_result = make_json_safe(
            zeep_to_plain(result)
        )

        return {
            "success": True,
            "step": "TELEGRAMMA_GET_STATUS",
            "guid_message": guid_message,
            "result": plain_result
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_GET_STATUS",
            "guid_message": guid_message,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/debug-package/{pratica_id}")
def telegramma_debug_package(pratica_id: str):
    try:
        pratica_res = (
            supabase
            .table("pratiche")
            .select("*")
            .eq("id", pratica_id)
            .single()
            .execute()
        )

        pratica = pratica_res.data

        if not pratica:
            return {
                "success": False,
                "step": "TELEGRAMMA_DEBUG_PACKAGE",
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        id_request = (
            poste_response.get("id_request")
            or poste_response.get("idRequest")
            or poste_response.get("id_request_submit")
        )

        guid_message = (
            poste_response.get("guid_message")
            or poste_response.get("GUIDMessage")
            or poste_response.get("guid")
        )

        submit_result = poste_response.get("submit_result")
        validation_same_id_request = poste_response.get("validation_same_id_request")
        pricing = poste_response.get("pricing")

        xml_sent = (
            pratica.get("xml_sent")
            or poste_response.get("xml_sent")
            or ""
        )

        xml_received = (
            pratica.get("xml_received")
            or poste_response.get("xml_received")
            or ""
        )

        get_status_result = None

        if guid_message:
            try:
                client, service = telegramma_service()

                GetStatusRequestType = telegramma_find_type(
                    client,
                    "GetStatusRequest",
                    "Telegramma.WS"
                )

                get_status_request = GetStatusRequestType(
                    GUIDMessage=guid_message
                )

                status_result = service.GetStatus(
                    getStatusRequest=get_status_request
                )

                get_status_result = make_json_safe(
                    zeep_to_plain(status_result)
                )

            except Exception as status_error:
                get_status_result = {
                    "success": False,
                    "error": str(status_error)
                }

        ticket_text = f"""
Buongiorno,

stiamo effettuando i test di integrazione del servizio Telegramma H2H in ambiente test.

Dati servizio:
- Servizio: Telegramma H2H
- Ambiente: test
- Endpoint: {POSTE_H2H_TOL_SERVICE_URL}
- Userid: {POSTE_H2H_TOL_USERID}
- Codice contratto: {POSTE_H2H_TOL_CONTRACT_ID}

Operazioni riuscite:
- GetIdRequest: OK
- Preventivo: OK
- RecipientsValidation: OK / Address is valid

Operazione non riuscita:
- Submit

Pratica interna:
- pratica_id: {pratica_id}
- order_name: {pratica.get("order_name")}
- tipo_servizio: {pratica.get("tipo_servizio")}

Dati Submit:
- idRequest: {id_request}
- GUIDMessage: {guid_message}

Risposta Submit:
{json.dumps(submit_result, ensure_ascii=False, indent=2)}

Validation stesso idRequest:
{json.dumps(validation_same_id_request, ensure_ascii=False, indent=2)}

Preventivo:
{json.dumps(pricing, ensure_ascii=False, indent=2)}

GetStatus:
{json.dumps(get_status_result, ensure_ascii=False, indent=2)}

Il Submit restituisce:
"Unexpected error has occurred. The request has not been processed"

Il GetStatus restituisce telegramma non trovato, quindi il Submit non registra il telegramma.

Chiediamo cortesemente verifica sui log interni Poste per capire quale campo, parametro contrattuale o abilitazione blocca il Submit.
""".strip()

        return {
            "success": True,
            "step": "TELEGRAMMA_DEBUG_PACKAGE",
            "pratica_id": pratica_id,
            "order_name": pratica.get("order_name"),
            "stato": pratica.get("stato"),
            "id_request": id_request,
            "guid_message": guid_message,
            "validation_same_id_request": validation_same_id_request,
            "pricing": pricing,
            "submit_result": submit_result,
            "get_status_result": get_status_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received,
            "ticket_text": ticket_text
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_DEBUG_PACKAGE",
            "pratica_id": pratica_id,
            "error": str(e)
        }


def telegramma_find_type(client, local_name, namespace_contains=None):
    """
    Cerca un tipo nel WSDL Telegramma usando il nome locale.
    Serve perché i prefissi ns0/ns1/ns2 possono cambiare.
    """

    matches = []

    for t in client.wsdl.types.types:
        qname = getattr(t, "qname", None)

        if not qname:
            continue

        try:
            q_local = qname.localname
            q_namespace = str(qname.namespace)
        except Exception:
            continue

        if q_local != local_name:
            continue

        if namespace_contains and namespace_contains not in q_namespace:
            continue

        matches.append(qname)

    if not matches:
        raise RuntimeError(
            f"Tipo Telegramma non trovato: {local_name} / {namespace_contains}"
        )

    return client.wsdl.types.get_type(matches[0])


def telegramma_split_nome_cognome(full_name):
    full_name = str(full_name or "").strip()

    parts = full_name.split()

    if len(parts) <= 1:
        return full_name, ""

    return parts[0], " ".join(parts[1:])

def telegramma_clean_telefono(value):
    """
    Poste si aspetta un telefono, non una email.
    Se arriva una email o testo non telefonico, restituisce stringa vuota.
    """

    value = str(value or "").strip()

    if not value:
        return ""

    if "@" in value:
        return ""

    allowed = "+0123456789"

    cleaned = "".join(
        ch for ch in value
        if ch in allowed
    )

    if len(cleaned.replace("+", "")) < 6:
        return ""

    return cleaned

def telegramma_build_valorizzazione(client, service, parole):
    """
    Costruisce la Valorizzazione Telegramma partendo dal Preventivo Poste.
    Serve perché Submit sembra non accettare Valorizzazione vuota/nil.
    """

    from decimal import Decimal

    parole = int(parole or 1)

    TOLPricingRequestType = telegramma_find_type(
        client,
        "TOLPricingRequest",
        "Telegramma.WS"
    )

    ValorizzazioneType = telegramma_find_type(
        client,
        "Valorizzazione",
        "Telegramma.Schema"
    )

    DetailBillRowType = telegramma_find_type(
        client,
        "DetailBillRow",
        "PreConfirmResponseSchema"
    )

    ArrayOfDetailBillRowType = telegramma_find_type(
        client,
        "ArrayOfDetailBillRow",
        "PreConfirmResponseSchema"
    )

    pricing_request = TOLPricingRequestType(
        AnticipazioneTelefonica=False,
        CopiaMittente=False,
        Coupon=None,
        JoikidElettronico=False,
        Jokid=False,
        Nazionale=True,
        NumeroDestinatari=1,
        Parole=parole,
        StatoDestinazione="ITALIA"
    )

    pricing_result = service.Preventivo(
        request=pricing_request
    )

    pricing_plain = make_json_safe(
        zeep_to_plain(pricing_result)
    )

    prezzo_totale = Decimal(
        str(pricing_plain.get("prezzoTotale") or "0")
    )

    base_iva = Decimal(
        str(pricing_plain.get("baseImponibileIva") or "0")
    )

    base_no_iva = Decimal(
        str(pricing_plain.get("baseImponibileNoIva") or "0")
    )

    imponibile = base_iva + base_no_iva

    importo_iva = Decimal(
        str(pricing_plain.get("importoIva") or "0")
    )

    total_row = DetailBillRowType(
        Currency="EUR",
        Description="TELEGRAMMA",
        GrossValue=prezzo_totale,
        NetValue=imponibile,
        TaxAmount=importo_iva,
        GrossValuePerUnit=prezzo_totale,
        MaterialCode="TOLNAZIO",
        MaterialCodeDescription="TELEGRAMMI TRAFFICO NAZIONALE",
        NetValuePerUnit=imponibile,
        Quantity=Decimal("1"),
        TaxAmountPerUnit=importo_iva,
        TaxCode="22",
        TaxPercentage=Decimal("22")
    )

    details = ArrayOfDetailBillRowType(
        DetailBillRow=[
            total_row
        ]
    )

    valorizzazione_obj = ValorizzazioneType(
        Details=details,
        ImportoTotale=str(prezzo_totale),
        ParoleDigitate=parole,
        ParoleFisiche=parole,
        ParoleSviluppate=parole,
        ParoleTassabili=parole,
        ParoleTassateNelRigoPreambolo=0,
        TariffazioneManuale=False,
        Total=total_row
    )

    return valorizzazione_obj, pricing_plain


def telegramma_normalizza_dati_indirizzo(data):
    """
    Normalizza mittente/destinatario Telegramma dalla pratica Supabase.
    Gestisce sia dict strutturato sia raw.
    """

    data = data or {}

    if isinstance(data, str):
        data = {
            "raw": data
        }

    raw = str(data.get("raw") or "").strip()

    if raw:
        parsed = estrai_dati_rubrica_da_raw(raw)
    else:
        parsed = {
            "nome": str(data.get("nome") or "").strip(),
            "via": str(data.get("via") or "").strip(),
            "civico": str(data.get("civico") or "").strip(),
            "cap": str(data.get("cap") or "").strip(),
            "comune": str(data.get("comune") or "").strip(),
            "provincia": str(data.get("provincia") or "").strip().upper()[:2],
            "contatto": str(data.get("contatto") or "").strip()
        }

    indirizzo = " ".join([
        str(parsed.get("via") or "").strip(),
        str(parsed.get("civico") or "").strip()
    ]).strip()

    return {
        "nome": clean_h2h_text(parsed.get("nome") or ""),
        "indirizzo": clean_h2h_text(indirizzo),
        "cap": normalizza_cap(parsed.get("cap") or ""),
        "comune": clean_h2h_text(parsed.get("comune") or "").upper(),
        "provincia": normalizza_provincia(parsed.get("provincia") or ""),
        "telefono": telegramma_clean_telefono(
            parsed.get("contatto") or data.get("contatto") or ""
        )
    }

@app.get("/poste/h2h/telegramma/enum-values/{enum_name}")
def telegramma_enum_values(enum_name: str):
    """
    Cerca nel WSDL/XSD i valori ammessi per un enum Telegramma.
    NON invia Telegrammi.
    NON genera costi.
    """

    import re
    from urllib.parse import urljoin

    try:
        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_TOL_USERID,
            POSTE_H2H_TOL_PASSWORD
        )
        session.verify = False

        start_urls = [
            POSTE_H2H_TOL_WSDL,
            POSTE_H2H_TOL_SERVICE_URL + "?wsdl",
            POSTE_H2H_TOL_SERVICE_URL + "?singleWsdl"
        ]

        visited = set()
        queue = list(dict.fromkeys(start_urls))
        snippets = []
        enum_values = []

        while queue and len(visited) < 40:
            url = queue.pop(0)

            if url in visited:
                continue

            visited.add(url)

            try:
                r = session.get(url, timeout=30)
                text = r.text or ""
            except Exception:
                continue

            if enum_name in text:
                idx = text.find(enum_name)
                start = max(0, idx - 1500)
                end = min(len(text), idx + 3000)
                snippets.append({
                    "url": url,
                    "snippet": text[start:end]
                })

                pattern = (
                    r'<[^>]*(?:simpleType|SimpleType)[^>]*name=["\']'
                    + re.escape(enum_name)
                    + r'["\'][\s\S]*?</[^>]*(?:simpleType|SimpleType)>'
                )

                matches = re.findall(pattern, text)

                for m in matches:
                    values = re.findall(
                        r'<[^>]*(?:enumeration|Enumeration)[^>]*value=["\']([^"\']+)["\']',
                        m
                    )

                    for v in values:
                        if v not in enum_values:
                            enum_values.append(v)

            links = re.findall(
                r'(?:schemaLocation|location)=["\']([^"\']+)["\']',
                text
            )

            for link in links:
                full = urljoin(url, link)

                if full not in visited and full not in queue:
                    queue.append(full)

        return {
            "success": True,
            "enum_name": enum_name,
            "values": enum_values,
            "visited_count": len(visited),
            "visited": list(visited),
            "snippets_count": len(snippets),
            "snippets": snippets[:5]
        }

    except Exception as e:
        return {
            "success": False,
            "enum_name": enum_name,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/enum-values-fast/{enum_name}")
def telegramma_enum_values_fast(enum_name: str):
    """
    Versione veloce per cercare enum nel WSDL/XSD Telegramma.
    NON invia Telegrammi.
    NON genera costi.
    """

    import re
    from urllib.parse import urljoin

    try:
        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_TOL_USERID,
            POSTE_H2H_TOL_PASSWORD
        )
        session.verify = False

        urls = [
            POSTE_H2H_TOL_SERVICE_URL + "?singleWsdl",
            POSTE_H2H_TOL_SERVICE_URL + "?wsdl",
            POSTE_H2H_TOL_WSDL
        ]

        visited = []
        found_snippets = []
        enum_values = []

        for url in urls:
            try:
                r = session.get(url, timeout=8)
                text = r.text or ""
                visited.append(url)
            except Exception as ex:
                visited.append(f"{url} ERRORE: {ex}")
                continue

            if enum_name in text:
                idx = text.find(enum_name)
                snippet = text[max(0, idx - 2500): min(len(text), idx + 5000)]
                found_snippets.append({
                    "url": url,
                    "snippet": snippet
                })

                values = re.findall(
                    r'<[^>]*enumeration[^>]*value=["\']([^"\']+)["\']',
                    snippet,
                    flags=re.IGNORECASE
                )

                for v in values:
                    if v not in enum_values:
                        enum_values.append(v)

            links = re.findall(
                r'(?:schemaLocation|location)=["\']([^"\']+)["\']',
                text
            )

            for link in links[:12]:
                full = urljoin(url, link)

                try:
                    r2 = session.get(full, timeout=8)
                    text2 = r2.text or ""
                    visited.append(full)
                except Exception as ex:
                    visited.append(f"{full} ERRORE: {ex}")
                    continue

                if enum_name in text2:
                    idx = text2.find(enum_name)
                    snippet = text2[max(0, idx - 2500): min(len(text2), idx + 5000)]
                    found_snippets.append({
                        "url": full,
                        "snippet": snippet
                    })

                    values = re.findall(
                        r'<[^>]*enumeration[^>]*value=["\']([^"\']+)["\']',
                        snippet,
                        flags=re.IGNORECASE
                    )

                    for v in values:
                        if v not in enum_values:
                            enum_values.append(v)

        return {
            "success": True,
            "enum_name": enum_name,
            "values": enum_values,
            "visited": visited,
            "snippets_count": len(found_snippets),
            "snippets": found_snippets[:3]
        }

    except Exception as e:
        return {
            "success": False,
            "enum_name": enum_name,
            "error": str(e)
        }


@app.get("/poste/h2h/telegramma/submit-preview/{pratica_id}")
def telegramma_submit_preview(pratica_id: str):
    """
    Genera anteprima XML Submit Telegramma.
    NON invia Telegrammi.
    NON chiama service.Submit.
    NON genera costi.
    Serve solo per verificare la struttura XML prima dell'invio reale.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        mittente_data = telegramma_normalizza_dati_indirizzo(
            pratica.get("mittente") or {}
        )

        destinatario_data = telegramma_normalizza_dati_indirizzo(
            pratica.get("destinatario") or {}
        )

        testo = (
            clean_h2h_text(pratica.get("testo") or "")
            .replace("Ã™", "U'")
            .replace("Ãš", "U'")
            .replace("Ù", "U'")
            .replace("ù", "u'")
            .upper()
        )

        if not testo:
            return {
                "success": False,
                "error": "Testo Telegramma mancante",
                "pratica_id": pratica_id
            }

        client, service = telegramma_service(timeout=60)

        TelegrammaType = telegramma_find_type(
            client,
            "Telegramma",
            "Telegramma.WS"
        )

        MittenteType = telegramma_find_type(
            client,
            "Mittente",
            "Telegramma.Schema"
        )

        DestinatarioType = telegramma_find_type(
            client,
            "Destinatario",
            "Telegramma.Schema"
        )

        TelegrammaDestinatarioType = telegramma_find_type(
            client,
            "TelegrammaDestinatario",
            "Telegramma.Schema"
        )

        InfoTestoType = telegramma_find_type(
            client,
            "InfoTesto",
            "Telegramma.Schema"
        )

        OpzioniType = telegramma_find_type(
            client,
            "Opzioni",
            "Telegramma.WS"
        )

        mitt_nome, mitt_cognome = telegramma_split_nome_cognome(
            mittente_data.get("nome")
        )

        dest_nome, dest_cognome = telegramma_split_nome_cognome(
            destinatario_data.get("nome")
        )

        mittente_obj = MittenteType(
            CAP=mittente_data.get("cap"),
            Citta=mittente_data.get("comune"),
            Cognome=mitt_cognome,
            Indirizzo=mittente_data.get("indirizzo"),
            InvioAlMittente=False,
            Nome=mitt_nome,
            RagioneSociale="",
            Telefono=mittente_data.get("telefono")
        )

        destinatario_obj = DestinatarioType(
            CAP=destinatario_data.get("cap"),
            Citta=destinatario_data.get("comune"),
            Cognome=dest_cognome,
            Indirizzo=destinatario_data.get("indirizzo"),
            Nome=dest_nome,
            RagioneSociale="",
            Stato="ITALIA",
            Telefono=destinatario_data.get("telefono")
        )

        TipoRecType = telegramma_find_type(
            client,
            "TelegrammaDestinatarioTipoRec",
            "Telegramma.Schema"
        )

        telegramma_destinatario = TelegrammaDestinatarioType(
            Destinatario=destinatario_obj,
            Frazionario="",
            IDTelegramma="",
            LineaPilota="",
            NumeroDestinatarioCorrente=1,
            TipoRec=TipoRecType("Item"),
            TipoRecapitoJokid=None
        )

        info_testo = InfoTestoType(
            NumeroParteCorrente=1,
            Testo=testo
        )

        opzioni = OpzioniType(
            CTA=False,
            Note=""
        )
        
        parole = int(
            pratica.get("parole") or len(testo.split()) or 1
        )

        valorizzazione_obj = xsd.SkipValue

        pricing_plain = {
            "note": "Preventivo rimosso dal flusso come indicato da Poste",
            "preventivo_chiamato": False
        }

        id_request = str(uuid.uuid4())
        guid_message = id_request

        telegramma_obj = TelegrammaType(
            Coupon=None,
            DataTelegramma=datetime.datetime.now().replace(microsecond=0),
            Destinatari={
                "TelegrammaDestinatario": [
                    telegramma_destinatario
                ]
            },
            Firma=mittente_data.get("nome") or "",
            GUIDMessage=guid_message,
            Jokid=None,
            Mittente=mittente_obj,
            Mod60Elettronico=None,
            Nazionale=True,
            Opzioni=opzioni,
            PartiTesto=info_testo,
            TipoRecapitoMod60=None,
            TipoTelegramma="TS",
            Valorizzazione=valorizzazione_obj
        )

        message = client.create_message(
            service,
            "Submit",
            telegramma=telegramma_obj,
            Customer=POSTE_H2H_TOL_CUSTOMER,
            idRequest=id_request,
            CodiceContratto=POSTE_H2H_TOL_CONTRACT_ID
        )
        
        fix_telegramma_wsa_to(message)

        xml_string = etree.tostring(
            message,
            pretty_print=True,
            encoding="unicode"
        )

        return {
            "success": True,
            "step": "TELEGRAMMA_SUBMIT_PREVIEW",
            "pratica_id": pratica_id,
            "id_request": id_request,
            "guid_message": guid_message,
            "mittente": mittente_data,
            "destinatario": destinatario_data,
            "pricing": pricing_plain,
            "xml_preview": xml_string
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_SUBMIT_PREVIEW",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/poste/h2h/reporting/debug-operations")
def poste_reporting_debug_operations():
    try:
        history = HistoryPlugin()

        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_TOL_USERID,
            POSTE_H2H_TOL_PASSWORD
        )
        session.verify = False

        transport = Transport(session=session, timeout=60)

        client = Client(
            wsdl=POSTE_H2H_REPORTING_WSDL,
            transport=transport,
            plugins=[history]
        )

        services = []

        for service_name, service in client.wsdl.services.items():
            for port_name, port in service.ports.items():
                operations = list(port.binding._operations.keys())

                services.append({
                    "service": service_name,
                    "port": port_name,
                    "binding": str(port.binding.name),
                    "operations": operations
                })

        return {
            "success": True,
            "wsdl": POSTE_H2H_REPORTING_WSDL,
            "service_url": POSTE_H2H_REPORTING_SERVICE_URL,
            "services": services
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("//pratiche/telegramma-submit-poste/{pratica_id}")
def _telegramma_submit_poste(pratica_id: str, variant: str = ""):
    """
    Esegue il Submit reale Telegramma su Poste H2H.
    ATTENZIONE:
    - chiama davvero service.Submit
    - NON esegue PreConfirm
    - NON esegue Confirm
    - salva XML e risposta Poste

    Sicurezza:
    - sempre permesso sulla pratica tecnica #1392
    - sulle pratiche reali serve TELEGRAMMA_H2H_AUTO_ENABLED=true
    - blocca automaticamente se l'ambiente Poste è ancora sptest
    """

    history = HistoryPlugin()
    from zeep import xsd

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        stato = pratica.get("stato")

        # =====================================================
        # SICUREZZA TELEGRAMMA H2H AUTOMATICO
        # =====================================================
        # Sempre permesso sulla pratica tecnica #1392.
        # Sulle pratiche reali serve TELEGRAMMA_H2H_AUTO_ENABLED=true
        # e non deve essere ambiente sptest.
        # =====================================================

        telegramma_auto_enabled = os.getenv(
            "TELEGRAMMA_H2H_AUTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        is_pratica_tecnica = pratica_id == "525aceed-cd97-400e-9a25-49ec102078f1"

        if not is_pratica_tecnica and not telegramma_auto_enabled:
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_H2H_SUBMIT_BLOCCATO",
                "error": "Submit Telegramma H2H automatico disattivato. Imposta TELEGRAMMA_H2H_AUTO_ENABLED=true su Render.",
                "pratica_id": pratica_id,
                "order_name": pratica.get("order_name"),
                "stato": stato
            }

        telegramma_test_send_enabled = os.getenv(
            "TELEGRAMMA_H2H_TEST_SEND_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        if (
            not is_pratica_tecnica
            and "sptest" in str(POSTE_H2H_TOL_SERVICE_URL).lower()
            and not telegramma_test_send_enabled
        ):
            return {
                "success": False,
                "blocked": True,
                "step": "TELEGRAMMA_AMBIENTE_TEST_BLOCCATO",
                "error": "Ambiente Poste TEST rilevato. Per testare su sptest imposta TELEGRAMMA_H2H_TEST_SEND_ENABLED=true.",
                "service_url": POSTE_H2H_TOL_SERVICE_URL,
                "pratica_id": pratica_id
            }
        
        if stato not in [
            "PREZZATA_DA_CONFERMARE",
            "ERRORE_POSTE",
            "ERRORE_SUBMIT_POSTE",
            "SUBMIT_POSTE_OK"
        ]:
            return {
                "success": False,
                "blocked": True,
                "error": "Submit Telegramma consentito solo da PREZZATA_DA_CONFERMARE, ERRORE_POSTE, ERRORE_SUBMIT_POSTE o SUBMIT_POSTE_OK",
                "stato": stato,
                "pratica_id": pratica_id
            }

        mittente_data = telegramma_normalizza_dati_indirizzo(
            pratica.get("mittente") or {}
        )

        destinatario_data = telegramma_normalizza_dati_indirizzo(
            pratica.get("destinatario") or {}
        )

        # =====================================================
        # VARIANTI SOLO TEST SU PRATICA TECNICA #1392
        # =====================================================

        if is_pratica_tecnica and variant == "clean_address":
            mittente_data["indirizzo"] = "VIA ROMA 1"
            destinatario_data["indirizzo"] = "VIA ROMA 1"

        if is_pratica_tecnica and variant == "clean_all":
            mittente_data["nome"] = "MARIO ROSSI"
            mittente_data["indirizzo"] = "VIA ROMA 1"
            mittente_data["cap"] = "00131"
            mittente_data["comune"] = "ROMA"
            mittente_data["provincia"] = "RM"
            mittente_data["telefono"] = ""

            destinatario_data["nome"] = "LUCA BIANCHI"
            destinatario_data["indirizzo"] = "VIA ROMA 1"
            destinatario_data["cap"] = "00131"
            destinatario_data["comune"] = "ROMA"
            destinatario_data["provincia"] = "RM"
            destinatario_data["telefono"] = ""

        if is_pratica_tecnica and variant == "paderna_15":
            mittente_data["nome"] = "MARIO ROSSI"
            mittente_data["indirizzo"] = "VIA ROMA 1"
            mittente_data["cap"] = "15050"
            mittente_data["comune"] = "PADERNA"
            mittente_data["provincia"] = "AL"
            mittente_data["telefono"] = ""

            destinatario_data["nome"] = "LUCA BIANCHI"
            destinatario_data["indirizzo"] = "VIA ROMA 1"
            destinatario_data["cap"] = "15050"
            destinatario_data["comune"] = "PADERNA"
            destinatario_data["provincia"] = "AL"
            destinatario_data["telefono"] = ""

        testo = (
            clean_h2h_text(pratica.get("testo") or "")
            .replace("Ã™", "U'")
            .replace("Ãš", "U'")
            .replace("Ù", "U'")
            .replace("ù", "u'")
            .upper()
        )

        if not testo:
            return {
                "success": False,
                "error": "Testo Telegramma mancante",
                "pratica_id": pratica_id
            }

        # Test tecnico: forziamo testo controllato solo sulla pratica #1392
        if is_pratica_tecnica:
            if variant in ["15_words", "paderna_15"]:
                testo = "QUESTO E UN TEST TELEGRAMMA H2H CON QUINDICI PAROLE PER VERIFICA INTEGRAZIONE POSTE SERVIZIO ONLINE"
            else:
                testo = "TEST TELEGRAMMA H2H"

        client, service = telegramma_service(
            timeout=120,
            extra_plugins=[history]
        )

        TelegrammaType = telegramma_find_type(
            client,
            "Telegramma",
            "Telegramma.WS"
        )

        MittenteType = telegramma_find_type(
            client,
            "Mittente",
            "Telegramma.Schema"
        )

        DestinatarioType = telegramma_find_type(
            client,
            "Destinatario",
            "Telegramma.Schema"
        )

        TelegrammaDestinatarioType = telegramma_find_type(
            client,
            "TelegrammaDestinatario",
            "Telegramma.Schema"
        )

        InfoTestoType = telegramma_find_type(
            client,
            "InfoTesto",
            "Telegramma.Schema"
        )

        OpzioniType = telegramma_find_type(
            client,
            "Opzioni",
            "Telegramma.WS"
        )

        TipoRecType = telegramma_find_type(
            client,
            "TelegrammaDestinatarioTipoRec",
            "Telegramma.Schema"
        )

        mitt_nome, mitt_cognome = telegramma_split_nome_cognome(
            mittente_data.get("nome")
        )

        dest_nome, dest_cognome = telegramma_split_nome_cognome(
            destinatario_data.get("nome")
        )

        mittente_obj = MittenteType(
            CAP=mittente_data.get("cap"),
            Citta=mittente_data.get("comune"),
            Cognome=mitt_cognome,
            Indirizzo=mittente_data.get("indirizzo"),
            InvioAlMittente=False,
            Nome=mitt_nome,
            RagioneSociale="",
            Telefono=mittente_data.get("telefono")
        )

        destinatario_obj = DestinatarioType(
            CAP=destinatario_data.get("cap"),
            Citta=destinatario_data.get("comune"),
            Cognome=dest_cognome,
            Indirizzo=destinatario_data.get("indirizzo"),
            Nome=dest_nome,
            RagioneSociale="",
            Stato="ITALIA",
            Telefono=destinatario_data.get("telefono")
        )

        telegramma_destinatario = TelegrammaDestinatarioType(
            Destinatario=destinatario_obj,
            Frazionario="",
            IDTelegramma="",
            LineaPilota="",
            NumeroDestinatarioCorrente=1,
            TipoRec=TipoRecType("Item"),
            TipoRecapitoJokid=None
        )

        info_testo = InfoTestoType(
            NumeroParteCorrente=1,
            Testo=testo
        )

        opzioni = OpzioniType(
            CTA=False,
            Note=""
        )

        if is_pratica_tecnica:
            parole = len(testo.split()) or 1
        else:
            parole = int(
                pratica.get("parole") or len(testo.split()) or 1
            )

        valorizzazione_da_inviare = xsd.SkipValue

        pricing_plain = {
            "note": "Preventivo rimosso dal flusso come indicato da Poste",
            "preventivo_chiamato": False,
            "parole": parole
        }

        try:
            id_request = service.GetIdRequest()
        except Exception:
            id_request = str(uuid.uuid4())

        guid_message = id_request
        guid_message_da_inviare = id_request

        # Varianti vecchie mantenute solo per debug sulla pratica tecnica
        if is_pratica_tecnica and variant in ["no_guid", "no_guid_no_valorizzazione"]:
            try:
                from zeep import xsd
                guid_message_da_inviare = xsd.SkipValue
            except Exception:
                guid_message_da_inviare = None

        if is_pratica_tecnica and variant == "guid_equals_id":
            guid_message = id_request
            guid_message_da_inviare = id_request

        telegramma_obj = TelegrammaType(
            Coupon=None,
            DataTelegramma=datetime.datetime.now().replace(microsecond=0),
            Destinatari={
                "TelegrammaDestinatario": [
                    telegramma_destinatario
                ]
            },
            Firma=mittente_data.get("nome") or "",
            GUIDMessage=guid_message_da_inviare,
            Jokid=None,
            Mittente=mittente_obj,
            Mod60Elettronico=None,
            Nazionale=True,
            Opzioni=opzioni,
            PartiTesto=info_testo,
            TipoRecapitoMod60=None,
            TipoTelegramma="TS",
            Valorizzazione=valorizzazione_da_inviare
        )

        RecipientType = telegramma_find_type(
            client,
            "Recipient",
            "Telegramma.WS"
        )

        ArrayOfRecipientType = telegramma_find_type(
            client,
            "ArrayOfRecipient",
            "Telegramma.WS"
        )

        recipient_obj = RecipientType(
            ClientIDRecipient="1",
            Provincia=destinatario_data.get("provincia") or "",
            destinatario=destinatario_obj
        )

        recipients_obj = ArrayOfRecipientType(
            Recipient=[
                recipient_obj
            ]
        )

        validation_result = service.RecipientsValidation(
            recipients=recipients_obj,
            idRequest=id_request
        )

        validation_plain = make_json_safe(
            zeep_to_plain(validation_result)
        )

# =====================================================
# PRODUZIONE: APPLICA SUGGERIMENTO INDIRIZZO POSTE
# =====================================================
# Se Poste risponde "Invalid address. Suggestions available",
# prendiamo il primo suggerimento e ricostruiamo il destinatario.
# Questo evita l'errore produzione:
# "throw Linea Pilota exception"
# =====================================================

        try:
            validation_item = None

            if isinstance(validation_plain, list) and len(validation_plain) > 0:
                validation_item = validation_plain[0]
            elif isinstance(validation_plain, dict):
                validation_item = validation_plain

            validation_result_info = (
                (validation_item or {}).get("Result")
                or {}
            )

            validation_res_type = validation_result_info.get("ResType")
            validation_description = validation_result_info.get("Description")

            destinatari_validation = (
                (validation_item or {}).get("Destinatari")
                or {}
            )

            suggerimenti = destinatari_validation.get("Recipient") or []

            if isinstance(suggerimenti, dict):
                suggerimenti = [suggerimenti]

            if suggerimenti and len(suggerimenti) > 0:
                primo_suggerimento = suggerimenti[0] or {}

                destinatario_suggerito = (
                    primo_suggerimento.get("destinatario")
                    or {}
                )

                provincia_suggerita = primo_suggerimento.get("Provincia")

                if destinatario_suggerito:
                    destinatario_data["cap"] = (
                        destinatario_suggerito.get("CAP")
                        or destinatario_data.get("cap")
                    )

                    destinatario_data["comune"] = (
                        destinatario_suggerito.get("Citta")
                        or destinatario_data.get("comune")
                    )

                    destinatario_data["indirizzo"] = (
                        destinatario_suggerito.get("Indirizzo")
                        or destinatario_data.get("indirizzo")
                    )

                    destinatario_data["nome"] = " ".join(
                        [
                            str(destinatario_suggerito.get("Nome") or "").strip(),
                            str(destinatario_suggerito.get("Cognome") or "").strip()
                        ]
                    ).strip() or destinatario_data.get("nome")

                    destinatario_data["provincia"] = (
                        provincia_suggerita
                        or destinatario_data.get("provincia")
                    )

                    print(
                        "SUGGERIMENTO_POSTE_APPLICATO_TELEGRAMMA:",
                        destinatario_data
                    )

                    dest_nome, dest_cognome = telegramma_split_nome_cognome(
                        destinatario_data.get("nome")
                    )

                    destinatario_obj = DestinatarioType(
                        CAP=destinatario_data.get("cap"),
                        Citta=destinatario_data.get("comune"),
                        Cognome=dest_cognome,
                        Indirizzo=destinatario_data.get("indirizzo"),
                        Nome=dest_nome,
                        RagioneSociale="",
                        Stato="ITALIA",
                        Telefono=destinatario_data.get("telefono")
                    )

                    telegramma_destinatario = TelegrammaDestinatarioType(
                        Destinatario=destinatario_obj,
                        Frazionario="",
                        IDTelegramma="",
                        LineaPilota="",
                        NumeroDestinatarioCorrente=1,
                        TipoRec=TipoRecType("Item"),
                        TipoRecapitoJokid=None
                    )

                    telegramma_obj = TelegrammaType(
                        Coupon=None,
                        DataTelegramma=datetime.datetime.now().replace(microsecond=0),
                        Destinatari={
                            "TelegrammaDestinatario": [
                                telegramma_destinatario
                            ]
                        },
                        Firma=mittente_data.get("nome") or "",
                        GUIDMessage=guid_message_da_inviare,
                        Jokid=None,
                        Mittente=mittente_obj,
                        Mod60Elettronico=None,
                        Nazionale=True,
                        Opzioni=opzioni,
                        PartiTesto=info_testo,
                        TipoRecapitoMod60=None,
                        TipoTelegramma="TS",
                        Valorizzazione=valorizzazione_da_inviare
                    )

            elif validation_res_type == "E":
                now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

                poste_payload = {
                    "step": "TELEGRAMMA_VALIDAZIONE_DESTINATARIO_KO",
                    "variant": variant,
                    "customer": POSTE_H2H_TOL_CUSTOMER,
                    "note": "Validazione destinatario Poste fallita prima del Submit",        
                    "id_request": id_request,
                    "guid_message": guid_message,
                    "validation_res_type": validation_res_type,
                    "validation_description": validation_description,
                    "validation_same_id_request": validation_plain,
                    "submit_at": now_iso
                }

                supabase.table("pratiche") \
                    .update({
                        "stato": "ERRORE_SUBMIT_POSTE",
                        "id_richiesta": id_request,
                        "poste_response": poste_payload,
                        "updated_at": now_iso
                    }) \
                    .eq("id", pratica_id) \
                    .execute()

                return {
                    "success": False,
                    "step": "TELEGRAMMA_VALIDAZIONE_DESTINATARIO_KO",
                    "pratica_id": pratica_id,
                    "id_request": id_request,
                    "guid_message": guid_message,
                    "validation_same_id_request": validation_plain,
                    "error": validation_description
                }

        except Exception as suggest_err:
            print(
                "ERRORE_APPLICAZIONE_SUGGERIMENTO_POSTE:",
                str(suggest_err)
            )

        submit_result = service.Submit(
            telegramma=telegramma_obj,
            Customer=POSTE_H2H_TOL_CUSTOMER,
            idRequest=id_request,
            CodiceContratto=POSTE_H2H_TOL_CONTRACT_ID
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        plain_result = make_json_safe(
            zeep_to_plain(submit_result)
        )

        submit_result_block = plain_result.get("SubmitResult") or {}
        submit_result_info = submit_result_block.get("Result") or {}

        poste_res_type = submit_result_info.get("ResType")
        poste_description = submit_result_info.get("Description")

        submit_ok = poste_res_type in ["I", "W"]

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        poste_payload = {
            "step": "TELEGRAMMA_SUBMIT_POSTE",
            "variant": variant,
            "customer": POSTE_H2H_TOL_CUSTOMER,
            "note": "Submit Telegramma eseguito su Poste H2H. PreConfirm/Confirm non ancora eseguiti.",
            "id_request": id_request,
            "guid_message": guid_message,
            "poste_res_type": poste_res_type,
            "poste_description": poste_description,
            "submit_ok": submit_ok,
            "pricing": pricing_plain,
            "validation_same_id_request": validation_plain,
            "submit_result": plain_result,
            "raw": str(submit_result),
            "submit_at": now_iso
        }

        supabase.table("pratiche") \
            .update({
                "stato": "SUBMIT_POSTE_OK" if submit_ok else "ERRORE_SUBMIT_POSTE",
                "id_richiesta": id_request,
                "poste_response": poste_payload,
                "xml_sent": xml_sent,
                "xml_received": xml_received,
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return {
            "success": True,
            "step": "TELEGRAMMA_SUBMIT_POSTE",
            "variant": variant,
            "customer": POSTE_H2H_TOL_CUSTOMER,
            "pratica_id": pratica_id,
            "id_request": id_request,
            "guid_message": guid_message,
            "validation_same_id_request": validation_plain,
            "submit_result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            supabase.table("pratiche") \
                .update({
                    "stato": "ERRORE_SUBMIT_POSTE",
                    "poste_response": {
                        "step": "ERRORE_TELEGRAMMA_SUBMIT_POSTE",
                        "error": str(e)
                    },
                    "xml_sent": xml_sent,
                    "xml_received": xml_received,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }) \
                .eq("id", pratica_id) \
                .execute()
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_SUBMIT_POSTE",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/operations")
def poste_operations():
    try:
        client, service = poste_client(timeout=30)

        operation = client.service._binding._operations.get("InvioDoc")

        if not operation:
            return {
                "success": False,
                "error": "Operazione InvioDoc non trovata"
            }

        return {
            "success": True,
            "input": str(operation.input.signature()),
            "output": str(operation.output.signature())
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/poste/h2h/types")
def poste_types():
    try:
        client, service = poste_client(timeout=30)

        richiesta_type = client.get_type("ns1:Richiesta")
        documento_type = client.get_type("ns1:Documento")
        inviodoc_result_type = client.get_type("ns0:InvioDocResult")

        return {
            "success": True,
            "Richiesta": str(richiesta_type),
            "Documento": str(documento_type),
            "InvioDocResult": str(inviodoc_result_type)
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/poste/h2h/debug-xml")
def poste_debug_xml():
    try:
        client, service = poste_client(timeout=60)

        richiesta = {
            "IDRichiesta": "TEST-001",
            "GuidUtente": POSTE_H2H_CONTRACT_ID
        }

        documento = {
            "Immagine": "TEST",
            "MD5": "TEST",
            "Firmatari": [],
            "TipoDocumento": "PDF"
        }

        message = client.create_message(
            service,
            "InvioDoc",
            Richiesta=richiesta,
            Documento=documento
        )

        fix_wsa_to(message)

        xml_string = etree.tostring(
            message,
            pretty_print=True
        ).decode()

        return {
            "success": True,
            "xml": xml_string
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/poste/h2h/all-operations")
def poste_all_operations():
    try:
        client, service = poste_client(timeout=30)

        data = {}

        for service_name, srv in client.wsdl.services.items():

            data[service_name] = {}

            for port_name, port in srv.ports.items():

                ops = list(port.binding._operations.keys())

                data[service_name][port_name] = ops

        return {
            "success": True,
            "services": data
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/invio-signature")
def poste_invio_signature():
    try:
        client, service = poste_client(timeout=30)

        operation = client.service._binding._operations.get("Invio")

        if not operation:
            return {
                "success": False,
                "error": "Operazione Invio non trovata"
            }

        return {
            "success": True,
            "input": str(operation.input.signature()),
            "output": str(operation.output.signature())
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/rolsubmit-type")
def poste_rolsubmit_type():
    try:
        client, service = poste_client(timeout=30)

        rolsubmit_type = client.get_type("ns0:ROLSubmit")
        invio_result_type = client.get_type("ns0:InvioResult")

        return {
            "success": True,
            "ROLSubmit": str(rolsubmit_type),
            "InvioResult": str(invio_result_type)
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/rol-types-detail")
def poste_rol_types_detail():
    try:
        client, service = poste_client(timeout=30)

        types_to_check = [
            "ns0:Mittente",
            "ns0:Destinatario",
            "ns0:Documento",
            "ns0:Opzioni",
            "ns0:DatiRicevuta",
            "ns0:OpzioniDiStampa",
            "ns0:OpzioniAggiuntive",
        ]

        result = {}

        for t in types_to_check:
            try:
                result[t] = str(client.get_type(t))
            except Exception as e:
                result[t] = f"ERRORE: {str(e)}"

        return {
            "success": True,
            "types": result
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/cover-types-detail")
def poste_cover_types_detail():
    try:
        client, service = poste_client(timeout=30)

        types_to_check = [
            "ns0:DatiRicevuta",
            "ns0:TestataCover",
            "ns0:Cover",
            "ns0:CoverHeader",
            "ns0:CoverBasic",
            "ns0:CoverBody",
            "ns0:CoverFooter",
        ]

        result = {}

        for t in types_to_check:
            try:
                result[t] = str(client.get_type(t))
            except Exception as e:
                result[t] = f"ERRORE: {str(e)}"

        return {
            "success": True,
            "types": result
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/xol-types")
def poste_xol_types():
    try:
        client, service = poste_client(timeout=30)

        namespaces = {}
        for item in client.namespaces:
            namespaces[str(item)] = str(client.namespaces[item])

        xol_types = [
            "ns1:Nominativo",
            "ns1:Documento",
            "ns1:Destinatario",
            "ns1:OpzioniDiStampa",
            "ns1:OpzioniAggiuntive",
            "ns1:ArrayOfServizioAggiuntivo",
            "ns1:OpzioniAvanzate",
            "ns1:PagineBollettini",
        ]

        details = {}

        for t in xol_types:
            try:
                details[t] = str(client.get_type(t))
            except Exception as e:
                details[t] = f"ERRORE: {str(e)}"

        return {
            "success": True,
            "namespaces": namespaces,
            "types": details
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/xol-more-types")
def poste_xol_more_types():
    try:
        client, service = poste_client(timeout=30)

        xol_types = [
            "ns1:Mittente",
            "ns1:Indirizzo",
            "ns1:OpzionidiStampa",
            "ns1:ServizioAggiuntivo",
            "ns1:ArrayOfString",
        ]

        details = {}

        for t in xol_types:
            try:
                details[t] = str(client.get_type(t))
            except Exception as e:
                details[t] = f"ERRORE: {str(e)}"

        return {
            "success": True,
            "types": details
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/poste/h2h/test-submit")

def poste_test_submit():

    try:

        client, service = poste_client(timeout=60)

        Mittente = client.get_type("ns1:Mittente")

        Nominativo = client.get_type("ns1:Nominativo")

        Indirizzo = client.get_type("ns1:Indirizzo")

        Destinatario = client.get_type("ns1:Destinatario")

        Documento = client.get_type("ns1:Documento")

        OpzionidiStampa = client.get_type("ns1:OpzionidiStampa")

        ROLSubmit = client.get_type("ns0:ROLSubmit")

        indirizzo_mitt = Indirizzo(

            DUG="VIA",

            Toponimo="ROMA",

            NumeroCivico="1"

        )

        nom_mitt = Nominativo(

            Nome="TEST",

            Cognome="MITTENTE",

            CAP="00100",

            Citta="ROMA",

            Provincia="RM",

            Indirizzo=indirizzo_mitt

        )

        mittente = Mittente(

            Nominativo=nom_mitt,

            InviaStampa=False

        )

        indirizzo_dest = Indirizzo(

            DUG="VIA",

            Toponimo="MILANO",

            NumeroCivico="10"

        )

        nom_dest = Nominativo(

            Nome="TEST",

            Cognome="DESTINATARIO",

            CAP="20100",

            Citta="MILANO",

            Provincia="MI",

            Indirizzo=indirizzo_dest

        )

        destinatario = Destinatario(

            Nominativo=nom_dest

        )

        pdf_fake = base64.b64encode(

            b"%PDF-1.4 TEST PDF"

        ).decode()

        documento = Documento(

            Immagine=pdf_fake,

            TipoDocumento="PDF"

        )

        stampa = OpzionidiStampa(

            ResolutionX="300",
            ResolutionY="300",
            BW="true",
            FronteRetro="false"
            
        )

        submit = ROLSubmit(

            Mittente=mittente,
            Destinatari={"Destinatario": [destinatario]},
            NumeroDestinatari=1,
            Documento=[documento],
            Opzioni={
                "OpzionidiStampa": stampa,
                "SecurPaper": False,
                "DPM": False,
                "InserisciMittente": True,
                "Archiviazione": False,
                "FirmaElettronica": False
            },
            PrezzaturaSincrona=True,
            Nazionale="true",
            ForzaInvioDestinazioniValide=True
        )

        return {

            "success": True,

            "submit_preview": str(submit)

        }

    except Exception as e:

        return {

            "success": False,

            "error": str(e)

        }

@app.get("/poste/h2h/invio-test")
def poste_invio_test():
    require_h2h_debug_enabled()
    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        Mittente = client.get_type("ns1:Mittente")
        Nominativo = client.get_type("ns1:Nominativo")
        Indirizzo = client.get_type("ns1:Indirizzo")
        Destinatario = client.get_type("ns1:Destinatario")
        Documento = client.get_type("ns1:Documento")
        ROLSubmit = client.get_type("ns0:ROLSubmit")

        pdf_bytes = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>
endobj
trailer
<< /Root 1 0 R >>
%%EOF
"""
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        pdf_md5 = hashlib.md5(pdf_bytes).hexdigest()

        indirizzo_mitt = Indirizzo(
            DUG="VIALE",
            Toponimo="STEFANO D'ARRIGO",
            NumeroCivico="321"
        )

        nom_mitt = Nominativo(
            Nome="SALVATORE",
            Cognome="DEL LIBANO",
            CAP="00131",
            Citta="ROMA",
            Provincia="RM",
            Indirizzo=indirizzo_mitt,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        mittente = Mittente(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        indirizzo_dest = Indirizzo(
            DUG="VIA",
            Toponimo="PRAGA",
            NumeroCivico="7"
        )

        nom_dest = Nominativo(
            Nome="PIETRO",
            Cognome="DEL LIBANO",
            CAP="88842",
            Citta="CUTRO",
            Provincia="KR",
            Indirizzo=indirizzo_dest,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        destinatario = Destinatario(
            Nominativo=nom_dest
        )

        documento = Documento(
            Immagine=pdf_base64,
            MD5=pdf_md5,
            TipoDocumento="PDF"
        )

        submit = ROLSubmit(
            Mittente=mittente,
            Destinatari={
                "Destinatario": [destinatario]
            },
            NumeroDestinatari=1,
            Documento=[documento],
            Opzioni={
                "OpzionidiStampa": {
                    "ResolutionX": 300,
                    "ResolutionY": 300,
                    "BW": True,
                    "FronteRetro": False,
                    "PageSize": "A4"
                },
                "SecurPaper": False,
                "DPM": False,
                "DataStampa": datetime.datetime.now(),
                "InserisciMittente": True,
                "AnniArchiviazioneSpecified": False,
                "Archiviazione": False,
                "FirmaElettronica": False
            },
            PrezzaturaSincrona=True,
            Nazionale=True,
            ForzaInvioDestinazioniValide=False
        )

        result = service.Invio(
            IDRichiesta=str(uuid.uuid4()),
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit=submit
        )

        xml_sent = None
        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": True,
            "result": str(result),
            "xml_sent": xml_sent
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }


@app.get("/poste/h2h/valorizza-test")
def poste_valorizza_test():

    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        # DATI OTTENUTI DA INVIO V6
        id_richiesta = "c4eb8836-f2e6-4e8f-ba2e-29b4e057d9b0"
        guid_utente = "ROL202605000210302"

        # TYPE RICHIESTA
        RichiestaType = client.get_type("ns1:Richiesta")

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        # CHIAMATA VALORIZZA
        result = service.Valorizza(
            Richieste=[richiesta]
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        return {
            "success": True,
            "step": "Valorizza",
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "poste_response": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        return {
            "success": False,
            "step": "Valorizza",
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/preconferma-test")
def poste_preconferma_test():
    require_h2h_debug_enabled()
    
    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        # ==========================================
        # DATI DELLA RICHIESTA GIÀ PREZZATA
        # ==========================================

        id_richiesta = "c4eb8836-f2e6-4e8f-ba2e-29b4e057d9b0"
        guid_utente = "ROL202605000210302"

        # ==========================================
        # TYPE SOAP
        # ==========================================

        RichiestaType = client.get_type("ns1:Richiesta")

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        # ==========================================
        # PRECONFERMA
        # ==========================================

        result = service.PreConferma(
            Richieste=[richiesta],
            autoConferma=True
        )
        salva_poste_h2h_order({
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "id_ordine_poste": str(result.IdOrdine),
            "numero_raccomandata": str(
                result.DestinatariRaccomandata.ArrayOfDestinatarioRaccomandata[0].NumeroRaccomandata
            ),
            "id_ricevuta": str(
                   result.DestinatariRaccomandata.ArrayOfDestinatarioRaccomandata[0].IdRicevuta
            ),   
            "stato": "PreConfermata",
            "costo": float(result.Valorizzazione.Totale.ImportoTotale),
            "poste_response": str(result)
        })

        # ==========================================
        # XML DEBUG
        # ==========================================

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        return {
            "success": True,
            "step": "PreConferma",
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "poste_response": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        return {
            "success": False,
            "step": "PreConferma",
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/stato-test")
def poste_stato_test():

    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        id_richiesta = "c4eb8836-f2e6-4e8f-ba2e-29b4e057d9b0"

        result = service.RecuperaStatoIdRichiesta(
            IdRichiesta=id_richiesta
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        return {
            "success": True,
            "step": "RecuperaStatoIdRichiesta",
            "id_richiesta": id_richiesta,
            "poste_response": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:

        return {
            "success": False,
            "step": "RecuperaStatoIdRichiesta",
            "error": str(e)
        }

@app.get("/poste/h2h/ricevuta-test")
def poste_ricevuta_test():

    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        id_richiesta = "c4eb8836-f2e6-4e8f-ba2e-29b4e057d9b0"

        result = service.RecuperaRicevutaAccettazione(
            IDRichiesta=id_richiesta
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except:
            pass

        pdf_bytes = result["Contenuto"]

        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode("latin1")

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": "inline; filename=ricevuta_poste.pdf"
            }
        )

    except Exception as e:

        return {
            "success": False,
            "step": "RecuperaRicevutaAccettazione",
            "error": str(e)
        }

@app.get("/poste/h2h/full-cycle-v7")
def poste_full_cycle_v7():
    require_h2h_debug_enabled()

    history = HistoryPlugin()

    try:

        client, service = poste_client(
            timeout=60,
            extra_plugins=[history]
        )

        # =========================
        # TYPES
        # =========================

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")
        RichiestaType = client.get_type("ns1:Richiesta")

        # =========================
        # MITTENTE
        # =========================

        indirizzo_mitt = IndirizzoType(
            DUG="VIA",
            Toponimo="PIOBESI",
            NumeroCivico="5"
        )

        nom_mitt = NominativoType(
            Nome="VERUSKA",
            Cognome="SCAGLIONE",
            CAP="10135",
            Citta="TORINO",
            Provincia="TO",
            Indirizzo=indirizzo_mitt,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0,
            ComplementoIndirizzo=""
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        # =========================
        # DESTINATARIO
        # =========================

        indirizzo_dest = IndirizzoType(
            DUG="VIA",
            Toponimo="NEBRODI",
            NumeroCivico="2/B"
        )

        nom_dest = NominativoType(
            Nome="GIANNI",
            Cognome="RANIOLO",
            CAP="97017",
            Citta="SANTA CROCE CAMERINA",
            Provincia="RG",
            Indirizzo=indirizzo_dest,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0,
            ComplementoIndirizzo="FRAZIONE DI CASUZZE"
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        # =========================
        # PDF
        # =========================

        buffer = BytesIO()

        c = canvas.Canvas(buffer, pagesize=A4)

        c.drawString(
            100,
            750,
            "Eccomi Posta - Test FULL CYCLE V7"
        )

        c.drawString(
            100,
            720,
            "Raccomandata online Poste H2H"
        )

        c.showPage()

        c.save()

        pdf_bytes = buffer.getvalue()

        pdf_base64 = base64.b64encode(
            pdf_bytes
        ).decode("utf-8")

        md5_pdf = hashlib.md5(
            pdf_bytes
        ).hexdigest()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        # =========================
        # INVIO
        # =========================

        id_richiesta = str(uuid.uuid4())

        invio_result = service.Invio(
            IDRichiesta=id_richiesta,
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit={
                "Mittente": mittente,
                "Destinatari": {
                    "Destinatario": [destinatario]
                },
                "NumeroDestinatari": 1,
                "Documento": [documento],
                "Opzioni": {
                    "OpzionidiStampa": {
                        "ResolutionX": 300,
                        "ResolutionY": 300,
                        "BW": True,
                        "FronteRetro": False,
                        "PageSize": "A4"
                    },
                    "SecurPaper": False,
                    "DPM": False,
                    "DataStampa": datetime.datetime.now().replace(microsecond=0),
                    "InserisciMittente": True,
                    "Archiviazione": False,
                    "AnniArchiviazioneSpecified": False,
                    "FirmaElettronica": False,
                    "AnniArchiviazione": 0,
                    "ArchiviazioneDocumenti": "NESSUNA"
                },
                "PrezzaturaSincrona": False,
                "Nazionale": True,
                "ForzaInvioDestinazioniValide": True
            }
        )

        guid_utente = invio_result.GuidUtente

        # =========================
        # VALORIZZA
        # =========================

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        valorizza_result = service.Valorizza(
            Richieste=[richiesta]
        )

        # =========================
        # PRECONFERMA
        # =========================

        preconferma_result = service.PreConferma(
            Richieste=[richiesta],
            autoConferma=True
        )

        numero_racc = str(
            preconferma_result
            .DestinatariRaccomandata
            .ArrayOfDestinatarioRaccomandata[0]
            .NumeroRaccomandata
        )

        id_ricevuta = str(
            preconferma_result
            .DestinatariRaccomandata
            .ArrayOfDestinatarioRaccomandata[0]
            .IdRicevuta
        )

        costo = float(
            preconferma_result
            .Valorizzazione
            .Totale
            .ImportoTotale
        )

        # =========================
        # SALVATAGGIO DB
        # =========================

        salva_poste_h2h_order({

            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "id_ordine_poste": str(preconferma_result.IdOrdine),
            "numero_raccomandata": numero_racc,
            "id_ricevuta": id_ricevuta,
            "stato": "PreConfermata",
            "costo": costo,

            "mittente": {
                "nome": "VERUSKA",
                "cognome": "SCAGLIONE"
            },

            "destinatario": {
                "nome": "GIANNI",
                "cognome": "RANIOLO"
            },

            "poste_response": str(preconferma_result)

        })

        return {

            "success": True,
            "step": "FULL_CYCLE_COMPLETED",

            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,

            "numero_raccomandata": numero_racc,
            "id_ricevuta": id_ricevuta,

            "costo": costo,

            "message": "Raccomandata creata e salvata su Supabase"

        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }

def zeep_to_plain(obj):
    try:
        return serialize_object(obj)
    except Exception:
        if isinstance(obj, dict):
            return obj

        if isinstance(obj, list):
            return obj

        return str(obj)

def make_json_safe(obj):
    """
    Converte oggetti non JSON serializzabili:
    datetime, date, Decimal, bytes, ecc.
    Serve prima di salvare poste_response su Supabase.
    """

    import datetime as _dt
    import decimal as _decimal

    if obj is None:
        return None

    if isinstance(obj, (_dt.datetime, _dt.date)):
        return obj.isoformat()

    if isinstance(obj, _decimal.Decimal):
        return float(obj)

    if isinstance(obj, bytes):
        return obj.decode("utf-8", errors="ignore")

    if isinstance(obj, dict):
        return {
            str(k): make_json_safe(v)
            for k, v in obj.items()
        }

    if isinstance(obj, list):
        return [
            make_json_safe(v)
            for v in obj
        ]

    return obj


def parse_amount_value(value):
    if value is None or isinstance(value, bool):
        return None

    text = str(value).strip()

    if not text:
        return None

    text = (
        text.replace("€", "")
        .replace("EUR", "")
        .replace(" ", "")
        .strip()
    )

    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".")
    else:
        text = text.replace(",", ".")

    match = re.search(r"\d+(?:\.\d+)?", text)

    if not match:
        return None

    try:
        amount = float(match.group(0))

        if amount > 0:
            return amount

    except Exception:
        return None

    return None


def collect_amount_candidates(obj, path=""):
    candidates = []

    if isinstance(obj, dict):
        for key, value in obj.items():
            key_str = str(key)
            key_low = key_str.lower()
            next_path = f"{path}.{key_str}" if path else key_str

            is_amount_key = any(
                token in key_low
                for token in [
                    "importo",
                    "prezzo",
                    "costo",
                    "totale",
                    "tariffa"
                ]
            )

            if is_amount_key and not isinstance(value, (dict, list, tuple)):
                amount = parse_amount_value(value)

                if amount is not None:
                    candidates.append({
                        "path": next_path,
                        "value": amount,
                        "raw": str(value)
                    })

            candidates.extend(
                collect_amount_candidates(value, next_path)
            )

    elif isinstance(obj, (list, tuple)):
        for index, item in enumerate(obj):
            candidates.extend(
                collect_amount_candidates(item, f"{path}[{index}]")
            )

    return candidates


def estrai_costo_valorizza(valorizza_result):
    """
    Estrae il prezzo dalla risposta Poste Valorizza.
    Cerca ImportoTotale / prezzo / costo / totale anche se la struttura SOAP cambia.
    """

    try:
        plain = zeep_to_plain(valorizza_result)
        candidates = collect_amount_candidates(plain)

        if not candidates:
            print("VALORIZZA: nessun importo trovato")
            print("VALORIZZA RAW:", str(valorizza_result))
            return None

        def score(candidate):
            path = candidate.get("path", "").lower()

            if "importototale" in path:
                return 1
            if "prezzototale" in path:
                return 2
            if "costototale" in path:
                return 3
            if "totale" in path and "importo" in path:
                return 4
            if "importo" in path:
                return 5
            if "prezzo" in path:
                return 6
            if "costo" in path:
                return 7
            if "tariffa" in path:
                return 8
            if "totale" in path:
                return 9

            return 99

        candidates = sorted(candidates, key=score)

        costo = candidates[0]["value"]

        print("VALORIZZA COSTO TROVATO:", costo)
        print("VALORIZZA CANDIDATI:", candidates[:10])

        return costo

    except Exception as e:
        print("ERRORE estrai_costo_valorizza:", str(e))
        return None


def debug_costi_valorizza(valorizza_result):
    try:
        plain = zeep_to_plain(valorizza_result)
        return collect_amount_candidates(plain)[:30]
    except Exception as e:
        return [{"error": str(e)}]


@app.get("/poste/h2h/ricalcola-prezzo/{order_id}")
def ricalcola_prezzo_poste(order_id: str):
    history = HistoryPlugin()

    try:
        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            return {
                "success": False,
                "error": "Ordine H2H non trovato",
                "order_id": order_id
            }

        ordine = ordine_res.data

        id_richiesta = ordine.get("id_richiesta")
        guid_utente = ordine.get("guid_utente")

        if not id_richiesta or not guid_utente:
            return {
                "success": False,
                "error": "id_richiesta o guid_utente mancanti",
                "order_id": order_id
            }

        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        RichiestaType = client.get_type("ns1:Richiesta")

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        valorizza_result = service.Valorizza(
            Richieste=[richiesta]
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        costo_valorizzato = estrai_costo_valorizza(valorizza_result)

        poste_response_payload = {
            "step": "RICALCOLO_VALORIZZA",
            "raw": str(valorizza_result),
            "costo_valorizzato": costo_valorizzato
        }

        supabase.table("poste_h2h_orders") \
            .update({
                "stato": "PREZZATA_DA_CONFERMARE",
                "costo": costo_valorizzato,
                "poste_response": json.dumps(poste_response_payload, ensure_ascii=False),
                "xml_sent": xml_sent,
                "xml_received": xml_received
            }) \
            .eq("id", order_id) \
            .execute()

        if ordine.get("pdf_url"):
            supabase.table("pratiche") \
                .update({
                    "stato": "PREZZATA_DA_CONFERMARE",
                    "poste_response": poste_response_payload,
                    "xml_sent": xml_sent,
                    "xml_received": xml_received,
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }) \
                .eq("pdf_url", ordine.get("pdf_url")) \
                .execute()

        return RedirectResponse(
            url="//pratiche?stato=PREZZATA_DA_CONFERMARE",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RICALCOLO_PREZZO",
            "order_id": order_id,
            "error": str(e)
        }


@app.get("/poste/h2h/process-order/{order_id}")
def process_poste_order(order_id: str):

    history = HistoryPlugin()

    try:
        poste_invio_mode = os.getenv("POSTE_INVIO_MODE", "manual").strip().lower()

        if poste_invio_mode == "disabled":
            return {
                "success": False,
                "blocked": True,
                "step": "POSTE_INVIO_DISABILITATO",
                "order_id": order_id,
                "error": "Invio Poste produzione disabilitato da POSTE_INVIO_MODE."
            }

        if poste_invio_mode not in ["manual", "auto"]:
            return {
                "success": False,
                "blocked": True,
                "step": "POSTE_INVIO_MODE_NON_VALIDO",
                "order_id": order_id,
                "mode": poste_invio_mode,
                "error": "POSTE_INVIO_MODE non valido. Valori ammessi: manual, auto, disabled."
            }

        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            return {
                "success": False,
                "error": "Ordine non trovato"
            }

        ordine = ordine_res.data

        has_rr = get_ricevuta_ritorno_from_order(ordine)

        stato_ordine = ordine.get("stato")

        if stato_ordine not in ["RICEVUTO_PAGATO", "IN_LAVORAZIONE"]:
            return {
                "success": False,
                "blocked": True,
                "error": "Invio Poste bloccato: ordine non pagato o non lavorabile",
                "stato": stato_ordine,
                "order_id": order_id
            }

        pdf_url = ordine.get("pdf_url")

        if not pdf_url:
            return {
                "success": False,
                "error": "PDF non presente"
            }

        response_pdf = requests.get(pdf_url, timeout=60)

        if response_pdf.status_code != 200:
            return {
                "success": False,
                "error": "Impossibile scaricare PDF",
                "status_code": response_pdf.status_code
            }

        pdf_bytes = response_pdf.content
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest().upper()

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")
        RichiestaType = client.get_type("ns1:Richiesta")
        DatiRicevutaType = client.get_type("ns0:DatiRicevuta")

        # =====================================================
        # MITTENTE / DESTINATARIO DINAMICI DA ORDINE SUPABASE
        # =====================================================

        mittente_data = ordine.get("mittente") or {}
        destinatario_data = ordine.get("destinatario") or {}

        nom_mitt = build_nominativo_h2h_from_data(
            mittente_data,
            NominativoType,
            IndirizzoType,
            label="mittente"
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        dati_ricevuta = DatiRicevutaType(
            Nominativo=nom_mitt
        ) if has_rr else None

        nom_dest = build_nominativo_h2h_from_data(
            destinatario_data,
            NominativoType,
            IndirizzoType,
            label="destinatario"
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        # =====================================================
        # 1. Recupera ID richiesta da Poste
        # =====================================================

        id_result = service.RecuperaIdRichiesta()
        id_richiesta = id_result.IDRichiesta

        # =====================================================
        # 2. Invio tecnico a Poste per presa in carico / prezzatura
        #    ATTENZIONE: non finalizza ancora la raccomandata.
        # =====================================================

        invio_result = service.Invio(
            IDRichiesta=id_richiesta,
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit={
                "Mittente": mittente,
                **({"DatiRicevuta": dati_ricevuta} if has_rr else {}),
                "Destinatari": {
                    "Destinatario": [destinatario]
                },
                "NumeroDestinatari": 1,
                "Documento": [documento],
                "Opzioni": {
                    "OpzionidiStampa": {
                        "ResolutionX": 300,
                        "ResolutionY": 300,
                        "BW": True,
                        "FronteRetro": False,
                        "PageSize": "A4"
                    },
                    "SecurPaper": False,
                    "DPM": False,
                    "DataStampa": datetime.datetime.now().replace(microsecond=0),
                    "InserisciMittente": True,
                    "Archiviazione": False,
                    "AnniArchiviazioneSpecified": False,
                    "FirmaElettronica": False,
                    "AnniArchiviazione": 0,
                    "ArchiviazioneDocumenti": "NESSUNA"
                },
                "PrezzaturaSincrona": False,
                "Nazionale": True,
                "ForzaInvioDestinazioniValide": True
            }
        )

        xml_invio_sent = None
        xml_invio_received = None

        try:
            xml_invio_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_invio_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        guid_utente = invio_result.GuidUtente

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        # =====================================================
        # 3. Valorizza: prova a recuperare il prezzo da Poste
        # =====================================================

        valorizza_result = service.Valorizza(
            Richieste=[richiesta]
        )

        costo_valorizzato = estrai_costo_valorizza(valorizza_result)
        poste_response_text = str(valorizza_result)

        # =====================================================
        # 4. Aggiorna poste_h2h_orders
        # =====================================================

        update_h2h = {
            "stato": "PREZZATA_DA_CONFERMARE",
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "costo": costo_valorizzato,
            "poste_response": poste_response_text,
            "xml_sent": xml_invio_sent,
            "xml_received": xml_invio_received,
            "ricevuta_ritorno": has_rr
        }

        supabase.table("poste_h2h_orders") \
            .update(update_h2h) \
            .eq("id", order_id) \
            .execute()

        # =====================================================
        # 5. Aggiorna pratica  collegata
        # =====================================================

        update_pratica = {
            "stato": "PREZZATA_DA_CONFERMARE",
            "id_richiesta": id_richiesta,
            "poste_response": {
                "raw": poste_response_text,
                "costo_valorizzato": costo_valorizzato
            },
            "xml_sent": xml_invio_sent,
            "xml_received": xml_invio_received,
            "ricevuta_ritorno": has_rr,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        if ordine.get("pdf_url"):
            supabase.table("pratiche") \
                .update(update_pratica) \
                .eq("pdf_url", ordine.get("pdf_url")) \
                .execute()

        return {
            "success": True,
            "step": "PREZZATA_DA_CONFERMARE",
            "order_id": order_id,
            "shopify_order_name": ordine.get("shopify_order_name"),
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "ricevuta_ritorno": has_rr,
            "costo": costo_valorizzato,
            "message": "Ordine valorizzato. Controlla il costo e poi finalizza manualmente."
        }

    except Exception as e:

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            supabase.table("poste_h2h_orders") \
                .update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": str(e),
                    "xml_sent": xml_sent,
                    "xml_received": xml_received
                }) \
                .eq("id", order_id) \
                .execute()
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_PROCESS_ORDER",
            "order_id": order_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

def aggiorna_esito_email_raccomandata(
    h2h_order_id=None,
    pratica_id=None,
    pdf_url=None,
    data=None
):
    data = data or {}

    try:
        if h2h_order_id:
            supabase.table("poste_h2h_orders") \
                .update(data) \
                .eq("id", h2h_order_id) \
                .execute()
    except Exception as e:
        print("ERRORE UPDATE EMAIL H2H:", str(e))

    try:
        if pratica_id:
            supabase.table("pratiche") \
                .update(data) \
                .eq("id", pratica_id) \
                .execute()
        elif pdf_url:
            supabase.table("pratiche") \
                .update(data) \
                .eq("pdf_url", pdf_url) \
                .execute()
    except Exception as e:
        print("ERRORE UPDATE EMAIL PRATICA:", str(e))


def invia_email_cliente_raccomandata(
    ordine: dict,
    pratica: dict,
    pdf_cliente_url: str,
    internal_bcc_email=None
):
    """
    Invia email al cliente dopo INVIATO_POSTE.
    Protezione anti doppio invio tramite email_sent.
    La ricevuta ufficiale Poste NON viene mai inviata al cliente.
    """

    ordine = ordine or {}
    pratica = pratica or {}

    h2h_order_id = ordine.get("id")
    pratica_id = pratica.get("id")
    pdf_url = (
    pdf_cliente_url
    or ordine.get("pdf_ricevuta_cliente_url")
    or pratica.get("pdf_ricevuta_cliente_url")
    or ordine.get("pdf_url")
    or pratica.get("pdf_url")
    or ""
)

    cliente_email = (
        pratica.get("cliente_email")
        or pratica.get("email_to")
        or ordine.get("cliente_email")
        or ordine.get("email_to")
        or ""
    )

    cliente_email = str(cliente_email or "").strip().lower()

    numero_raccomandata = (
        ordine.get("numero_raccomandata")
        or pratica.get("numero_raccomandata")
        or ""
    )

    shopify_order_name = (
        ordine.get("shopify_order_name")
        or pratica.get("shopify_order_name")
        or pratica.get("order_name")
        or ordine.get("order_name")
        or ""
    )

    tipo_servizio = (
        pratica.get("tipo_servizio")
        or ordine.get("tipo_servizio")
        or "RACCOMANDATA"
    ).upper()

    is_telegramma = tipo_servizio == "TELEGRAMMA"

    subject = (
        f"Il tuo Telegramma Eccomi Posta è stato inviato - N. {numero_raccomandata}"
        if is_telegramma
        else f"La tua raccomandata Eccomi Posta è stata inviata - {numero_raccomandata}"
    )

    base_update = {
        "email_to": cliente_email,
        "email_subject": subject
    }

    gia_inviata = (
        bool_from_any(ordine.get("email_sent"))
        or bool_from_any(pratica.get("email_sent"))
    )

    if gia_inviata:
        return {
            "success": True,
            "skipped": True,
            "reason": "Email già inviata in precedenza"
        }

    if not EMAIL_RACCOMANDATA_ENABLED:
        return {
            "success": True,
            "skipped": True,
            "reason": "Invio email disattivato da ENV"
        }

    if not RESEND_API_KEY:
        errore = "RESEND_API_KEY mancante"

        aggiorna_esito_email_raccomandata(
            h2h_order_id=h2h_order_id,
            pratica_id=pratica_id,
            pdf_url=pdf_url,
            data={
                **base_update,
                "email_sent": False,
                "email_error": errore
            }
        )

        return {
            "success": False,
            "error": errore
        }

    if not cliente_email:
        errore = "Email cliente mancante"

        aggiorna_esito_email_raccomandata(
            h2h_order_id=h2h_order_id,
            pratica_id=pratica_id,
            pdf_url=pdf_url,
            data={
                **base_update,
                "email_sent": False,
                "email_error": errore
            }
        )

        return {
            "success": False,
            "error": errore
        }

    email_regex = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

    if not re.match(email_regex, cliente_email):
        errore = f"Email cliente non valida: {cliente_email}"

        aggiorna_esito_email_raccomandata(
            h2h_order_id=h2h_order_id,
            pratica_id=pratica_id,
            pdf_url=pdf_url,
            data={
                **base_update,
                "email_sent": False,
                "email_error": errore
            }
        )

        return {
            "success": False,
            "error": errore
        }

    tracking_url = ""

    if numero_raccomandata:
        tracking_url = (
            "https://www.poste.it/cerca/index.html#/risultati-spedizioni/"
            + str(numero_raccomandata)
        )

    tracking_button = ""

    if tracking_url:
        tracking_button = f"""
        <p style="margin:22px 0;">
            <a href="{tracking_url}"
               style="background:#2563eb;color:white;padding:12px 18px;
                      border-radius:10px;text-decoration:none;font-weight:bold;">
                Traccia la raccomandata
            </a>
        </p>
        """

    pdf_cliente_button = ""

    if pdf_cliente_url:
        pdf_cliente_button = f"""
        <p style="margin:22px 0;">
            <a href="{pdf_cliente_url}"
               style="background:#16a34a;color:white;padding:12px 18px;
                      border-radius:10px;text-decoration:none;font-weight:bold;">
                Scarica ricevuta Eccomi Posta
            </a>
        </p>
        """

    # Ricevuta ufficiale Poste:
    # NON deve mai essere inviata al cliente perché contiene dati interni/costi H2H.
    # Rimane disponibile solo in  e database.
    pdf_poste_button = ""

    tipo_servizio = (
        pratica.get("tipo_servizio")
        or ordine.get("tipo_servizio")
        or "RACCOMANDATA"
    ).upper()

    is_telegramma = tipo_servizio == "TELEGRAMMA"

    nome_servizio = "telegramma" if is_telegramma else "raccomandata"
    nome_servizio_titolo = "Telegramma" if is_telegramma else "Raccomandata"

    numero_label = "Numero accettazione" if is_telegramma else "Numero raccomandata"

    titolo_mail = (
        "Il tuo telegramma è stato inviato"
        if is_telegramma
        else "La tua raccomandata è stata inviata"
    )

    testo_mail = (
        "la tua pratica Eccomi Posta è stata lavorata correttamente e il telegramma è stato inviato tramite Poste Italiane."
        if is_telegramma
        else "la tua pratica Eccomi Posta è stata lavorata correttamente e la raccomandata è stata inviata tramite Poste Italiane."
    )

    if is_telegramma:
        tracking_button = ""
        
        pdf_poste_button = ""

        pdf_cliente_button = f"""
        <p style="margin:18px 0;">
            <a href="{pdf_url}"
               style="background:#15803d;color:white;padding:12px 18px;
                      border-radius:10px;text-decoration:none;font-weight:bold;
                      display:inline-block;">
                Scarica ricevuta Telegramma
            </a>
        </p>
        """

    html = f"""
    <div style="font-family:Arial,Helvetica,sans-serif;background:#f4f6f9;
                padding:24px;color:#111827;">
        <div style="max-width:640px;margin:0 auto;background:white;
                    border-radius:16px;padding:26px;">
            <h1 style="margin-top:0;color:#0f172a;">
                {titolo_mail}
            </h1>

            <p>
                Ciao,<br>
                {testo_mail}
            </p>

            <div style="background:#f8fafc;border-radius:12px;padding:16px;margin:20px 0;">
                <p><strong>Ordine:</strong> {shopify_order_name or "-"}</p>
                <p><strong>{numero_label}:</strong> {numero_raccomandata or "-"}</p>
            </div>

            {tracking_button}
            {pdf_cliente_button}
            {pdf_poste_button}

            <hr style="border:none;border-top:1px solid #e5e7eb;margin:26px 0;">

            <p>
                Hai bisogno di inviare un nuovo documento, una raccomandata,
                un telegramma, Posta1 o Posta4?
            </p>

            <p style="margin:22px 0;">
                <a href="{ECCOMI_POSTA_CTA_URL}"
                   style="background:#f97316;color:white;padding:12px 18px;
                          border-radius:10px;text-decoration:none;font-weight:bold;">
                    Vai a Eccomi Posta
                </a>
            </p>

            <p style="font-size:12px;color:#6b7280;margin-top:28px;">
                Eccomi Posta — Servizi postali digitali<br>
                www.eccomionline.com
            </p>
        </div>
    </div>
    """

    try:
        email_payload = {
            "from": FROM_EMAIL,
            "to": [cliente_email],
            "subject": subject,
            "html": html
        }

        if internal_bcc_email:
            email_payload["bcc"] = [internal_bcc_email]

        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json=email_payload,
            timeout=30
        )

        response_text = response.text

        try:
            response_json = response.json()
        except Exception:
            response_json = {}

        resend_email_id = (
            response_json.get("id")
            or response_json.get("data", {}).get("id")
            or ""
        )

        if response.status_code < 200 or response.status_code >= 300:
            errore = f"Errore Resend {response.status_code}: {response_text}"

            aggiorna_esito_email_raccomandata(
                h2h_order_id=h2h_order_id,
                pratica_id=pratica_id,
                pdf_url=pdf_url,
                data={
                    **base_update,
                    "email_sent": False,
                    "email_error": errore,
                    "email_status": "failed",
                    "email_last_event": "email.failed",
                    "email_last_event_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }
            )

            return {
                "success": False,
                "error": errore
            }

        resend_id = resend_email_id

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        aggiorna_esito_email_raccomandata(
            h2h_order_id=h2h_order_id,
            pratica_id=pratica_id,
            pdf_url=pdf_url,
            data={
                **base_update,
                "email_sent": True,
                "email_sent_at": now_iso,
                "email_error": None,
                "email_status": "sent",
                "email_last_event": "email.sent",
                "email_last_event_at": now_iso,
                "email_resend_id": resend_id
            }
        )

        return {
            "success": True,
            "email_to": cliente_email,
            "resend_id": resend_id
        }

    except Exception as e:
        errore = str(e)

        aggiorna_esito_email_raccomandata(
            h2h_order_id=h2h_order_id,
            pratica_id=pratica_id,
            pdf_url=pdf_url,
            data={
                **base_update,
                "email_sent": False,
                "email_error": errore,
                "email_status": "failed",
                "email_last_event": "email.failed",
                "email_last_event_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }
        )

        return {
            "success": False,
            "error": errore
        }

@app.get("/poste/h2h/finalizza/{order_id}")
def confirm_poste_order(order_id: str):

    history = HistoryPlugin()

    try:
        poste_invio_mode = os.getenv("POSTE_INVIO_MODE", "manual").strip().lower()

        if poste_invio_mode == "disabled":
            return {
                "success": False,
                "blocked": True,
                "step": "POSTE_FINALIZZA_DISABILITATO",
                "order_id": order_id,
                "error": "Finalizzazione Poste produzione disabilitata da POSTE_INVIO_MODE."
            }

        if poste_invio_mode not in ["manual", "auto"]:
            return {
                "success": False,
                "blocked": True,
                "step": "POSTE_INVIO_MODE_NON_VALIDO",
                "order_id": order_id,
                "mode": poste_invio_mode,
                "error": "POSTE_INVIO_MODE non valido. Valori ammessi: manual, auto, disabled."
            }

        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            return {
                "success": False,
                "error": "Ordine non trovato"
            }

        ordine = ordine_res.data
        
        # =====================================================
        # BLOCCO SICUREZZA: evita doppia finalizzazione Poste
        # =====================================================

        stato_corrente = ordine.get("stato")
        numero_esistente = ordine.get("numero_raccomandata")

        if stato_corrente in ["INVIATO_POSTE", "RICEVUTA_SALVATA", "COMPLETATO"] or numero_esistente:
            return {
                "success": False,
                "blocked": True,
                "error": "Pratica già inviata a Poste: finalizzazione bloccata",
                "stato": stato_corrente,
                "numero_raccomandata": numero_esistente,
                "order_id": order_id
            }

        if stato_corrente != "PREZZATA_DA_CONFERMARE":
            return {
                "success": False,
                "blocked": True,
                "error": "Finalizzazione consentita solo per pratiche PREZZATA_DA_CONFERMARE",
                "stato": stato_corrente,
                "order_id": order_id
            }

        id_richiesta = ordine.get("id_richiesta")
        guid_utente = ordine.get("guid_utente")

        if not id_richiesta or not guid_utente:
            return {
                "success": False,
                "error": "id_richiesta o guid_utente mancanti"
            }

        RichiestaType = client.get_type("ns1:Richiesta")

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        pre_result = service.PreConferma(
            Richieste=[richiesta],
            autoConferma=True
        )

        if not pre_result.DestinatariRaccomandata:
            return {
                "success": False,
                "error": "PreConferma senza DestinatariRaccomandata",
                "id_richiesta": id_richiesta,
                "guid_utente": guid_utente,
                "preconferma_response": str(pre_result)
            }

        numero_racc = str(
            pre_result.DestinatariRaccomandata
            .ArrayOfDestinatarioRaccomandata[0]
            .NumeroRaccomandata
        )

        id_ricevuta = str(
            pre_result.DestinatariRaccomandata
            .ArrayOfDestinatarioRaccomandata[0]
            .IdRicevuta
        )

        costo = float(
            pre_result.Valorizzazione.Totale.ImportoTotale
        )

        pdf_cliente = genera_pdf_cliente_eccomi_posta(
            numero_raccomandata=numero_racc,
            mittente=ordine.get("mittente", {}).get("raw", ""),
            destinatario=ordine.get("destinatario", {}).get("raw", "")
        )

        cliente_pdf_path = f"ricevute-clienti/{order_id}/ricevuta_cliente.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            cliente_pdf_path,
            pdf_cliente,
            {
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        cliente_pdf_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            cliente_pdf_path
        )

        update_h2h_finale = {
            "stato": "INVIATO_POSTE",
            "numero_raccomandata": numero_racc,
            "id_ricevuta": id_ricevuta,
            "id_ordine_poste": str(pre_result.IdOrdine),
            "costo": costo,
            "poste_response": str(pre_result),
            "pdf_ricevuta_cliente_url": cliente_pdf_url
        }

        supabase.table("poste_h2h_orders") \
            .update(update_h2h_finale) \
            .eq("id", order_id) \
            .execute()

        pratica_collegata = {}

        if ordine.get("pdf_url"):
            pratica_res = supabase.table("pratiche") \
                .select("*") \
                .eq("pdf_url", ordine.get("pdf_url")) \
                .limit(1) \
                .execute()

            if pratica_res.data:
                pratica_collegata = pratica_res.data[0]

        update_pratica_finale = {
            "numero_raccomandata": numero_racc,
            "stato": "INVIATO_POSTE",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        if ordine.get("pdf_url"):
            supabase.table("pratiche") \
                .update(update_pratica_finale) \
                .eq("pdf_url", ordine.get("pdf_url")) \
                .execute()

        if pratica_collegata:
            pratica_collegata.update(update_pratica_finale)

        ordine_email = dict(ordine)
        ordine_email.update(update_h2h_finale)
        ordine_email["id"] = order_id

        email_result = {
            "success": False,
            "skipped": True,
            "reason": "Funzione email non disponibile"
        }

        email_fn = globals().get("invia_email_cliente_raccomandata")

        if callable(email_fn):
            email_result = email_fn(
                ordine=ordine_email,
                pratica=pratica_collegata,
                pdf_cliente_url=cliente_pdf_url
            )

        return {
            "success": True,
            "step": "INVIATO_POSTE",
            "order_id": order_id,
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "numero_raccomandata": numero_racc,
            "id_ricevuta": id_ricevuta,
            "costo": costo,
            "pdf_cliente_url": cliente_pdf_url,
            "email": email_result,
            "message": "Ordine confermato, raccomandata generata, PDF cliente salvato ed email cliente gestita"
        }

    except Exception as e:

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_FINALIZZA_POSTE",
            "order_id": order_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }


@app.get("/poste/h2h/salva-ricevuta/{order_id}")
def salva_ricevuta_poste(order_id: str):

    try:
        ordine = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", order_id) \
            .single() \
            .execute()

        if not ordine.data:
            return {"success": False, "error": "Ordine non trovato"}

        ordine = ordine.data
        id_richiesta = ordine.get("id_richiesta")

        if not id_richiesta:
            return {"success": False, "error": "id_richiesta mancante"}

        history = HistoryPlugin()
        client, service = poste_client(timeout=120, extra_plugins=[history])

        result = service.RecuperaRicevutaAccettazione(
            IDRichiesta=id_richiesta
        )

        pdf_bytes = result["Contenuto"]

        if isinstance(pdf_bytes, str):
            pdf_bytes = pdf_bytes.encode("latin1")

        file_path = f"ricevute/{order_id}/ricevuta_accettazione.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            file_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(file_path)

        supabase.table("poste_h2h_orders") \
            .update({
                "pdf_ricevuta_url": pdf_public_url,
                "stato": "RICEVUTA_SALVATA"
            }) \
            .eq("id", order_id) \
            .execute()

        return {
            "success": True,
            "order_id": order_id,
            "id_richiesta": id_richiesta,
            "pdf_ricevuta_url": pdf_public_url,
            "message": "Ricevuta Poste salvata correttamente"
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/valida-destinatari-test")
def valida_destinatari_test():
    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        Nominativo = client.get_type("ns1:Nominativo")
        Indirizzo = client.get_type("ns1:Indirizzo")
        Destinatario = client.get_type("ns1:Destinatario")

        indirizzo_dest = Indirizzo(
            DUG="VIA",
            Toponimo="NAZIONALE",
            NumeroCivico="1"
        )

        nom_dest = Nominativo(
            Nome="MARIO",
            Cognome="ROSSI",
            CAP="00184",
            Citta="ROMA",
            Provincia="RM",
            Indirizzo=indirizzo_dest,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        destinatario = Destinatario(
            Nominativo=nom_dest
        )

        result = service.ValidaDestinatari(
            IDRichiesta=str(uuid.uuid4()),
            Destinatari={
                "Destinatario": [destinatario]
            }
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": True,
            "result": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/debug-invio-xml")
def debug_invio_xml():
    try:
        client, service = poste_client(timeout=60)

        Mittente = client.get_type("ns1:Mittente")
        Nominativo = client.get_type("ns1:Nominativo")
        Indirizzo = client.get_type("ns1:Indirizzo")
        Destinatario = client.get_type("ns1:Destinatario")
        Documento = client.get_type("ns1:Documento")
        ROLSubmit = client.get_type("ns0:ROLSubmit")

        indirizzo_mitt = Indirizzo(DUG="VIA", Toponimo="ROMA", NumeroCivico="1")
        nom_mitt = Nominativo(
            Nome="TEST",
            Cognome="MITTENTE",
            CAP="00100",
            Citta="ROMA",
            Provincia="RM",
            Indirizzo=indirizzo_mitt,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        mittente = Mittente(Nominativo=nom_mitt, InviaStampa=False)

        indirizzo_dest = Indirizzo(DUG="VIA", Toponimo="MILANO", NumeroCivico="10")
        nom_dest = Nominativo(
            Nome="TEST",
            Cognome="DESTINATARIO",
            CAP="20100",
            Citta="MILANO",
            Provincia="MI",
            Indirizzo=indirizzo_dest,
            TipoIndirizzo="NORMAL",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        destinatario = Destinatario(Nominativo=nom_dest)

        pdf_fake = base64.b64encode(b"%PDF-1.4 TEST PDF").decode()

        documento = Documento(
            Immagine=pdf_fake,
            TipoDocumento="PDF"
        )

        submit = ROLSubmit(
            Mittente=mittente,
            Destinatari={"Destinatario": [destinatario]},
            NumeroDestinatari=1,
            Documento=[documento],
            Opzioni={
                "OpzionidiStampa": {
                    "ResolutionX": "300",
                    "ResolutionY": "300",
                    "BW": "true",
                    "FronteRetro": "false",
                    "PageSize": "A4"
                },
                "SecurPaper": False,
                "DPM": False,
                "DataStampa": datetime.datetime.now(),
                "InserisciMittente": True,
                "AnniArchiviazioneSpecified": False,
                "Archiviazione": False,
                "FirmaElettronica": False
            },
            PrezzaturaSincrona=True,
            Nazionale="true",
            ForzaInvioDestinazioniValide=True
        )

        message = client.create_message(
            service,
            "Invio",
            IDRichiesta="TEST-INVIO-001",
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit=submit
        )

        fix_wsa_to(message)

        return {
            "success": True,
            "xml": etree.tostring(message, pretty_print=True).decode()
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/poste/h2h/tipo-indirizzo-values")
def poste_tipo_indirizzo_values():
    try:
        client, service = poste_client(timeout=30)

        result = {}

        checks = [
            "ns1:NominativoTipoIndirizzo",
            "ns0:NominativoTipoIndirizzo",
            "ns1:TipoIndirizzo",
            "ns0:TipoIndirizzo",
        ]

        for item in checks:
            try:
                result[item] = str(client.get_type(item))
            except Exception as e:
                result[item] = f"ERRORE: {str(e)}"

        found = []
        for t in client.wsdl.types.types:
            text = str(t)
            if "NominativoTipoIndirizzo" in text or "TipoIndirizzo" in text:
                found.append(text)

        return {
            "success": True,
            "checks": result,
            "found": found
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/poste/h2h/send-test")
def poste_send_test():
    try:
        pdf_path = "data/test.pdf"

        if not os.path.exists(pdf_path):
            return {
                "success": False,
                "error": "File data/test.pdf non trovato"
            }

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        md5_hash = hashlib.md5(pdf_bytes).hexdigest()
        pdf_base64 = base64.b64encode(pdf_bytes).decode()

        client, service = poste_client(timeout=60)

        richiesta = {
            "IDRichiesta": "TEST-001",
            "GuidUtente": POSTE_H2H_CONTRACT_ID
        }

        documento = {
            "Immagine": pdf_base64,
            "MD5": md5_hash,
            "Firmatari": [],
            "TipoDocumento": "PDF"
        }

        result = service.InvioDoc(
            Richiesta=richiesta,
            Documento=documento
        )

        return {
            "success": True,
            "result": str(result)
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

def clean_h2h_text(value):
    return (value or "") \
        .replace("’", "'") \
        .replace("‘", "'") \
        .replace("“", '"') \
        .replace("”", '"') \
        .replace("–", "-") \
        .replace("—", "-") \
        .strip()


def format_indirizzo_blocco(testo):
    testo = testo or ""

    if " - " in testo:
        nome, resto = testo.split(" - ", 1)
    else:
        nome, resto = testo, ""

    via = resto
    cap_citta = ""

    if "," in resto:
        via, cap_citta = resto.split(",", 1)

    return [
        nome.strip(),
        via.strip(),
        cap_citta.strip()
    ]


def genera_pdf_da_testo(pdf_path, mittente, destinatario, oggetto, testo, firma):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    left = 2.7 * cm
    right = width - 2.7 * cm
    y = height - 2.8 * cm

    def draw_lines(lines, x, y, size=10.5, bold_first=True):
        for i, line in enumerate(lines):
            if not line:
                continue
            c.setFont("Times-Bold" if i == 0 and bold_first else "Times-Roman", size)
            c.drawString(x, y, line)
            y -= 0.48 * cm
        return y

    def draw_wrapped(text, x, y, max_width, size=11, line_height=0.62 * cm):
        c.setFont("Times-Roman", size)

        for raw_line in (text or "").split("\n"):
            words = raw_line.split()
            line = ""

            if not words:
                y -= line_height
                continue

            for word in words:
                test = f"{line} {word}".strip()

                if c.stringWidth(test, "Times-Roman", size) <= max_width:
                    line = test
                else:
                    c.drawString(x, y, line)
                    y -= line_height
                    line = word

                    if y < 3 * cm:
                        c.showPage()
                        y = height - 2.8 * cm
                        c.setFont("Times-Roman", size)

            if line:
                c.drawString(x, y, line)
                y -= line_height

        return y

    y = draw_lines(format_indirizzo_blocco(mittente), left, y, size=10.5)
    y -= 1.0 * cm

    c.setFont("Times-Roman", 10.5)
    c.drawRightString(right, y, f"Roma, {datetime.datetime.now().strftime('%d/%m/%Y')}")
    y -= 1.5 * cm

    y = draw_lines(format_indirizzo_blocco(destinatario), left, y, size=10.5)
    y -= 1.0 * cm

    if oggetto:
        c.setFont("Times-Bold", 11)
        c.drawString(left, y, "OGGETTO:")
        c.setFont("Times-Roman", 11)
        c.drawString(left + 2.5 * cm, y, oggetto.upper())
        y -= 1.2 * cm

    y = draw_wrapped(
        testo or "",
        left,
        y,
        right - left,
        size=10.8,
        line_height=0.68 * cm
    )

    y -= 1.2 * cm

    if y < 5 * cm:
        c.showPage()
        y = height - 2.8 * cm

    c.setFont("Times-Roman", 10.5)
    c.drawRightString(right, y, "Distinti saluti")
    y -= 0.7 * cm

    if firma:
        c.setFont("Times-Italic", 13)
        c.drawRightString(right, y, firma)

    c.save()

def parse_bool(value):
    if isinstance(value, bool):
        return value

    if value is None:
        return False

    return str(value).strip().lower() in [
        "true",
        "1",
        "yes",
        "y",
        "si",
        "sì",
        "on"
    ]
def normalizza_cap(value):
    return re.sub(r"\D", "", str(value or ""))[:5]


def normalizza_provincia(value):
    return re.sub(r"[^A-Za-z]", "", str(value or "")).upper()[:2]


def split_via_civico_from_text(indirizzo):
    """
    Gestisce:
    - Viale Stefano D'Arrigo 321
    - Viale Stefano D'Arrigo, 321
    - Via Praga 7
    - Via Praga, 7
    - Piazza Trilussa, 3
    - Via Nebrodi 2/B
    """

    indirizzo = str(indirizzo or "").strip().strip(",")

    if not indirizzo:
        return "", ""

    # Caso con virgola: "Via Praga, 7"
    parts = [p.strip() for p in indirizzo.split(",") if p.strip()]

    if len(parts) >= 2:
        possibile_civico = parts[-1]

        if re.fullmatch(r"[0-9]+[A-Za-z0-9\/\-]*", possibile_civico):
            via = ", ".join(parts[:-1]).strip()
            return via, possibile_civico

    # Caso senza virgola: "Via Praga 7" / "Via Nebrodi 2/B"
    match = re.match(
        r"^(.*?)[\s]+([0-9]+[A-Za-z0-9\/\-]*)$",
        indirizzo
    )

    if match:
        via = match.group(1).strip().strip(",")
        civico = match.group(2).strip().strip(",")
        return via, civico

    return indirizzo, ""


def estrai_dati_rubrica_da_raw(raw):
    raw = str(raw or "").strip()

    nome = raw
    resto = ""

    via = ""
    civico = ""
    cap = ""
    comune = ""
    provincia = ""

    if " - " in raw:
        nome, resto = raw.split(" - ", 1)
    else:
        resto = ""

    nome = nome.strip()

    # Cerca la località finale:
    # "00131 Roma (RM)"
    # "00131 Roma, RM"
    # "88842 Cutro KR"
    localita_match = re.search(
        r"\b(\d{5})\s+(.+?)(?:\s*[\(,]?\s*([A-Z]{2})\s*\)?)?\s*$",
        resto,
        flags=re.IGNORECASE
    )

    if localita_match:
        cap = normalizza_cap(localita_match.group(1))
        comune = str(localita_match.group(2) or "").strip().strip(",")
        provincia = normalizza_provincia(localita_match.group(3))

        indirizzo_part = resto[:localita_match.start()].strip().strip(",")

    else:
        indirizzo_part = resto.strip().strip(",")

    via, civico = split_via_civico_from_text(indirizzo_part)

    return {
        "nome": nome.strip(),
        "via": via.strip(),
        "civico": civico.strip(),
        "cap": cap.strip(),
        "comune": comune.strip(),
        "provincia": provincia.strip().upper()[:2]
    }


def salva_rubrica_posta_da_raccomandata(cliente_email, mittente, destinatario):
    cliente_email = str(cliente_email or "").strip().lower()

    if not cliente_email:
        print("Rubrica Posta: cliente_email assente, salvataggio saltato")
        return

    items = [
        {
            "tipo": "mittente",
            "raw": mittente or ""
        },
        {
            "tipo": "destinatario",
            "raw": destinatario or ""
        }
    ]

    for item in items:
        raw = str(item["raw"] or "").strip()

        if len(raw) < 5:
            continue

        dati = estrai_dati_rubrica_da_raw(raw)

        if (
            not dati["nome"]
            or not dati["via"]
            or not dati["cap"]
            or not dati["comune"]
            or not dati["provincia"]
        ):
            print("Rubrica Posta: dati incompleti, riga saltata:", dati)
            continue

        payload = {
            "shopify_customer_id": "",
            "customer_id": "",
            "customer_email": cliente_email,
            "email": cliente_email,
            "tipo": item["tipo"],
            "nome": dati["nome"],
            "via": dati["via"],
            "civico": dati["civico"],
            "cap": dati["cap"],
            "comune": dati["comune"],
            "provincia": dati["provincia"]
        }

        try:
            supabase.table("rubrica_posta").insert(payload).execute()
            print("Rubrica Posta salvata:", payload)

        except Exception as e:
            errore = str(e).lower()

            if "duplicate key" in errore or "23505" in errore:
                print("Rubrica Posta: contatto già presente, ignorato:", payload)
                continue

            print("ERRORE SALVATAGGIO RUBRICA POSTA:", str(e))


@app.post("/raccomandata")
async def crea_raccomandata(
    order_id: str = Form(...),
    mittente: str = Form(...),
    destinatario: str = Form(...),
    cliente_email: str = Form(None),
    testo: str = Form(None),
    oggetto: str = Form(None),
    firma: str = Form(None),
    pagine: str = Form(None),
    ricevuta_ritorno: str = Form("false"),
    metodo: str = Form(None),
    file: UploadFile = File(None),
):
    try:

        ricevuta_ritorno_bool = parse_bool(ricevuta_ritorno)
        now = datetime.datetime.now()
        anno = now.year
        timestamp = now.strftime("%d/%m/%Y %H:%M:%S")

        token = f"RACC-{anno}-{order_id}"

        pratica_dir = f"data/{token}"

        os.makedirs(pratica_dir, exist_ok=True)

        pratica_path = f"{pratica_dir}/pratica.txt"

        pdf_path = f"{pratica_dir}/documento.pdf"

        with open(pratica_path, "w", encoding="utf-8") as f:

            f.write(f"TOKEN: {token}\n")
            f.write(f"DATA CREAZIONE: {timestamp}\n")
            f.write(f"ORDER ID: {order_id}\n")
            f.write("STATO: BOZZA_CHECKOUT\n\n")

            f.write(f"METODO: {metodo}\n")
            f.write(f"OGGETTO: {oggetto}\n")
            f.write(f"PAGINE: {pagine}\n")
            f.write(f"RICEVUTA DI RITORNO: {ricevuta_ritorno}\n")
            f.write(f"FIRMA: {firma}\n\n")

            f.write("MITTENTE:\n")
            f.write(f"{mittente}\n\n")

            f.write("DESTINATARIO:\n")
            f.write(f"{destinatario}\n\n")

            if testo:
                f.write("TESTO RACCOMANDATA:\n")
                f.write(testo)

        # =========================
        # PDF
        # =========================

        if file:

            contents = await file.read()

            with open(pdf_path, "wb") as f:
                f.write(contents)

            pdf_saved = True

        else:

            genera_pdf_da_testo(
                pdf_path=pdf_path,
                mittente=mittente,
                destinatario=destinatario,
                oggetto=oggetto,
                testo=testo or "",
                firma=firma or "",
            )

            pdf_saved = True

        # =========================
        # UPLOAD SUPABASE STORAGE
        # =========================

        storage_path = f"raccomandate/{token}/documento.pdf"

        with open(pdf_path, "rb") as f:

            supabase.storage.from_(SUPABASE_BUCKET).upload(
                path=storage_path,
                file=f,
                file_options={
                    "content-type": "application/pdf",
                    "upsert": "true",
                },
            )

        pdf_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            storage_path
        )

        # =========================
        # SALVATAGGIO PRATICA
        # =========================

        cliente_email_safe = cliente_email or ""

        try:
            supabase.table("pratiche").insert({
                "order_id": str(order_id),
                "order_name": str(order_id),
                "shopify_order_name": str(order_id),
                "tipo_servizio": "RACCOMANDATA",
                "cliente_email": cliente_email_safe,
                "mittente": {
                    "raw": mittente
                },
                "destinatario": {
                    "raw": destinatario
                },
                "testo": testo or "",
                "parole": 0,
                "pdf_url": pdf_url,
                "stato": "BOZZA_CHECKOUT",
                "ricevuta_ritorno": ricevuta_ritorno_bool
            }).execute()

        except Exception as db_error:
            print(
                "ERRORE SALVATAGGIO PRATICA RACCOMANDATA:",
                str(db_error)
            )

        # =========================
        # SALVATAGGIO RUBRICA POSTA
        # =========================

        try:
            salva_rubrica_posta_da_raccomandata(
                cliente_email=cliente_email_safe,
                mittente=mittente,
                destinatario=destinatario
            )

        except Exception as rubrica_error:
            print(
                "ERRORE SALVATAGGIO RUBRICA POSTA:",
                str(rubrica_error)
            )

        return {
            "success": True,
            "token": token,
            "pdf_saved": pdf_saved,
            "folder": pratica_dir,
            "pdf_url": pdf_url,
            "stato": "BOZZA_CHECKOUT"
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def supabase_rubrica_headers():
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation"
    }


@app.get("/rubrica-posta")
async def get_rubrica_posta(email: str = "", customer_id: str = ""):
    try:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "Supabase non configurato"
                }
            )

        if not email and not customer_id:
            return {
                "success": True,
                "items": []
            }

        params = {
            "select": "*",
            "order": "created_at.desc"
        }

        if email:
            params["email"] = f"eq.{email}"

        if customer_id:
            params["customer_id"] = f"eq.{customer_id}"

        response = requests.get(
            f"{SUPABASE_URL}/rest/v1/rubrica_posta",
            headers=supabase_rubrica_headers(),
            params=params,
            timeout=20
        )

        if response.status_code >= 400:
            return JSONResponse(
                status_code=response.status_code,
                content={
                    "success": False,
                    "error": response.text
                }
            )

        return {
            "success": True,
            "items": response.json()
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )


@app.post("/rubrica-posta")
async def save_rubrica_posta(request: Request):
    try:
        if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
            return JSONResponse(
                status_code=500,
                content={
                    "success": False,
                    "error": "Supabase non configurato"
                }
            )

        data = await request.json()

        email = str(data.get("email") or "").strip()
        customer_id = str(data.get("customer_id") or "").strip()
        tipo = str(data.get("tipo") or "").strip().lower()

        nome = str(data.get("nome") or "").strip()
        via = str(data.get("via") or "").strip()
        civico = str(data.get("civico") or "").strip()
        cap = str(data.get("cap") or "").strip()
        comune = str(data.get("comune") or "").strip()
        provincia = str(data.get("provincia") or "").strip().upper()

        if not email and not customer_id:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Email o customer_id obbligatorio"
                }
            )

        if tipo not in ["mittente", "destinatario"]:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Tipo non valido"
                }
            )

        if not nome or not via or not cap or not comune or not provincia:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Dati rubrica incompleti"
                }
            )

        payload = {
            "email": email,
            "customer_id": customer_id,
            "tipo": tipo,
            "nome": nome,
            "via": via,
            "civico": civico,
            "cap": cap,
            "comune": comune,
            "provincia": provincia
        }

        response = requests.post(
            f"{SUPABASE_URL}/rest/v1/rubrica_posta",
            headers=supabase_rubrica_headers(),
            json=payload,
            timeout=20
        )

        if response.status_code >= 400:
            error_text = response.text or ""

            if "duplicate key" in error_text.lower() or "23505" in error_text:
                return {
                    "success": True,
                    "duplicate": True,
                    "item": payload
                }

            return JSONResponse(
                status_code=response.status_code,
                content={
                    "success": False,
                    "error": error_text
                }
            )

        try:
            saved = response.json()
        except Exception:
            saved = []

        return {
            "success": True,
            "item": saved[0] if saved else payload
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e)
            }
        )


@app.get("/raccomandata/{token}/pdf")
async def scarica_pdf(token: str):
    pdf_path = f"data/{token}/documento.pdf"

    if not os.path.exists(pdf_path):
        return {"success": False, "error": "PDF non trovato"}

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=f"{token}.pdf",
    )

def genera_pdf_telegramma(pdf_path, telegramma):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4

    mitt = telegramma.get("mittente", {})
    dest = telegramma.get("destinatario", {})
    testo = telegramma.get("testo", "")

    y = height - 2.5 * cm

    c.setFont("Times-Bold", 12)
    c.drawCentredString(width / 2, y, "TELEGRAMMA")
    y -= 1.5 * cm

    c.setFont("Times-Bold", 10)
    c.drawCentredString(width / 2, y, "DESTINATARIO")
    y -= 0.6 * cm

    c.setFont("Times-Roman", 10)
    c.drawCentredString(width / 2, y, dest.get("nome", ""))
    y -= 0.5 * cm
    c.drawCentredString(width / 2, y, f'{dest.get("via", "")} {dest.get("civico", "")}')
    y -= 0.5 * cm
    c.drawCentredString(width / 2, y, f'{dest.get("cap", "")} {dest.get("comune", "")} ({dest.get("provincia", "")})')
    y -= 1.2 * cm

    c.line(2 * cm, y, width - 2 * cm, y)
    y -= 0.9 * cm

    c.setFont("Times-Bold", 11)
    text_obj = c.beginText(2.5 * cm, y)
    text_obj.setFont("Times-Bold", 11)

    words = testo.upper().split()
    line = ""
    max_chars = 72

    for word in words:
        test_line = f"{line} {word}".strip()
        if len(test_line) <= max_chars:
            line = test_line
        else:
            text_obj.textLine(line)
            line = word

    if line:
        text_obj.textLine(line)

    c.drawText(text_obj)

    y = text_obj.getY() - 1 * cm
    c.line(2 * cm, y, width - 2 * cm, y)
    y -= 1 * cm

    c.setFont("Times-Bold", 10)
    c.drawCentredString(width / 2, y, "MITTENTE")
    y -= 0.6 * cm

    c.setFont("Times-Roman", 10)
    c.drawCentredString(width / 2, y, mitt.get("nome", ""))
    y -= 0.5 * cm
    c.drawCentredString(width / 2, y, f'{mitt.get("via", "")} {mitt.get("civico", "")}')
    y -= 0.5 * cm
    c.drawCentredString(width / 2, y, f'{mitt.get("cap", "")} {mitt.get("comune", "")} ({mitt.get("provincia", "")})')

    c.save()

@app.get("//pratiche/telegramma-pdf/{pratica_id}")
def _telegramma_pdf(pratica_id: str):
    """
    Genera il PDF cliente del Telegramma partendo dalla pratica salvata.
    NON chiama Poste.
    NON genera costi.
    Serve per lavorazione manuale e archivio cliente.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica Telegramma non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        telegramma = {
            "mittente": pratica.get("mittente") or {},
            "destinatario": pratica.get("destinatario") or {},
            "testo": pratica.get("testo") or ""
        }

        order_name_clean = str(
            pratica.get("shopify_order_name")
            or pratica.get("order_name")
            or pratica_id
        ).replace("#", "").replace("/", "-")

        os.makedirs("data/telegrammi_pdf", exist_ok=True)

        pdf_path = f"data/telegrammi_pdf/telegramma_{order_name_clean}.pdf"

        genera_pdf_telegramma(
            pdf_path=pdf_path,
            telegramma=telegramma
        )

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        storage_path = f"telegrammi/{pratica_id}/telegramma_cliente.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            storage_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            storage_path
        )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        supabase.table("pratiche") \
            .update({
                "pdf_url": pdf_public_url,
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return RedirectResponse(
            url=pdf_public_url,
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_GENERA_PDF_TELEGRAMMA",
            "pratica_id": pratica_id,
            "error": str(e)
        }

def split_nome_cognome(full_name):
    parts = (full_name or "").strip().split()

    if len(parts) <= 1:
        return full_name or "", ""

    return parts[0], " ".join(parts[1:])

def parse_indirizzo_h2h(via):
    via = clean_h2h_text(via)
    via = (via or "").strip()
    parts = via.split()

    if not parts:
        return "VIA", ""

    dug_list = [
        "VIA",
        "VIALE",
        "PIAZZA",
        "PIAZZALE",
        "VICOLO",
        "VICO",
        "STRADA",
        "CORSO",
        "LOCALITÀ",
        "LOCALITA",
        "CIRCONVALLAZIONE"
    ]

    first = parts[0].upper()

    if first in dug_list:
        return first, " ".join(parts[1:]).upper()

    return "VIA", via.upper()


def build_nominativo_h2h_from_data(data, NominativoType, IndirizzoType, label="indirizzo"):
    data = data or {}

    if isinstance(data, str):
        data = {"raw": data}

    raw = str(data.get("raw") or "").strip()

    if raw:
        parsed = estrai_dati_rubrica_da_raw(raw)
    else:
        parsed = {
            "nome": str(data.get("nome") or "").strip(),
            "via": str(data.get("via") or "").strip(),
            "civico": str(data.get("civico") or "").strip(),
            "cap": str(data.get("cap") or "").strip(),
            "comune": str(data.get("comune") or "").strip(),
            "provincia": str(data.get("provincia") or "").strip().upper()[:2],
        }

    nome_completo = clean_h2h_text(parsed.get("nome") or "")
    nome, cognome = split_nome_cognome(nome_completo)

    via = clean_h2h_text(parsed.get("via") or "")
    civico = clean_h2h_text(parsed.get("civico") or "")
    cap = normalizza_cap(parsed.get("cap") or "")
    comune = clean_h2h_text(parsed.get("comune") or "").upper()
    provincia = normalizza_provincia(parsed.get("provincia") or "")

    if not nome_completo or not via or not cap or not comune or not provincia:
        raise ValueError(
            f"Dati {label} incompleti: "
            f"nome={nome_completo}, via={via}, civico={civico}, "
            f"cap={cap}, comune={comune}, provincia={provincia}"
        )

    dug, toponimo = parse_indirizzo_h2h(via)

    return NominativoType(
        Nome=clean_h2h_text(nome).upper(),
        Cognome=clean_h2h_text(cognome).upper(),
        CAP=cap,
        Citta=comune,
        Provincia=provincia,
        Indirizzo=IndirizzoType(
            DUG=clean_h2h_text(dug).upper(),
            Toponimo=clean_h2h_text(toponimo).upper(),
            NumeroCivico=civico
        ),
        TipoIndirizzo="NORMALE",
        ForzaDestinazione=True,
        InesitateDigitali=False,
        CodiceFiscaleResult=0
    )

@app.get("//pratiche/telegramma-prezza/{pratica_id}")
def _telegramma_prezza(pratica_id: str):
    """
    Porta un Telegramma da RICEVUTO_MANUALE a PREZZATA_DA_CONFERMARE.
    NON chiama Poste.
    NON genera costi.
    Serve come controllo operatore prima della finalizzazione.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        stato = pratica.get("stato")

        if stato != "RICEVUTO_MANUALE":
            return {
                "success": False,
                "blocked": True,
                "error": "Il Telegramma può essere prezzato solo da RICEVUTO_MANUALE",
                "stato": stato,
                "pratica_id": pratica_id
            }

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        supabase.table("pratiche") \
            .update({
                "stato": "PREZZATA_DA_CONFERMARE",
                "poste_response": {
                    "step": "TELEGRAMMA_PREZZATO_MANUALE",
                    "note": "Telegramma controllato manualmente e pronto per finalizzazione",
                    "prezzato_at": now_iso
                },
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return RedirectResponse(
            url="//pratiche?stato=PREZZATA_DA_CONFERMARE",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_PREZZA",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("//pratiche/telegramma-finalizza/{pratica_id}")
def _telegramma_finalizza(pratica_id: str):
    """
    Finalizza manualmente un Telegramma già controllato/prezzato.
    NON chiama Poste H2H.
    NON genera costi.
    Porta la pratica a INVIATO_POSTE e garantisce il PDF Telegramma.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        stato = pratica.get("stato")

        if stato != "PREZZATA_DA_CONFERMARE":
            return {
                "success": False,
                "blocked": True,
                "error": "Il Telegramma può essere finalizzato solo da PREZZATA_DA_CONFERMARE",
                "stato": stato,
                "pratica_id": pratica_id
            }

        pdf_public_url = pratica.get("pdf_url") or ""

        # Se il PDF Telegramma non esiste ancora, lo genera e lo salva
        if not pdf_public_url:
            telegramma = {
                "mittente": pratica.get("mittente") or {},
                "destinatario": pratica.get("destinatario") or {},
                "testo": pratica.get("testo") or ""
            }

            order_name_clean = str(
                pratica.get("shopify_order_name")
                or pratica.get("order_name")
                or pratica_id
            ).replace("#", "").replace("/", "-")

            os.makedirs("data/telegrammi_pdf", exist_ok=True)

            pdf_path = f"data/telegrammi_pdf/telegramma_{order_name_clean}.pdf"

            genera_pdf_telegramma(
                pdf_path=pdf_path,
                telegramma=telegramma
            )

            with open(pdf_path, "rb") as f:
                pdf_bytes = f.read()

            storage_path = f"telegrammi/{pratica_id}/telegramma_cliente.pdf"

            supabase.storage.from_(SUPABASE_BUCKET).upload(
                storage_path,
                pdf_bytes,
                {
                    "content-type": "application/pdf",
                    "upsert": "true"
                }
            )

            pdf_public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
                storage_path
            )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        supabase.table("pratiche") \
            .update({
                "stato": "INVIATO_POSTE",
                "pdf_url": pdf_public_url,
                "pdf_ricevuta_cliente_url": pdf_public_url,
                "poste_response": {
                    "step": "TELEGRAMMA_FINALIZZATO_MANUALE",
                    "note": "Telegramma finalizzato manualmente da ",
                    "finalizzato_at": now_iso
                },
                "updated_at": now_iso
            }) \
            .eq("id", pratica_id) \
            .execute()

        return RedirectResponse(
            url="//pratiche?stato=INVIATO_POSTE",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_FINALIZZA",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/shopify/telegramma/order")
def shopify_telegramma_order_info():
    return {
        "success": True,
        "message": "Endpoint attivo. Usa POST per inviare un ordine Shopify."
    }


@app.post("/shopify/telegramma/order")
async def shopify_telegramma_order(request: Request):
    """
    Webhook Shopify per ordini Telegramma.

    Flusso:
    - riceve ordine Shopify
    - legge i prodotti Telegramma
    - salva la pratica in Supabase
    - se ordine pagato:
        AUTO TELEGRAMMA TEST POST-PAGAMENTO
        preventivo -> invio H2H test -> ricevuta cliente -> email cliente
    - se ordine non pagato:
        salva solo la pratica per lavorazione manuale
    """

    try:
        order = await request.json()

        order_id = order.get("id")
        order_name = order.get("name")
        email = order.get("email") or order.get("contact_email")

        financial_status = str(
            order.get("financial_status") or ""
        ).lower().strip()

        is_paid = financial_status == "paid"

        telegrammi = []
        saved_items = []
        auto_results = []

        for item in order.get("line_items", []):
            title = item.get("title", "")

            if "TELEGRAMMA" not in title.upper():
                continue

            props = {}

            for p in item.get("properties", []):
                props[p.get("name")] = p.get("value")

            telegrammi.append({
                "order_id": order_id,
                "order_name": order_name,
                "email": email,
                "financial_status": financial_status,
                "is_paid": is_paid,
                "testo": props.get("📨 Testo telegramma"),
                "parole": props.get("🔢 Parole telegramma"),
                "mittente": {
                    "nome": props.get("_mittente_nome"),
                    "via": props.get("_mittente_via"),
                    "civico": props.get("_mittente_civico"),
                    "cap": props.get("_mittente_cap"),
                    "comune": props.get("_mittente_comune"),
                    "provincia": props.get("_mittente_provincia"),
                    "contatto": props.get("_mittente_contatto"),
                },
                "destinatario": {
                    "nome": props.get("_destinatario_nome"),
                    "via": props.get("_destinatario_via"),
                    "civico": props.get("_destinatario_civico"),
                    "cap": props.get("_destinatario_cap"),
                    "comune": props.get("_destinatario_comune"),
                    "provincia": props.get("_destinatario_provincia"),
                    "contatto": props.get("_destinatario_contatto"),
                }
            })

        os.makedirs("data/webhooks", exist_ok=True)

        log_path = f"data/webhooks/order_{order_id}.json"

        with open(log_path, "w", encoding="utf-8") as f:
            json.dump({
                "order_id": order_id,
                "order_name": order_name,
                "email": email,
                "financial_status": financial_status,
                "is_paid": is_paid,
                "telegrammi": telegrammi
            }, f, ensure_ascii=False, indent=2)

        for tg in telegrammi:
            try:
                # =====================================================
                # ANTI-DUPLICATO SHOPIFY TELEGRAMMA
                # =====================================================
                # Shopify può richiamare il webhook più volte.
                # Evitiamo di creare più pratiche per lo stesso ordine.
                # =====================================================

                try:
                    existing_res = supabase.table("pratiche") \
                        .select("id, order_id, order_name, tipo_servizio, stato, numero_raccomandata, email_sent, testo") \
                        .eq("order_id", str(order_id)) \
                        .eq("tipo_servizio", "TELEGRAMMA") \
                        .execute()

                    existing_items = existing_res.data or []

                    tg_testo_norm = str(tg.get("testo") or "").strip().upper()

                    existing_match = None

                    for existing in existing_items:
                        existing_testo_norm = str(
                            existing.get("testo") or ""
                        ).strip().upper()

                        if existing_testo_norm == tg_testo_norm:
                            existing_match = existing
                            break

                    if existing_match:
                        saved_items.append([existing_match])

                        auto_results.append({
                            "success": True,
                            "skipped": True,
                            "step": "TELEGRAMMA_SHOPIFY_DUPLICATO",
                            "reason": "Ordine Telegramma già presente in pratiche",
                            "order_id": order_id,
                            "order_name": order_name,
                            "pratica_id": existing_match.get("id"),
                            "stato": existing_match.get("stato"),
                            "numero_accettazione": existing_match.get("numero_raccomandata"),
                            "email_sent": existing_match.get("email_sent")
                        })

                        print(
                            "TELEGRAMMA_SHOPIFY_DUPLICATO:",
                            order_name,
                            existing_match.get("id")
                        )

                        continue

                except Exception as duplicate_check_error:
                    print(
                        "ERRORE_CONTROLLO_DUPLICATO_TELEGRAMMA:",
                        str(duplicate_check_error)
                    )
                now_iso = datetime.datetime.now(
                    datetime.timezone.utc
                ).isoformat()

                insert_payload = {
                    "order_id": str(order_id),
                    "order_name": order_name,
                    "tipo_servizio": "TELEGRAMMA",
                    "cliente_email": email,
                    "mittente": tg.get("mittente"),
                    "destinatario": tg.get("destinatario"),
                    "testo": tg.get("testo"),
                    "parole": int(tg.get("parole") or 0),
                    "stato": "RICEVUTO_PAGATO" if is_paid else "RICEVUTO_MANUALE",
                    "updated_at": now_iso
                }

                insert_result = supabase.table("pratiche") \
                    .insert(insert_payload) \
                    .execute()

                saved_items.append(insert_result.data)

                pratica_id = None

                if insert_result.data and len(insert_result.data) > 0:
                    pratica_id = insert_result.data[0].get("id")

                if pratica_id:
                    if is_paid:
                        try:
                            auto_result = auto_telegramma_test_post_pagamento(
                                pratica_id
                            )

                            auto_results.append({
                                "pratica_id": pratica_id,
                                "order_name": order_name,
                                "financial_status": financial_status,
                                "result": auto_result
                            })

                            print(
                                "AUTO_TELEGRAMMA_TEST_POST_PAGAMENTO:",
                                auto_result
                            )

                        except Exception as auto_err:
                            auto_results.append({
                                "pratica_id": pratica_id,
                                "order_name": order_name,
                                "financial_status": financial_status,
                                "success": False,
                                "error": str(auto_err)
                            })

                            print(
                                "ERRORE_AUTO_TELEGRAMMA_TEST_POST_PAGAMENTO:",
                                str(auto_err)
                            )

                    else:
                        auto_results.append({
                            "pratica_id": pratica_id,
                            "order_name": order_name,
                            "skipped": True,
                            "reason": f"Ordine non pagato: financial_status={financial_status}"
                        })

            except Exception as db_error:
                print(
                    "ERRORE SALVATAGGIO/INVIO TELEGRAMMA:",
                    str(db_error)
                )

                auto_results.append({
                    "success": False,
                    "step": "ERRORE_SALVATAGGIO_TELEGRAMMA",
                    "order_name": order_name,
                    "error": str(db_error)
                })

        return {
            "success": True,
            "message": "Telegramma Shopify ricevuto e processato",
            "order_id": order_id,
            "order_name": order_name,
            "email": email,
            "financial_status": financial_status,
            "is_paid": is_paid,
            "telegrammi_trovati": len(telegrammi),
            "telegrammi": telegrammi,
            "saved_items": saved_items,
            "auto_results": auto_results
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_SHOPIFY_TELEGRAMMA_ORDER",
            "error": str(e)
        }

@app.get("/shopify/telegramma/last")
def shopify_telegramma_last():
    try:
        folder = "data/webhooks"

        if not os.path.exists(folder):
            return {
                "success": False,
                "error": "Nessun webhook ricevuto"
            }

        files = sorted(
            [f for f in os.listdir(folder) if f.endswith(".json")],
            reverse=True
        )

        if not files:
            return {
                "success": False,
                "error": "Nessun file webhook trovato"
            }

        latest_file = files[0]

        with open(os.path.join(folder, latest_file), "r", encoding="utf-8") as f:
            data = json.load(f)

        return {
            "success": True,
            "file": latest_file,
            "data": data
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }


@app.get("/shopify/telegramma/test-pdf-last")
def shopify_telegramma_test_pdf_last():
    try:
        folder = "data/webhooks"

        if not os.path.exists(folder):
            return {"success": False, "error": "Nessun webhook ricevuto"}

        files = sorted(
            [f for f in os.listdir(folder) if f.endswith(".json")],
            reverse=True
        )

        if not files:
            return {"success": False, "error": "Nessun file webhook trovato"}

        latest_file = files[0]

        with open(os.path.join(folder, latest_file), "r", encoding="utf-8") as f:
            data = json.load(f)

        telegrammi = data.get("telegrammi", [])

        if not telegrammi:
            return {"success": False, "error": "Nessun telegramma trovato"}

        telegramma = telegrammi[0]

        os.makedirs("data/telegrammi_pdf", exist_ok=True)

        order_name = str(data.get("order_name", "TEST")).replace("#", "")
        pdf_path = f"data/telegrammi_pdf/telegramma_{order_name}.pdf"

        genera_pdf_telegramma(pdf_path, telegramma)

        return FileResponse(
            pdf_path,
            media_type="application/pdf",
            filename=f"telegramma_{order_name}.pdf"
        )

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/shopify/telegramma/test-h2h-xml-last")
def shopify_telegramma_test_h2h_xml_last():
    history = HistoryPlugin()

    try:
        folder = "data/webhooks"

        files = sorted(
            [f for f in os.listdir(folder) if f.endswith(".json")],
            reverse=True
        )

        if not files:
            return {"success": False, "error": "Nessun webhook trovato"}

        latest_file = files[0]

        with open(os.path.join(folder, latest_file), "r", encoding="utf-8") as f:
            data = json.load(f)

        telegramma = data.get("telegrammi", [])[0]

        client, service = poste_client(timeout=60, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")

        mitt = telegramma.get("mittente", {})
        dest = telegramma.get("destinatario", {})

        mitt_nome, mitt_cognome = split_nome_cognome(mitt.get("nome", ""))
        dest_nome, dest_cognome = split_nome_cognome(dest.get("nome", ""))

        mitt_dug, mitt_toponimo = parse_indirizzo_h2h(mitt.get("via", ""))
        dest_dug, dest_toponimo = parse_indirizzo_h2h(dest.get("via", ""))

        nom_mitt = NominativoType(
            Nome=mitt_nome,
            Cognome=mitt_cognome,
            CAP=mitt.get("cap", ""),
            Citta=mitt.get("comune", "").upper(),
            Provincia=mitt.get("provincia", "").upper(),
            Indirizzo=IndirizzoType(
                DUG=mitt_dug,
                Toponimo=mitt_toponimo,
                NumeroCivico=mitt.get("civico", "")
            ),
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        nom_dest = NominativoType(
            Nome=dest_nome,
            Cognome=dest_cognome,
            CAP=dest.get("cap", ""),
            Citta=dest.get("comune", "").upper(),
            Provincia=dest.get("provincia", "").upper(),
            Indirizzo=IndirizzoType(
                DUG=dest_dug,
                Toponimo=dest_toponimo,
                NumeroCivico=dest.get("civico", "")
            ),
            TipoIndirizzo="NORMAL",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        os.makedirs("data/telegrammi_pdf", exist_ok=True)

        order_name = str(data.get("order_name", "TEST")).replace("#", "")
        pdf_path = f"data/telegrammi_pdf/telegramma_{order_name}.pdf"

        genera_pdf_telegramma(pdf_path, telegramma)

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="PDF",
            MD5=md5_pdf
        )

        message = client.create_message(
            service,
            "Invio",
            IDRichiesta=str(uuid.uuid4()),
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit={
                "Mittente": MittenteType(
                    Nominativo=nom_mitt,
                    InviaStampa=False
                ),
                "Destinatari": {
                    "Destinatario": [
                        DestinatarioType(Nominativo=nom_dest)
                    ]
                },
                "NumeroDestinatari": 1,
                "Documento": [documento],
                "Opzioni": {
                    "OpzionidiStampa": {
                        "ResolutionX": 300,
                        "ResolutionY": 300,
                        "BW": True,
                        "FronteRetro": False,
                        "PageSize": "A4"
                    },
                    "SecurPaper": False,
                    "DPM": False,
                    "DataStampa": datetime.datetime.now().replace(microsecond=0),
                    "InserisciMittente": True,
                    "Archiviazione": False,
                    "AnniArchiviazioneSpecified": False,
                    "FirmaElettronica": False,
                    "AnniArchiviazione": 0,
                    "ArchiviazioneDocumenti": ""
                },
                "PrezzaturaSincrona": False,
                "Nazionale": True,
                "ForzaInvioDestinazioniValide": True
            }
        )

        fix_wsa_to(message)

        xml_string = etree.tostring(
            message,
            pretty_print=True,
            encoding="unicode"
        )

        return {
            "success": True,
            "file": latest_file,
            "order_name": data.get("order_name"),
            "xml_preview": xml_string
        }

    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/shopify/telegramma/send-last")
def shopify_telegramma_send_last():
    require_h2h_debug_enabled()
    
    history = HistoryPlugin()

    try:
        folder = "data/webhooks"

        if not os.path.exists(folder):
            return {"success": False, "error": "Nessun webhook ricevuto"}

        files = sorted(
            [f for f in os.listdir(folder) if f.endswith(".json")],
            reverse=True
        )

        if not files:
            return {"success": False, "error": "Nessun webhook trovato"}

        latest_file = files[0]

        with open(os.path.join(folder, latest_file), "r", encoding="utf-8") as f:
            data = json.load(f)

        telegrammi = data.get("telegrammi", [])

        if not telegrammi:
            return {"success": False, "error": "Nessun telegramma trovato"}

        telegramma = telegrammi[0]

        client, service = poste_client(timeout=90, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")

        mitt = telegramma.get("mittente", {})
        dest = telegramma.get("destinatario", {})

        mitt_nome, mitt_cognome = split_nome_cognome(mitt.get("nome", ""))
        dest_nome, dest_cognome = split_nome_cognome(dest.get("nome", ""))

        mitt_dug, mitt_toponimo = parse_indirizzo_h2h(mitt.get("via", ""))
        dest_dug, dest_toponimo = parse_indirizzo_h2h(dest.get("via", ""))

        nom_mitt = NominativoType(
            Nome=clean_h2h_text(mitt_nome),
            Cognome=clean_h2h_text(mitt_cognome),
            CAP=clean_h2h_text(mitt.get("cap", "")),
            Citta=clean_h2h_text(mitt.get("comune", "")).upper(),
            Provincia=clean_h2h_text(mitt.get("provincia", "")).upper(),
            Indirizzo=IndirizzoType(
                DUG=clean_h2h_text(mitt_dug),
                Toponimo=clean_h2h_text(mitt_toponimo),
                NumeroCivico=clean_h2h_text(mitt.get("civico", ""))
            ),
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        nom_dest = NominativoType(
            Nome=clean_h2h_text(dest_nome),
            Cognome=clean_h2h_text(dest_cognome),
            CAP=clean_h2h_text(dest.get("cap", "")),
            Citta=clean_h2h_text(dest.get("comune", "")).upper(),
            Provincia=clean_h2h_text(dest.get("provincia", "")).upper(),
            Indirizzo=IndirizzoType(
                DUG=clean_h2h_text(dest_dug),
                Toponimo=clean_h2h_text(dest_toponimo),
                NumeroCivico=clean_h2h_text(dest.get("civico", ""))
            ),
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        os.makedirs("data/telegrammi_pdf", exist_ok=True)

        order_name_clean = str(data.get("order_name", "TEST")).replace("#", "")
        pdf_path = f"data/telegrammi_pdf/telegramma_{order_name_clean}.pdf"

        genera_pdf_telegramma(pdf_path, telegramma)

        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="PDF",
            MD5=md5_pdf
        )

        id_richiesta = str(uuid.uuid4())

        result = service.Invio(
            IDRichiesta=id_richiesta,
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit={
                "Mittente": MittenteType(
                    Nominativo=nom_mitt,
                    InviaStampa=False
                ),
                "Destinatari": {
                    "Destinatario": [
                        DestinatarioType(Nominativo=nom_dest)
                    ]
                },
                "NumeroDestinatari": 1,
                "Documento": [documento],
                "Opzioni": {
                    "OpzionidiStampa": {
                        "ResolutionX": 300,
                        "ResolutionY": 300,
                        "BW": True,
                        "FronteRetro": False,
                        "PageSize": "A4"
                    },
                    "SecurPaper": False,
                    "DPM": False,
                    "DataStampa": datetime.datetime.now().replace(microsecond=0),
                    "InserisciMittente": True,
                    "Archiviazione": False,
                    "AnniArchiviazioneSpecified": False,
                    "FirmaElettronica": False,
                    "AnniArchiviazione": 0,
                    "ArchiviazioneDocumenti": ""
                },
                "PrezzaturaSincrona": False,
                "Nazionale": True,
                "ForzaInvioDestinazioniValide": True
            }
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        os.makedirs("data/h2h_results", exist_ok=True)

        result_path = f"data/h2h_results/send_{order_name_clean}.json"

        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({
                "success": True,
                "order_name": data.get("order_name"),
                "id_richiesta": id_richiesta,
                "poste_response": str(result),
                "xml_sent": xml_sent,
                "xml_received": xml_received
            }, f, ensure_ascii=False, indent=2)

        try:
            poste_result_text = str(result)

            stato_pratica = "INVIATO_POSTE"

            if "Type': 'E'" in poste_result_text or '"Type": "E"' in poste_result_text:
                stato_pratica = "ERRORE_POSTE"

            supabase.table("pratiche").update({
                "stato": stato_pratica,
                "poste_response": {
                    "raw": poste_result_text
                },
                "xml_sent": xml_sent,
                "xml_received": xml_received,
                "id_richiesta": id_richiesta,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }).eq("order_name", data.get("order_name")).execute()

        except Exception as db_error:
            print("ERRORE AGGIORNAMENTO PRATICA H2H:", str(db_error))

        return {
            "success": True,
            "order_name": data.get("order_name"),
            "id_richiesta": id_richiesta,
            "poste_response": str(result),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/shopify/telegramma/send-last-pratica")
def shopify_telegramma_send_last_pratica():
    try:
        result = supabase.table("pratiche") \
            .select("*") \
            .eq("tipo_servizio", "TELEGRAMMA") \
            .order("created_at", desc=True) \
            .limit(1) \
            .execute()

        if not result.data:
            return {
                "success": False,
                "error": "Nessuna pratica Telegramma trovata su Supabase"
            }

        pratica = result.data[0]

        return {
            "success": True,
            "message": "Pratica trovata correttamente da Supabase",
            "pratica": pratica
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/shopify/telegramma/send-pratica/{pratica_id}")
def shopify_telegramma_send_pratica(pratica_id: str):

    try:
        result = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not result.data:
            return {
                "success": False,
                "error": "Pratica non trovata"
            }

        pratica = result.data

        return {
            "success": True,
            "message": "Pratica caricata correttamente",
            "pratica": pratica
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def invia_telegramma_pratica_h2h(pratica_id: str):
    history = HistoryPlugin()

    pratica_result = supabase.table("pratiche") \
        .select("*") \
        .eq("id", pratica_id) \
        .single() \
        .execute()

    if not pratica_result.data:
        return {"success": False, "error": "Pratica non trovata"}

    pratica = pratica_result.data

    telegramma = {
        "testo": pratica.get("testo"),
        "mittente": pratica.get("mittente") or {},
        "destinatario": pratica.get("destinatario") or {}
    }

    client, service = poste_client(timeout=90, extra_plugins=[history])

    NominativoType = client.get_type("ns1:Nominativo")
    IndirizzoType = client.get_type("ns1:Indirizzo")
    MittenteType = client.get_type("ns1:Mittente")
    DestinatarioType = client.get_type("ns1:Destinatario")
    DocumentoType = client.get_type("ns1:Documento")
    DatiRicevutaType = client.get_type("ns0:DatiRicevuta")

    mitt = telegramma["mittente"]
    dest = telegramma["destinatario"]

    mitt_nome, mitt_cognome = split_nome_cognome(mitt.get("nome", ""))
    dest_nome, dest_cognome = split_nome_cognome(dest.get("nome", ""))

    mitt_dug, mitt_toponimo = parse_indirizzo_h2h(mitt.get("via", ""))
    dest_dug, dest_toponimo = parse_indirizzo_h2h(dest.get("via", ""))

    nom_mitt = NominativoType(
        Nome=clean_h2h_text(mitt_nome),
        Cognome=clean_h2h_text(mitt_cognome),
        CAP=clean_h2h_text(mitt.get("cap", "")),
        Citta=clean_h2h_text(mitt.get("comune", "")).upper(),
        Provincia=clean_h2h_text(mitt.get("provincia", "")).upper(),
        Indirizzo=IndirizzoType(
            DUG=clean_h2h_text(mitt_dug),
            Toponimo=clean_h2h_text(mitt_toponimo),
            NumeroCivico=clean_h2h_text(mitt.get("civico", ""))
        ),
        TipoIndirizzo="NORMALE",
        ForzaDestinazione=True,
        InesitateDigitali=False,
        CodiceFiscaleResult=0
    )

    nom_dest = NominativoType(
        Nome=clean_h2h_text(dest_nome),
        Cognome=clean_h2h_text(dest_cognome),
        CAP=clean_h2h_text(dest.get("cap", "")),
        Citta=clean_h2h_text(dest.get("comune", "")).upper(),
        Provincia=clean_h2h_text(dest.get("provincia", "")).upper(),
        Indirizzo=IndirizzoType(
            DUG=clean_h2h_text(dest_dug),
            Toponimo=clean_h2h_text(dest_toponimo),
            NumeroCivico=clean_h2h_text(dest.get("civico", ""))
        ),
        TipoIndirizzo="NORMALE",
        ForzaDestinazione=True,
        InesitateDigitali=False,
        CodiceFiscaleResult=0
    )

    os.makedirs("data/telegrammi_pdf", exist_ok=True)

    order_name_clean = str(pratica.get("order_name", "TEST")).replace("#", "")
    pdf_path = f"data/telegrammi_pdf/telegramma_{order_name_clean}.pdf"

    genera_pdf_telegramma(pdf_path, telegramma)

    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    documento = DocumentoType(
        Immagine=base64.b64encode(pdf_bytes).decode("utf-8"),
        TipoDocumento="pdf",
        MD5=hashlib.md5(pdf_bytes).hexdigest()
    )
    destinatario_obj = DestinatarioType(
        Nominativo=nom_dest,
        IdDestinatario=1
    )
    dati_ricevuta = DatiRicevutaType(
        Nominativo=nom_mitt
    )

    id_richiesta = str(uuid.uuid4())

    result = service.Invio(
        IDRichiesta=id_richiesta,
        Cliente=POSTE_H2H_USERID,
        CodiceContratto=POSTE_H2H_CONTRACT_ID,
        ROLSubmit={
            "Mittente": MittenteType(Nominativo=nom_mitt, InviaStampa=False),
            "DatiRicevuta": dati_ricevuta,
            
            "Destinatari": {
                "Destinatario": [
                    destinatario_obj
                ]
            },
            "NumeroDestinatari": 1,
            "Documento": [documento],
            "Opzioni": {
                "OpzionidiStampa": {
                    "ResolutionX": 300,
                    "ResolutionY": 300,
                    "BW": True,
                    "FronteRetro": False,
                    "PageSize": "A4"
                },
                "SecurPaper": False,
                "DPM": False,
                "DataStampa": datetime.datetime.now().replace(microsecond=0),
                "InserisciMittente": True,
                "Archiviazione": False,
                "AnniArchiviazioneSpecified": False,
                "FirmaElettronica": False,
                "AnniArchiviazione": 0,
                "ArchiviazioneDocumenti": "NESSUNA"
            },
            "PrezzaturaSincrona": False,
            "Nazionale": True,
            "ForzaInvioDestinazioniValide": True
        }
    )

    xml_sent = None
    xml_received = None

    try:
        xml_sent = etree.tostring(history.last_sent["envelope"], pretty_print=True, encoding="unicode")
    except Exception:
        pass

    try:
        xml_received = etree.tostring(history.last_received["envelope"], pretty_print=True, encoding="unicode")
    except Exception:
        pass

    poste_result_text = str(result)
    stato_pratica = "INVIATO_POSTE"

    if "Type': 'E'" in poste_result_text or '"Type": "E"' in poste_result_text:
        stato_pratica = "ERRORE_POSTE"

    supabase.table("pratiche").update({
        "stato": stato_pratica,
        "poste_response": {"raw": poste_result_text},
        "xml_sent": xml_sent,
        "xml_received": xml_received,
        "id_richiesta": id_richiesta,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).eq("id", pratica_id).execute()

    return {
        "success": True,
        "pratica_id": pratica_id,
        "order_name": pratica.get("order_name"),
        "stato": stato_pratica,
        "id_richiesta": id_richiesta,
        "poste_response": poste_result_text
    }

@app.get("/shopify/telegramma/invia-pratica/{pratica_id}")
def shopify_telegramma_invia_pratica(pratica_id: str):
    require_h2h_debug_enabled()
    
    try:
        invia_telegramma_pratica_h2h(pratica_id)

        return RedirectResponse(
            url="//pratiche",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma-v2/{pratica_id}")
def invia_telegramma_h2h_v2_endpoint(pratica_id: str):
    require_h2h_debug_enabled()
    
    try:
        result = invia_telegramma_pratica_h2h(pratica_id)

        return {
            "success": result.get("success"),
            "versione": "TELEGRAMMA_H2H_V2_TEST",
            "pratica_id": pratica_id,
            "result": result
        }

    except Exception as e:
        return {
            "success": False,
            "versione": "TELEGRAMMA_H2H_V2_TEST",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/poste/h2h/telegramma/recipient-validation/{pratica_id}")
def telegramma_recipient_validation(pratica_id: str):
    """
    Valida il destinatario Telegramma con Poste H2H TEST.
    NON invia Telegrammi.
    NON fa PreConfirm.
    NON fa Confirm.
    Serve per capire se indirizzo/destinatario sono accettati da Poste.
    """

    history = HistoryPlugin()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("tipo_servizio") != "TELEGRAMMA":
            return {
                "success": False,
                "error": "Questa pratica non è un Telegramma",
                "tipo_servizio": pratica.get("tipo_servizio"),
                "pratica_id": pratica_id
            }

        destinatario_data = telegramma_normalizza_dati_indirizzo(
            pratica.get("destinatario") or {}
        )

        client, service = telegramma_service(
            timeout=120,
            extra_plugins=[history]
        )

        RecipientType = telegramma_find_type(
            client,
            "Recipient",
            "Telegramma.WS"
        )

        DestinatarioType = telegramma_find_type(
            client,
            "Destinatario",
            "Telegramma.Schema"
        )

        dest_nome, dest_cognome = telegramma_split_nome_cognome(
            destinatario_data.get("nome")
        )

        destinatario_obj = DestinatarioType(
            CAP=destinatario_data.get("cap"),
            Citta=destinatario_data.get("comune"),
            Cognome=dest_cognome,
            Indirizzo=destinatario_data.get("indirizzo"),
            Nome=dest_nome,
            RagioneSociale="",
            Stato="ITALIA",
            Telefono=destinatario_data.get("telefono") or ""
        )

        recipient_obj = RecipientType(
            ClientIDRecipient="1",
            Provincia=destinatario_data.get("provincia") or "",
            destinatario=destinatario_obj
        )

        try:
            id_request = service.GetIdRequest()
        except Exception:
            id_request = str(uuid.uuid4())

        result = service.RecipientValidation(
            recipient=recipient_obj,
            idRequest=id_request
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        plain_result = make_json_safe(
            zeep_to_plain(result)
        )

        return {
            "success": True,
            "step": "TELEGRAMMA_RECIPIENT_VALIDATION",
            "pratica_id": pratica_id,
            "id_request": id_request,
            "destinatario": destinatario_data,
            "validation_result": plain_result,
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_RECIPIENT_VALIDATION",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/shopify/telegramma/process-pending")
def process_pending_telegrammi():
    require_h2h_debug_enabled()

    try:
        result = supabase.table("pratiche") \
            .select("*") \
            .eq("tipo_servizio", "TELEGRAMMA") \
            .eq("stato", "RICEVUTO") \
            .order("created_at") \
            .limit(10) \
            .execute()

        pratiche = result.data or []

        lavorate = []

        for pratica in pratiche:

            pratica_id = pratica.get("id")

            try:
                invio = invia_telegramma_pratica_h2h(pratica_id)

                lavorate.append({
                    "pratica_id": pratica_id,
                    "order_name": pratica.get("order_name"),
                    "success": invio.get("success"),
                    "stato": invio.get("stato")
                })

            except Exception as pratica_error:

                lavorate.append({
                    "pratica_id": pratica_id,
                    "order_name": pratica.get("order_name"),
                    "success": False,
                    "error": str(pratica_error)
                })

        return {
            "success": True,
            "totali": len(lavorate),
            "pratiche": lavorate
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

def clean_order_display(value):
    value = str(value or "-")

    if value.startswith("SHOPIFY-"):
        return value.replace("SHOPIFY-", "", 1)

    return value

def resolve_h2h_order_id(pratica_id: str):
    """
    Trova l'id corretto in poste_h2h_orders partendo da:
    - id diretto H2H
    - oppure id pratica  collegata tramite pdf_url
    """

    try:
        h2h_res = supabase.table("poste_h2h_orders") \
            .select("id") \
            .eq("id", pratica_id) \
            .limit(1) \
            .execute()

        if h2h_res.data:
            return h2h_res.data[0].get("id")

        pratica_res = supabase.table("pratiche") \
            .select("id,pdf_url") \
            .eq("id", pratica_id) \
            .limit(1) \
            .execute()

        if not pratica_res.data:
            return None

        pdf_url = pratica_res.data[0].get("pdf_url")

        if not pdf_url:
            return None

        h2h_by_pdf = supabase.table("poste_h2h_orders") \
            .select("id") \
            .eq("pdf_url", pdf_url) \
            .limit(1) \
            .execute()

        if h2h_by_pdf.data:
            return h2h_by_pdf.data[0].get("id")

        return None

    except Exception as e:
        print("ERRORE resolve_h2h_order_id:", str(e))
        return None

def estrai_dati_pdf_telegramma_poste(pdf_bytes: bytes):
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        text = ""

        for page in reader.pages:
            text += page.extract_text() or ""
            text += "\n"

        numero_accettazione = None
        numero_telegramma = None
        importo = None

        m_acc = re.search(
            r"Numero\s+Accettazione[:\s]+([0-9]+)",
            text,
            re.IGNORECASE
        )
        if m_acc:
            numero_accettazione = m_acc.group(1).strip()

        m_tel = re.search(
            r"TELEGRAMMA\s+N\.?RO\s+([A-Z0-9]+)",
            text,
            re.IGNORECASE
        )
        if m_tel:
            numero_telegramma = m_tel.group(1).strip()

        m_imp = re.search(
            r"IMPORTO\s+EURO\s+([0-9]+(?:[.,][0-9]+)?)",
            text,
            re.IGNORECASE
        )
        if m_imp:
            importo = m_imp.group(1).replace(",", ".").strip()

        return {
            "success": True,
            "text": text,
            "numero_accettazione": numero_accettazione,
            "numero_telegramma": numero_telegramma,
            "importo": importo
        }

    except Exception as e:
        return {
            "success": False,
            "text": "",
            "numero_accettazione": None,
            "numero_telegramma": None,
            "importo": None,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/apri-pdf-poste/{pratica_id}")
def dashboard_apri_pdf_poste_originale(pratica_id: str):
    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        pdf_poste_url = (
            poste_response.get("pdf_poste_originale_url")
            or poste_response.get("pdf_originale_poste_url")
        )

        if not pdf_poste_url:
            return {
                "success": False,
                "error": "PDF Poste originale non disponibile",
                "pratica_id": pratica_id,
                "order_name": pratica.get("order_name")
            }

        return RedirectResponse(
            url=pdf_poste_url,
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "pratica_id": pratica_id
        }

@app.get("/dashboard/pratiche/apri-pdf/{pratica_id}")
def dashboard_apri_pdf_pratica(pratica_id: str):
    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if (pratica.get("tipo_servizio") or "").upper() == "TELEGRAMMA":
            pdf_url = ensure_pdf_cliente_telegramma(pratica)
        else:
            pdf_url = (
                pratica.get("pdf_ricevuta_cliente_url")
                or pratica.get("pdf_url")
            )

        if not pdf_url:
            return {
                "success": False,
                "error": "Nessun PDF disponibile per questa pratica",
                "pratica_id": pratica_id,
                "order_name": pratica.get("order_name")
            }

        return RedirectResponse(
            url=pdf_url,
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "pratica_id": pratica_id
        }

def genera_pdf_cliente_raccomandata_bytes(pratica: dict, numero_raccomandata: str):
    """
    Genera il documento cliente Eccomi Posta per Raccomandata.
    NON chiama Poste.
    NON invia email.
    NON genera costi.
    NON è documento fiscale.
    """

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    y = height - 2.0 * cm

    def clean_pdf_text(value):
        return str(value or "-").replace("\n", " ").strip()

    def draw_wrapped(text, x, y, font_name="Helvetica", font_size=10, max_width=None, line_height=0.45 * cm):
        if max_width is None:
            max_width = width - 4 * cm

        c.setFont(font_name, font_size)

        words = clean_pdf_text(text).split()
        line = ""

        for word in words:
            test_line = (line + " " + word).strip()

            if c.stringWidth(test_line, font_name, font_size) <= max_width:
                line = test_line
            else:
                c.drawString(x, y, line)
                y -= line_height
                line = word

        if line:
            c.drawString(x, y, line)
            y -= line_height

        return y

    order_name = (
        pratica.get("shopify_order_name")
        or pratica.get("order_name")
        or pratica.get("order_id")
        or "-"
    )

    try:
        order_name_clean = clean_order_display(order_name)
    except Exception:
        order_name_clean = order_name

    cliente_email = pratica.get("cliente_email") or pratica.get("email_to") or "-"

    mitt_nome, mitt_indirizzo, mitt_localita = _ecx_addr_label(pratica.get("mittente"))
    dest_nome, dest_indirizzo, dest_localita = _ecx_addr_label(pratica.get("destinatario"))

    has_rr = bool_from_any(pratica.get("ricevuta_ritorno"))
    servizio_label = "Raccomandata con ricevuta di ritorno" if has_rr else "Raccomandata"

    data_operazione = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    # Header
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, y, "ECCOMI POSTA")

    y -= 0.65 * cm
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, y, "Servizi Postali Digitali")

    y -= 0.95 * cm
    c.line(2 * cm, y, width - 2 * cm, y)

    y -= 1.1 * cm
    c.setFont("Helvetica-Bold", 19)
    c.drawString(2 * cm, y, "Conferma invio Raccomandata")

    y -= 1.05 * cm

    # Box numero/stato
    box_x = 2 * cm
    box_y = y - 2.2 * cm
    box_w = width - 4 * cm
    box_h = 2.35 * cm

    c.roundRect(box_x, box_y, box_w, box_h, 8, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 10)
    c.drawString(box_x + 0.6 * cm, y - 0.45 * cm, "Numero Raccomandata")
    c.drawString(box_x + 8.4 * cm, y - 0.45 * cm, "Stato")

    c.setFont("Helvetica-Bold", 15)
    c.drawString(box_x + 0.6 * cm, y - 1.2 * cm, clean_pdf_text(numero_raccomandata))

    c.setFont("Helvetica", 12)
    c.drawString(box_x + 8.4 * cm, y - 1.2 * cm, "Accettata da Poste Italiane")

    y = box_y - 0.85 * cm

    # Dati pratica
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Dati pratica")
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y, "Ordine Eccomi:")
    c.setFont("Helvetica", 10)
    c.drawString(5.4 * cm, y, clean_pdf_text(order_name_clean))
    y -= 0.5 * cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y, "Servizio:")
    c.setFont("Helvetica", 10)
    c.drawString(5.4 * cm, y, clean_pdf_text(servizio_label))
    y -= 0.5 * cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y, "Email cliente:")
    c.setFont("Helvetica", 10)
    c.drawString(5.4 * cm, y, clean_pdf_text(cliente_email))
    y -= 0.5 * cm

    c.setFont("Helvetica-Bold", 10)
    c.drawString(2 * cm, y, "Data documento:")
    c.setFont("Helvetica", 10)
    c.drawString(5.4 * cm, y, clean_pdf_text(data_operazione))
    y -= 0.85 * cm

    # Mittente
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Mittente")
    y -= 0.55 * cm

    y = draw_wrapped(mitt_nome, 2 * cm, y, "Helvetica", 10)
    y = draw_wrapped(mitt_indirizzo, 2 * cm, y, "Helvetica", 10)
    y = draw_wrapped(mitt_localita, 2 * cm, y, "Helvetica", 10)

    y -= 0.35 * cm

    # Destinatario
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Destinatario")
    y -= 0.55 * cm

    y = draw_wrapped(dest_nome, 2 * cm, y, "Helvetica", 10)
    y = draw_wrapped(dest_indirizzo, 2 * cm, y, "Helvetica", 10)
    y = draw_wrapped(dest_localita, 2 * cm, y, "Helvetica", 10)

    y -= 0.45 * cm

    # Conferma
    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Conferma Eccomi Posta")
    y -= 0.55 * cm

    testo = (
        "Il presente documento conferma la presa in carico della pratica "
        "e l'avvenuto invio o accettazione della raccomandata tramite "
        "il servizio Eccomi Posta."
    )

    y = draw_wrapped(testo, 2 * cm, y, "Helvetica", 10)

    # Footer
    c.line(2 * cm, 2.0 * cm, width - 2 * cm, 2.0 * cm)

    c.setFont("Helvetica", 8)
    c.drawString(
        2 * cm,
        1.55 * cm,
        "Documento di conferma del servizio Eccomi Posta. Non costituisce documento fiscale."
    )

    c.save()
    buffer.seek(0)

    return buffer.getvalue()

def genera_pdf_interno_monitoraggio_telegramma_bytes(pratica: dict):
    """
    Genera PDF interno Eccomi/Poste dal monitoraggio Telegramma già presente.
    NON chiama Poste.
    NON invia email.
    NON genera costi.
    """

    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    y = height - 2.0 * cm

    def clean(value):
        return str(value or "-").replace("\n", " ").strip()

    poste_response = pratica.get("poste_response") or {}

    if isinstance(poste_response, str):
        try:
            poste_response = json.loads(poste_response)
        except Exception:
            poste_response = {}

    if not isinstance(poste_response, dict):
        poste_response = {}

    get_status_result = poste_response.get("get_status_result") or {}
    status = get_status_result.get("Status") or {}

    result = get_status_result.get("Result") or {}
    result_description = result.get("Description") or "-"

    telegramma_status_details = status.get("TelegramStatusDetails") or {}
    telegramma_status_detail_type = telegramma_status_details.get("TelegrammaStatusDetailsType") or []

    if isinstance(telegramma_status_detail_type, dict):
        telegramma_status_detail_type = [telegramma_status_detail_type]

    primo_dettaglio = telegramma_status_detail_type[0] if telegramma_status_detail_type else {}

    id_telegramma = (
        poste_response.get("id_telegramma")
        or primo_dettaglio.get("IDTelegramma")
        or "-"
    )

    stato_poste = (
        poste_response.get("state")
        or primo_dettaglio.get("State")
        or "-"
    )

    id_richiesta = (
        pratica.get("id_richiesta")
        or poste_response.get("id_richiesta")
        or status.get("GTDMessage")
        or "-"
    )

    numero_accettazione = (
        pratica.get("numero_raccomandata")
        or poste_response.get("numero_accettazione")
        or poste_response.get("tracking")
        or poste_response.get("tracking_number")
        or "-"
    )

    order_name = (
        pratica.get("shopify_order_name")
        or pratica.get("order_name")
        or pratica.get("order_id")
        or "-"
    )

    try:
        order_name = clean_order_display(order_name)
    except Exception:
        pass

    cliente_email = pratica.get("cliente_email") or pratica.get("email_to") or "-"
    data_controllo = datetime.datetime.now().strftime("%d/%m/%Y %H:%M")

    # Header
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, y, "ECCOMI POSTA")

    y -= 0.65 * cm
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, y, "Documento interno di monitoraggio Poste")

    y -= 0.95 * cm
    c.line(2 * cm, y, width - 2 * cm, y)

    y -= 1.1 * cm
    c.setFont("Helvetica-Bold", 18)
    c.drawString(2 * cm, y, "Telegramma - Esito monitoraggio Poste")

    y -= 1.0 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Ordine:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(order_name))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Servizio:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, "TELEGRAMMA")
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Email cliente:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(cliente_email))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "ID richiesta:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(id_richiesta))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Numero accettazione:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(numero_accettazione))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Numero Telegramma:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(id_telegramma))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Stato Poste:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(stato_poste))
    y -= 0.6 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Data controllo:")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, clean(data_controllo))
    y -= 1.0 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Esito tecnico")
    y -= 0.6 * cm

    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, y, clean(result_description)[:110])
    y -= 0.7 * cm

    c.setFont("Helvetica", 10)
    c.drawString(
        2 * cm,
        y,
        "Documento interno generato da Eccomi Posta sulla base del monitoraggio Poste."
    )

    # Footer
    c.line(2 * cm, 2.0 * cm, width - 2 * cm, 2.0 * cm)

    c.setFont("Helvetica", 8)
    c.drawString(
        2 * cm,
        1.55 * cm,
        "Documento interno Eccomi Posta. Non costituisce documento fiscale."
    )

    c.save()
    buffer.seek(0)

    return buffer.getvalue()


@app.get("/dashboard/pratiche/genera-ricevuta-cliente/{pratica_id}")
def dashboard_genera_ricevuta_cliente(pratica_id: str, apri: int = 0):
    """
    Genera la ricevuta cliente Eccomi Posta UNA SOLA VOLTA.
    Regole:
    - NON chiama Poste
    - NON invia email
    - NON genera costi
    - se pdf_ricevuta_cliente_url esiste già: apre quella e basta
    - se email_sent=True ma manca il PDF: blocca per verifica manuale
    - se manca la ricevuta e non è stata inviata email: genera, salva e apre
    """
    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()
        if not pratica_res.data:
            return HTMLResponse(
                f"""
                <html>
                <body style="font-family:Arial;padding:30px;">
                    <h2>Pratica non trovata</h2>
                    <p>ID: {pratica_id}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </body>
                </html>
                """,
                status_code=404
            )
        pratica = pratica_res.data
        tipo_servizio = str(pratica.get("tipo_servizio") or "").upper().strip()
        pdf_esistente = pratica.get("pdf_ricevuta_cliente_url") or ""
        email_gia_inviata = bool_from_any(pratica.get("email_sent"))
        # =====================================================
        # 1) Se la ricevuta cliente esiste già, NON si rigenera.
        #    Si apre quella esistente.
        # =====================================================
        if pdf_esistente:
            return RedirectResponse(
                url=pdf_esistente if apri else "/dashboard/pratiche",
                status_code=303
            )
        # =====================================================
        # 2) Se la mail risulta già inviata ma manca il PDF,
        #    blocchiamo la generazione automatica.
        #    Non alteriamo una pratica già comunicata al cliente.
        # =====================================================
        if email_gia_inviata:
            return HTMLResponse(
                f"""
                <html>
                <body style="font-family:Arial;padding:30px;">
                    <h2>Ricevuta cliente bloccata</h2>
                    <p>Questa pratica risulta già comunicata al cliente via email.</p>
                    <p>Per sicurezza non genero ricevute automatiche su pratiche già inviate.</p>
                    <p><strong>Pratica:</strong> {pratica_id}</p>
                    <p><strong>Servizio:</strong> {tipo_servizio or "-"}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </body>
                </html>
                """,
                status_code=409
            )
        # =====================================================
        # 3) Telegramma: se manca la ricevuta cliente,
        #    usa il flusso già esistente e funzionante.
        # =====================================================
        if tipo_servizio == "TELEGRAMMA":
            pdf_cliente_url = ensure_pdf_cliente_telegramma(pratica)
            return RedirectResponse(
                url=pdf_cliente_url if apri else "/dashboard/pratiche",
                status_code=303
            )
        # =====================================================
        # 4) Raccomandata: genera ricevuta cliente Eccomi.
        #    NON chiama Poste.
        # =====================================================
        numero_raccomandata = pratica.get("numero_raccomandata") or ""
        if not numero_raccomandata:
            poste_response = pratica.get("poste_response") or {}
            if isinstance(poste_response, str):
                try:
                    poste_response = json.loads(poste_response)
                except Exception:
                    poste_response = {}
            if isinstance(poste_response, dict):
                numero_raccomandata = (
                    poste_response.get("numero_raccomandata")
                    or poste_response.get("tracking")
                    or poste_response.get("tracking_number")
                    or poste_response.get("numero_accettazione")
                    or ""
                )
        if not numero_raccomandata:
            return HTMLResponse(
                f"""
                <html>
                <body style="font-family:Arial;padding:30px;">
                    <h2>Numero raccomandata mancante</h2>
                    <p>Non posso generare la ricevuta cliente senza numero raccomandata.</p>
                    <p><strong>Pratica:</strong> {pratica_id}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </body>
                </html>
                """,
                status_code=400
            )
        pdf_bytes = genera_pdf_cliente_raccomandata_bytes(
            pratica=pratica,
            numero_raccomandata=numero_raccomandata
        )
        storage_path = f"raccomandate/{pratica_id}/ricevuta_cliente.pdf"
        supabase.storage.from_(SUPABASE_BUCKET).upload(
            storage_path,
            pdf_bytes,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )
        pdf_cliente_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            storage_path
        )
        poste_response = pratica.get("poste_response") or {}
        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}
        if not isinstance(poste_response, dict):
            poste_response = {}
        poste_response["pdf_ricevuta_cliente_url"] = pdf_cliente_url
        poste_response["pdf_cliente_generato_da"] = "dashboard_genera_ricevuta_cliente"
        poste_response["pdf_cliente_generato_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        supabase.table("pratiche").update({
            "pdf_ricevuta_cliente_url": pdf_cliente_url,
            "poste_response": poste_response,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).eq("id", pratica_id).execute()
        return RedirectResponse(
            url=pdf_cliente_url if apri else "/dashboard/pratiche",
            status_code=303
        )
    except Exception as e:
        return HTMLResponse(
            f"""
            <html>
            <body style="font-family:Arial;padding:30px;">
                <h2>Errore generazione ricevuta cliente</h2>
                <pre>{str(e)}</pre>
                <a href="/dashboard/pratiche">← Torna alla dashboard</a>
            </body>
            </html>
            """,
            status_code=500
        )

@app.get("/dashboard/pratiche/telegramma-manuale/{pratica_id}", response_class=HTMLResponse)
def dashboard_telegramma_manuale_form(pratica_id: str):
    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return HTMLResponse("<h2>Pratica non trovata</h2>", status_code=404)

        pratica = pratica_res.data

        return f"""
        <html>
        <head>
            <title>Telegramma manuale Poste</title>
            <style>
                body {{
                    font-family: Arial, sans-serif;
                    background: #f4f6fb;
                    padding: 30px;
                }}
                .box {{
                    max-width: 720px;
                    margin: auto;
                    background: white;
                    border-radius: 18px;
                    padding: 28px;
                    box-shadow: 0 10px 30px rgba(0,0,0,.08);
                }}
                h1 {{ margin-top: 0; }}
                label {{
                    display: block;
                    margin-top: 16px;
                    font-weight: 700;
                }}
                input {{
                    width: 100%;
                    padding: 12px;
                    margin-top: 6px;
                    border: 1px solid #d5d9e2;
                    border-radius: 10px;
                    font-size: 16px;
                }}
                button {{
                    margin-top: 24px;
                    background: #16a34a;
                    color: white;
                    border: none;
                    padding: 14px 22px;
                    border-radius: 12px;
                    font-size: 16px;
                    font-weight: 800;
                    cursor: pointer;
                }}
                a {{
                    display: inline-block;
                    margin-top: 18px;
                    color: #2563eb;
                    font-weight: 700;
                    text-decoration: none;
                }}
                .note {{
                    background: #fff7ed;
                    padding: 12px;
                    border-radius: 10px;
                    margin-top: 16px;
                }}
            </style>
        </head>
        <body>
            <div class="box">
                <h1>📨 Telegramma manuale Poste</h1>

                <p><b>Ordine:</b> {pratica.get("order_name")}</p>
                <p><b>Email cliente:</b> {pratica.get("cliente_email") or "-"}</p>
                <p><b>Stato attuale:</b> {pratica.get("stato")}</p>

                <div class="note">
                    Compila questi dati solo dopo aver completato l’invio/pagamento sul portale Poste.
                </div>

                <form method="post" enctype="multipart/form-data">
                    <label>Identificativo Poste opzionale</label>
                    <input name="identificativo_poste" placeholder="Es. 12044660">

                    <label>PDF ricevuta / copia mittente Poste</label>
                    <input type="file" name="pdf_file" accept="application/pdf" required>

                    <button type="submit">✅ Carica PDF e salva invio Poste</button>
                </form>

                <a href="/dashboard/pratiche">← Torna alla dashboard</a>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        return HTMLResponse(f"<h2>Errore</h2><pre>{str(e)}</pre>", status_code=500)

def _ecx_dict(value):
    if isinstance(value, dict):
        return value

    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return {}

    return {}


def _ecx_addr_label(data):
    """
    Legge mittente/destinatario sia da campi strutturati
    sia dal campo raw già salvato nelle pratiche.
    """

    raw_text = ""
    data_dict = {}

    if isinstance(data, dict):
        data_dict = data
        raw_text = str(
            data.get("raw")
            or data.get("full")
            or data.get("testo")
            or data.get("label")
            or ""
        ).strip()

    elif isinstance(data, str):
        try:
            parsed = json.loads(data)

            if isinstance(parsed, dict):
                data_dict = parsed
                raw_text = str(
                    parsed.get("raw")
                    or parsed.get("full")
                    or parsed.get("testo")
                    or parsed.get("label")
                    or ""
                ).strip()
            else:
                raw_text = data.strip()

        except Exception:
            raw_text = data.strip()

    nome = str(
        data_dict.get("nome")
        or data_dict.get("nominativo")
        or data_dict.get("ragione_sociale")
        or data_dict.get("ragioneSociale")
        or data_dict.get("full_name")
        or ""
    ).strip()

    via = str(
        data_dict.get("via")
        or data_dict.get("indirizzo")
        or data_dict.get("address")
        or data_dict.get("strada")
        or ""
    ).strip()

    civico = str(data_dict.get("civico") or "").strip()
    cap = str(data_dict.get("cap") or "").strip()

    comune = str(
        data_dict.get("comune")
        or data_dict.get("citta")
        or data_dict.get("città")
        or ""
    ).strip()

    provincia = str(
        data_dict.get("provincia")
        or data_dict.get("prov")
        or ""
    ).strip()

    indirizzo = " ".join([x for x in [via, civico] if x]).strip()

    localita = " ".join([
        x for x in [
            cap,
            comune,
            f"({provincia})" if provincia else ""
        ] if x
    ]).strip()

    # Fallback per dati tipo:
    # "SALVATORE DEL LIBANO - Viale Stefano D'Arrigo 321, 00131 Roma (RM)"
    if raw_text and (not nome or not indirizzo):
        parts = [
            x.strip()
            for x in raw_text.replace("\n", " - ").split(" - ")
            if x.strip()
        ]

        if parts:
            if not nome:
                nome = parts[0]

            if len(parts) >= 2 and not indirizzo:
                indirizzo = parts[1]

            if len(parts) >= 3 and not localita:
                localita = " - ".join(parts[2:])

        if not nome and raw_text:
            nome = raw_text

    return nome or "-", indirizzo or "-", localita or "-"


def genera_pdf_cliente_telegramma_bytes(
    pratica: dict,
    numero_accettazione: str,
    numero_telegramma: str
):
    """
    Genera ricevuta cliente Eccomi Posta per Telegramma.
    NON include importo/costo Poste.
    """
    buffer = BytesIO()

    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    y = height - 2.2 * cm

    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(width / 2, y, "ECCOMI POSTA")

    y -= 0.7 * cm
    c.setFont("Helvetica", 12)
    c.drawCentredString(width / 2, y, "Servizi Postali Digitali")

    y -= 1.1 * cm
    c.line(2 * cm, y, width - 2 * cm, y)

    y -= 1.3 * cm
    c.setFont("Helvetica-Bold", 20)
    c.drawString(2 * cm, y, "Ricevuta di invio Telegramma")

    y -= 1.2 * cm

    box_x = 2 * cm
    box_y = y - 2.2 * cm
    box_w = width - 4 * cm
    box_h = 2.4 * cm

    c.roundRect(box_x, box_y, box_w, box_h, 8, stroke=1, fill=0)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(box_x + 0.6 * cm, y - 0.5 * cm, "Numero Accettazione")
    c.drawString(box_x + 8.2 * cm, y - 0.5 * cm, "Stato")

    c.setFont("Helvetica-Bold", 15)
    c.drawString(box_x + 0.6 * cm, y - 1.25 * cm, str(numero_accettazione or "-"))

    c.setFont("Helvetica", 12)
    c.drawString(box_x + 8.2 * cm, y - 1.25 * cm, "Accettato da Poste Italiane")

    y = box_y - 1.2 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Numero Telegramma")
    y -= 0.55 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, str(numero_telegramma or "-"))

    y -= 1.1 * cm

    mitt_nome, mitt_indirizzo, mitt_localita = _ecx_addr_label(pratica.get("mittente"))
    dest_nome, dest_indirizzo, dest_localita = _ecx_addr_label(pratica.get("destinatario"))

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Mittente")
    y -= 0.55 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, mitt_nome or "-")
    y -= 0.45 * cm
    c.drawString(2 * cm, y, mitt_indirizzo or "-")
    y -= 0.45 * cm
    c.drawString(2 * cm, y, mitt_localita or "-")

    y -= 1.0 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Destinatario")
    y -= 0.55 * cm
    c.setFont("Helvetica", 11)
    c.drawString(2 * cm, y, dest_nome or "-")
    y -= 0.45 * cm
    c.drawString(2 * cm, y, dest_indirizzo or "-")
    y -= 0.45 * cm
    c.drawString(2 * cm, y, dest_localita or "-")

    y -= 1.0 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Data operazione")
    c.setFont("Helvetica", 11)
    c.drawString(6 * cm, y, datetime.datetime.now().strftime("%d/%m/%Y %H:%M"))

    y -= 1.6 * cm

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "Scopri anche gli altri servizi Eccomi Posta")
    y -= 0.55 * cm

    c.setFont("Helvetica", 10)
    servizi = [
        "Telegramma Online",
        "Raccomandata con ricevuta di ritorno",
        "Visure e certificati",
        "Spedizione buste e pacchi",
        "Servizi postali per aziende"
    ]

    for servizio in servizi:
        c.drawString(2.4 * cm, y, f"• {servizio}")
        y -= 0.42 * cm

    c.line(2 * cm, 2.0 * cm, width - 2 * cm, 2.0 * cm)
    c.setFont("Helvetica", 8)
    c.drawString(
        2 * cm,
        1.55 * cm,
        "Documento di conferma del servizio Eccomi Posta. Non costituisce documento fiscale."
    )

    c.save()

    buffer.seek(0)
    return buffer.getvalue()

def ensure_pdf_cliente_telegramma(pratica: dict):
    """
    Garantisce che un Telegramma INVIATO_POSTE abbia il PDF cliente pulito.
    Se manca pdf_ricevuta_cliente_url, lo genera, lo salva su Supabase
    e aggiorna la pratica.
    """
    pratica_id = pratica.get("id")

    pdf_esistente = pratica.get("pdf_ricevuta_cliente_url")
    if pdf_esistente:
        return pdf_esistente

    poste_response = pratica.get("poste_response") or {}

    if isinstance(poste_response, str):
        try:
            poste_response = json.loads(poste_response)
        except Exception:
            poste_response = {}

    flow = (
        poste_response.get("telegramma_flow_complete")
        or poste_response.get("complete_response")
        or {}
    )

    submit_response = poste_response.get("submit_response") or {}
    submit_result = (
        poste_response.get("submit_result")
        or submit_response.get("submit_result")
        or {}
    )

    telegramma_obj = (
        submit_result.get("telegramma")
        or poste_response.get("telegramma")
        or {}
    )

    numero_accettazione = (
        pratica.get("numero_raccomandata")
        or flow.get("numero_accettazione")
        or poste_response.get("numero_accettazione")
        or ""
    )

    numero_telegramma = (
        flow.get("id_telegramma")
        or poste_response.get("id_telegramma")
        or poste_response.get("numero_telegramma")
        or ""
    )

    if not numero_telegramma:
        try:
            dest = telegramma_obj.get("Destinatari") or {}
            tg_dest = dest.get("TelegrammaDestinatario")

            if isinstance(tg_dest, list) and tg_dest:
                numero_telegramma = tg_dest[0].get("IDTelegramma") or ""
            elif isinstance(tg_dest, dict):
                numero_telegramma = tg_dest.get("IDTelegramma") or ""
        except Exception:
            numero_telegramma = ""

    if not numero_accettazione:
        raise ValueError("Numero accettazione Telegramma mancante: impossibile generare PDF cliente")

    pdf_cliente_bytes = genera_pdf_cliente_telegramma_bytes(
        pratica=pratica,
        numero_accettazione=numero_accettazione,
        numero_telegramma=numero_telegramma
    )

    storage_path_cliente = f"telegrammi/{pratica_id}/ricevuta_cliente.pdf"

    supabase.storage.from_("eccomi-posta").upload(
        storage_path_cliente,
        pdf_cliente_bytes,
        file_options={
            "content-type": "application/pdf",
            "upsert": "true"
        }
    )

    pdf_cliente_url = supabase.storage.from_("eccomi-posta").get_public_url(
        storage_path_cliente
    )

    poste_response["pdf_ricevuta_cliente_url"] = pdf_cliente_url
    poste_response["pdf_cliente_generato_da"] = "ensure_pdf_cliente_telegramma"

    supabase.table("pratiche").update({
        "pdf_ricevuta_cliente_url": pdf_cliente_url,
        "poste_response": poste_response,
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).eq("id", pratica_id).execute()

    pratica["pdf_ricevuta_cliente_url"] = pdf_cliente_url
    pratica["poste_response"] = poste_response

    return pdf_cliente_url

@app.post("/dashboard/pratiche/telegramma-manuale/{pratica_id}")
async def dashboard_telegramma_manuale_save(
    pratica_id: str,
    identificativo_poste: str = Form(""),
    pdf_file: UploadFile = File(...)
):
    try:
        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "TELEGRAMMA_MANUALE_SAVE",
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        content = await pdf_file.read()

        estratti = estrai_dati_pdf_telegramma_poste(content)

        numero_accettazione = estratti.get("numero_accettazione")
        numero_telegramma = estratti.get("numero_telegramma")
        importo = estratti.get("importo")

        if not numero_accettazione or not numero_telegramma:
            return {
                "success": False,
                "step": "PDF_TELEGRAMMA_DATI_NON_LETTI",
                "error": "Non sono riuscito a leggere Numero Accettazione o Numero Telegramma dal PDF.",
                "numero_accettazione": numero_accettazione,
                "numero_telegramma": numero_telegramma,
                "importo": importo,
                "testo_estratto": estratti.get("text", "")[:3000]
            }

        # 1) PDF Poste originale: archivio interno
        storage_path_poste = f"telegrammi/{pratica_id}/ricevuta_poste_originale.pdf"

        supabase.storage.from_("eccomi-posta").upload(
            storage_path_poste,
            content,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_poste_originale_url = supabase.storage.from_("eccomi-posta").get_public_url(
            storage_path_poste
        )

        # 2) PDF cliente Eccomi: pulito, senza costo Poste
        pdf_cliente_bytes = genera_pdf_cliente_telegramma_bytes(
            pratica=pratica,
            numero_accettazione=numero_accettazione,
            numero_telegramma=numero_telegramma
        )

        storage_path_cliente = f"telegrammi/{pratica_id}/ricevuta_cliente.pdf"

        supabase.storage.from_("eccomi-posta").upload(
            storage_path_cliente,
            pdf_cliente_bytes,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_cliente_url = supabase.storage.from_("eccomi-posta").get_public_url(
            storage_path_cliente
        )

        poste_payload = {
            "step": "TELEGRAMMA_MANUALE_INVIATO",
            "note": "Invio Telegramma effettuato da portale Poste",
            "identificativo_poste": identificativo_poste,
            "numero_accettazione": numero_accettazione,
            "numero_telegramma": numero_telegramma,
            "importo_poste_interno": importo,
            "pdf_poste_originale_url": pdf_poste_originale_url,
            "pdf_ricevuta_cliente_url": pdf_cliente_url,
            "manual_sent_at": now_iso,
            "pdf_extract_success": estratti.get("success"),
            "pdf_extract_preview": estratti.get("text", "")[:1000]
        }

        update_payload = {
            "stato": "INVIATO_POSTE",
            "numero_raccomandata": numero_accettazione,
            "poste_response": poste_payload,
            "pdf_ricevuta_cliente_url": pdf_cliente_url,
            "email_sent": False,
            "updated_at": now_iso
        }

        supabase.table("pratiche") \
            .update(update_payload) \
            .eq("id", pratica_id) \
            .execute()

        return RedirectResponse(
            url="/dashboard/pratiche",
            status_code=303
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_TELEGRAMMA_MANUALE_SAVE",
            "pratica_id": pratica_id,
            "error": str(e)
        }


@app.get("/dashboard/pratiche/invia-email-cliente/{pratica_id}")
def dashboard_invia_email_cliente(pratica_id: str):
    """
    Invia o reinvia email cliente per una pratica già INVIATO_POSTE.
    NON chiama Poste.
    NON finalizza.
    NON genera costi.

    Raccomandata:
    - usa ordine H2H collegato

    Telegramma:
    - usa la pratica
    - usa solo pdf_ricevuta_cliente_url, cioè la ricevuta Eccomi pulita
    - NON usa il PDF originale Poste
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        if pratica.get("stato") != "INVIATO_POSTE":
            return {
                "success": False,
                "error": "Email cliente disponibile solo per pratiche INVIATO_POSTE",
                "stato": pratica.get("stato"),
                "pratica_id": pratica_id
            }

        tipo_servizio = (pratica.get("tipo_servizio") or "").upper()
        cliente_email = pratica.get("cliente_email") or pratica.get("email_to") or ""

        if not cliente_email:
            return {
                "success": False,
                "error": "Email cliente mancante",
                "pratica_id": pratica_id,
                "order_name": pratica.get("order_name")
            }

        email_fn = globals().get("invia_email_cliente_raccomandata")

        if not callable(email_fn):
            return {
                "success": False,
                "error": "Funzione invia_email_cliente_raccomandata non disponibile"
            }

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        h2h_order_id = None
        ordine = None
        pdf_cliente_url = ""
        email_subject = ""

        # ==========================================================
        # TELEGRAMMA — flusso manuale/portale Poste
        # ==========================================================
        if tipo_servizio == "TELEGRAMMA":
            pdf_cliente_url = ensure_pdf_cliente_telegramma(pratica)
            
            poste_response = pratica.get("poste_response") or {}

            if isinstance(poste_response, str):
                try:
                    poste_response = json.loads(poste_response)
                except Exception:
                    poste_response = {}

            numero_accettazione = (
                pratica.get("numero_raccomandata")
                or poste_response.get("numero_accettazione")
                or ""
            )

            numero_telegramma = (
                poste_response.get("numero_telegramma")
                or ""
            )

            identificativo_poste = (
                poste_response.get("identificativo_poste")
                or ""
            )

            # Finto ordine compatibile con la funzione email esistente
            ordine = {
                "id": f"telegramma-manuale-{pratica_id}",
                "pratica_id": pratica_id,
                "order_id": pratica.get("order_id"),
                "order_name": pratica.get("order_name"),
                "shopify_order_name": pratica.get("shopify_order_name"),
                "tipo_servizio": "TELEGRAMMA",
                "servizio": "TELEGRAMMA",
                "cliente_email": cliente_email,
                "email": cliente_email,
                "numero_raccomandata": numero_accettazione,
                "numero_accettazione": numero_accettazione,
                "numero_telegramma": numero_telegramma,
                "identificativo_poste": identificativo_poste,
                "pdf_ricevuta_cliente_url": pdf_cliente_url,
                "pdf_url": pdf_cliente_url,
                "created_at": pratica.get("created_at"),
                "updated_at": pratica.get("updated_at")
            }

            email_subject = f"Il tuo telegramma {pratica.get('order_name') or ''} è stato inviato"

        # ==========================================================
        # RACCOMANDATA — flusso H2H già esistente
        # ==========================================================
        else:
            h2h_order_id = resolve_h2h_order_id(pratica_id)

            if not h2h_order_id:
                return {
                    "success": False,
                    "error": "Ordine H2H collegato non trovato",
                    "pratica_id": pratica_id
                }

            ordine_res = supabase.table("poste_h2h_orders") \
                .select("*") \
                .eq("id", h2h_order_id) \
                .single() \
                .execute()

            if not ordine_res.data:
                return {
                    "success": False,
                    "error": "Ordine H2H non trovato",
                    "h2h_order_id": h2h_order_id
                }

            ordine = ordine_res.data

            pdf_cliente_url = (
                ordine.get("pdf_ricevuta_cliente_url")
                or pratica.get("pdf_ricevuta_cliente_url")
                or pratica.get("pdf_url")
                or ""
            )

            email_subject = f"La tua raccomandata {pratica.get('order_name') or ''} è stata inviata"

        if not pdf_cliente_url:
            return {
                "success": False,
                "error": "PDF cliente non disponibile",
                "pratica_id": pratica_id,
                "tipo_servizio": tipo_servizio
            }

        # ==========================================================
        # INVIO EMAIL + CCN INTERNO
        # Passa sales@eccomionline.com solo se la funzione email lo supporta
        # ==========================================================
        email_kwargs = {
            "ordine": ordine,
            "pratica": pratica,
            "pdf_cliente_url": pdf_cliente_url
        }

        try:
            params = inspect.signature(email_fn).parameters

            if "internal_bcc_email" in params:
                email_kwargs["internal_bcc_email"] = INTERNAL_BCC_EMAIL
            elif "bcc_email" in params:
                email_kwargs["bcc_email"] = INTERNAL_BCC_EMAIL
            elif "bcc" in params:
                email_kwargs["bcc"] = INTERNAL_BCC_EMAIL

        except Exception:
            pass

        email_result = email_fn(**email_kwargs)

        email_ok = True

        if isinstance(email_result, dict) and email_result.get("success") is False:
            email_ok = False

        update_payload = {
            "email_sent": email_ok,
            "email_to": cliente_email,
            "email_subject": email_subject,
            "email_error": None if email_ok else str(email_result),
            "updated_at": now_iso
        }

        if email_ok:
            update_payload["email_sent_at"] = now_iso
            update_payload["email_resend_id"] = str(uuid.uuid4())

        supabase.table("pratiche") \
            .update(update_payload) \
            .eq("id", pratica_id) \
            .execute()

        return RedirectResponse(
            url="/dashboard/pratiche?email=inviata" if email_ok else "/dashboard/pratiche?email=errore",
            status_code=303
        )
        
    except Exception as e:
        try:
            supabase.table("pratiche") \
                .update({
                    "email_sent": False,
                    "email_error": str(e),
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }) \
                .eq("id", pratica_id) \
                .execute()
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_INVIO_EMAIL_CLIENTE",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/anteprima-email-cliente/{pratica_id}", response_class=HTMLResponse)
def dashboard_anteprima_email_cliente(pratica_id: str):
    """
    Anteprima HTML della mail cliente.
    NON invia email.
    NON chiama Resend.
    Serve solo per controllare testo, bottoni e PDF.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return HTMLResponse(
                "<h2>Pratica non trovata</h2>",
                status_code=404
            )

        pratica = pratica_res.data

        tipo_servizio = (pratica.get("tipo_servizio") or "RACCOMANDATA").upper()
        is_telegramma = tipo_servizio == "TELEGRAMMA"

        cliente_email = pratica.get("cliente_email") or pratica.get("email_to") or ""
        shopify_order_name = (
            pratica.get("shopify_order_name")
            or pratica.get("order_name")
            or pratica.get("order_id")
            or ""
        )

        numero_raccomandata = pratica.get("numero_raccomandata") or ""

        pdf_url = (
            pratica.get("pdf_ricevuta_cliente_url")
            or pratica.get("pdf_url")
            or ""
        )

        titolo_mail = (
            "Il tuo telegramma è stato inviato"
            if is_telegramma
            else "La tua raccomandata è stata inviata"
        )

        testo_mail = (
            "la tua pratica Eccomi Posta è stata lavorata correttamente e il telegramma è stato inviato tramite Poste Italiane."
            if is_telegramma
            else "la tua pratica Eccomi Posta è stata lavorata correttamente e la raccomandata è stata inviata tramite Poste Italiane."
        )

        numero_label = (
            "Numero accettazione"
            if is_telegramma
            else "Numero raccomandata"
        )

        if is_telegramma:
            subject = (
                f"Il tuo Telegramma Eccomi Posta è stato inviato - N. {numero_raccomandata}"
                if numero_raccomandata
                else "Il tuo Telegramma Eccomi Posta è stato inviato"
            )
        else:
            subject = (
                f"La tua raccomandata Eccomi Posta è stata inviata - {numero_raccomandata}"
                if numero_raccomandata
                else "La tua raccomandata Eccomi Posta è stata inviata"
            )
            
        tracking_button = ""

        if numero_raccomandata and not is_telegramma:
            tracking_button = f"""
            <p style="margin:18px 0;">
                <a href="https://www.poste.it/cerca/index.html#/risultati-spedizioni/{numero_raccomandata}"
                   target="_blank"
                   style="background:#2563eb;color:white;padding:12px 18px;
                          border-radius:10px;text-decoration:none;font-weight:bold;
                          display:inline-block;">
                    Traccia la raccomandata
                </a>
            </p>
            """

        pdf_cliente_button = ""

        if pdf_url:
            label_pdf = (
                "Scarica ricevuta Telegramma"
                if is_telegramma
                else "Scarica ricevuta Eccomi Posta"
            )

            pdf_cliente_button = f"""
            <p style="margin:18px 0;">
                <a href="{pdf_url}"
                   target="_blank"
                   style="background:#15803d;color:white;padding:12px 18px;
                          border-radius:10px;text-decoration:none;font-weight:bold;
                          display:inline-block;">
                    {label_pdf}
                </a>
            </p>
            """

        html = f"""
        <div style="font-family:Arial,Helvetica,sans-serif;background:#f4f6f9;
                    padding:24px;color:#111827;">
            <div style="max-width:640px;margin:0 auto 16px auto;
                        background:#fff7ed;border:1px solid #fed7aa;
                        border-radius:14px;padding:14px 18px;color:#9a3412;">
                <strong>ANTEPRIMA EMAIL — NON INVIATA</strong><br>
                Destinatario previsto: {cliente_email or "-"}<br>
                Oggetto previsto: {subject}
            </div>

            <div style="max-width:640px;margin:0 auto;background:white;
                        border-radius:16px;padding:26px;">
                <h1 style="margin-top:0;color:#0f172a;">
                    {titolo_mail}
                </h1>

                <p>
                    Ciao,<br>
                    {testo_mail}
                </p>

                <div style="background:#f8fafc;border-radius:12px;padding:16px;margin:20px 0;">
                    <p><strong>Ordine:</strong> {shopify_order_name or "-"}</p>
                    <p><strong>{numero_label}:</strong> {numero_raccomandata or "-"}</p>
                </div>

                {tracking_button}
                {pdf_cliente_button}

                <hr style="border:none;border-top:1px solid #e5e7eb;margin:26px 0;">

                <p>
                    Hai bisogno di inviare un nuovo documento, una raccomandata,
                    un telegramma, Posta1 o Posta4?
                </p>

                <p style="margin:22px 0;">
                    <a href="{ECCOMI_POSTA_CTA_URL}"
                       style="background:#f97316;color:white;padding:12px 18px;
                              border-radius:10px;text-decoration:none;font-weight:bold;">
                        Vai a Eccomi Posta
                    </a>
                </p>

                <p style="font-size:12px;color:#6b7280;margin-top:28px;">
                    Eccomi Posta — Servizi postali digitali<br>
                    www.eccomionline.com
                </p>
            </div>
        </div>
        """

        return HTMLResponse(html)

    except Exception as e:
        return HTMLResponse(
            f"<h2>Errore anteprima email</h2><pre>{str(e)}</pre>",
            status_code=500
        )

@app.post("/resend/webhook")
async def resend_webhook(request: Request):
    """
    Riceve eventi email da Resend e aggiorna la pratica:
    - email.sent
    - email.delivered
    - email.opened
    - email.clicked
    - email.bounced
    - email.failed
    - email.complained
    """

    try:
        payload = await request.json()

        event_type = payload.get("type") or ""
        created_at = (
            payload.get("created_at")
            or datetime.datetime.now(datetime.timezone.utc).isoformat()
        )

        data = payload.get("data") or {}

        email_id = (
            data.get("email_id")
            or data.get("id")
            or payload.get("email_id")
            or payload.get("id")
            or ""
        )

        if not email_id:
            return {
                "success": False,
                "error": "email_id mancante",
                "event_type": event_type,
                "payload": payload
            }

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("email_resend_id", email_id) \
            .limit(1) \
            .execute()

        if not pratica_res.data:
            return {
                "success": True,
                "matched": False,
                "message": "Evento Resend ricevuto ma pratica non trovata",
                "email_id": email_id,
                "event_type": event_type
            }

        pratica = pratica_res.data[0]
        pratica_id = pratica.get("id")

        update_payload = {
            "email_last_event": event_type,
            "email_last_event_at": created_at,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        if event_type == "email.sent":
            update_payload["email_status"] = "sent"

        elif event_type == "email.delivered":
            update_payload["email_status"] = "delivered"
            update_payload["email_delivered_at"] = created_at

        elif event_type == "email.opened":
            update_payload["email_status"] = "opened"
            update_payload["email_opened_at"] = created_at

        elif event_type == "email.clicked":
            update_payload["email_status"] = "clicked"

        elif event_type == "email.bounced":
            update_payload["email_status"] = "bounced"
            update_payload["email_bounced_at"] = created_at
            update_payload["email_error"] = str(data.get("bounce") or data)

        elif event_type == "email.failed":
            update_payload["email_status"] = "failed"
            update_payload["email_failed_at"] = created_at
            update_payload["email_error"] = str(data)

        elif event_type == "email.complained":
            update_payload["email_status"] = "complained"
            update_payload["email_error"] = "Il destinatario ha segnalato la mail come spam"

        current_events = pratica.get("email_events") or []

        if not isinstance(current_events, list):
            current_events = []

        current_events.append({
            "type": event_type,
            "email_id": email_id,
            "created_at": created_at,
            "data": data
        })

        update_payload["email_events"] = current_events[-20:]

        supabase.table("pratiche") \
            .update(update_payload) \
            .eq("id", pratica_id) \
            .execute()

        return {
            "success": True,
            "matched": True,
            "step": "RESEND_WEBHOOK_RECEIVED",
            "event_type": event_type,
            "email_id": email_id,
            "pratica_id": pratica_id,
            "order_name": pratica.get("order_name")
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RESEND_WEBHOOK",
            "error": str(e)
        }

@app.get("/dashboard/pratiche/invia-poste/{pratica_id}")
def dashboard_invia_poste(pratica_id: str):
    """
    Pulsante dashboard: invia a Poste e torna alla dashboard.
    Non mostra JSON tecnico al cliente/operatore.
    """

    try:
        h2h_order_id = resolve_h2h_order_id(pratica_id)

        if not h2h_order_id:
            try:
                supabase.table("pratiche").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": {
                        "raw": "Impossibile trovare ordine H2H collegato alla pratica"
                    },
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", pratica_id).execute()
            except Exception:
                pass

            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        result = process_poste_order(h2h_order_id)

        if not result.get("success"):
            errore = result.get("error") or str(result)

            try:
                supabase.table("poste_h2h_orders").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": errore
                }).eq("id", h2h_order_id).execute()
            except Exception:
                pass

            try:
                supabase.table("pratiche").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": {
                        "raw": errore
                    },
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", pratica_id).execute()
            except Exception:
                pass

            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        return RedirectResponse(
            url="/dashboard/pratiche?stato=PREZZATA_DA_CONFERMARE",
            status_code=302
        )

    except Exception as e:
        print("ERRORE dashboard_invia_poste:", str(e))

        try:
            supabase.table("pratiche").update({
                "stato": "ERRORE_POSTE",
                "poste_response": {
                    "raw": str(e)
                },
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }).eq("id", pratica_id).execute()
        except Exception:
            pass

        return RedirectResponse(
            url="/dashboard/pratiche?stato=ERRORE_POSTE",
            status_code=302
        )

@app.get("/poste/h2h/preview-xml/{pratica_id}", response_class=HTMLResponse)
def preview_xml_h2h_pratica(pratica_id: str):
    try:
        result = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not result.data:
            return """
            <html>
            <body style="font-family:Arial;padding:30px;">
                <h1>Pratica non trovata</h1>
                <a href="/dashboard/pratiche">← Torna alla dashboard</a>
            </body>
            </html>
            """

        pratica = result.data
        has_rr = bool_from_any(pratica.get("ricevuta_ritorno"))

        def safe_html(value):
            return (
                str(value or "-")
                .replace("&", "&amp;")
                .replace("<", "&lt;")
                .replace(">", "&gt;")
            )

        history = HistoryPlugin()
        client, service = poste_client(timeout=60, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")
        DatiRicevutaType = client.get_type("ns0:DatiRicevuta")

        # =====================================================
        # MITTENTE / DESTINATARIO DINAMICI DALLA PRATICA
        # NON USA DATI FISSI DI TEST
        # NON INVIA NULLA A POSTE
        # =====================================================

        mittente_data = pratica.get("mittente") or {}
        destinatario_data = pratica.get("destinatario") or {}

        nom_mitt = build_nominativo_h2h_from_data(
            mittente_data,
            NominativoType,
            IndirizzoType,
            label="mittente"
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        dati_ricevuta = DatiRicevutaType(
            Nominativo=nom_mitt
        ) if has_rr else None

        nom_dest = build_nominativo_h2h_from_data(
            destinatario_data,
            NominativoType,
            IndirizzoType,
            label="destinatario"
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        # =====================================================
        # PDF FAKE SOLO PER ANTEPRIMA XML
        # =====================================================

        pdf_bytes = b"%PDF-1.4 PREVIEW ECCOMI POSTA\n%%EOF"
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest().upper()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        rol_submit = {
            "Mittente": mittente,
            **({"DatiRicevuta": dati_ricevuta} if has_rr else {}),
            "Destinatari": {
                "Destinatario": [destinatario]
            },
            "NumeroDestinatari": 1,
            "Documento": [documento],
            "Opzioni": {
                "OpzionidiStampa": {
                    "ResolutionX": 300,
                    "ResolutionY": 300,
                    "BW": True,
                    "FronteRetro": False,
                    "PageSize": "A4"
                },
                "SecurPaper": False,
                "DPM": False,
                "DataStampa": datetime.datetime.now().replace(microsecond=0),
                "InserisciMittente": True,
                "Archiviazione": False,
                "AnniArchiviazioneSpecified": False,
                "FirmaElettronica": False,
                "AnniArchiviazione": 0,
                "ArchiviazioneDocumenti": "NESSUNA"
            },
            "PrezzaturaSincrona": False,
            "Nazionale": True,
            "ForzaInvioDestinazioniValide": True
        }

        message = client.create_message(
            service,
            "Invio",
            IDRichiesta=f"PREVIEW-{uuid.uuid4()}",
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit=rol_submit
        )

        fix_wsa_to(message)

        xml_string = etree.tostring(
            message,
            pretty_print=True,
            encoding="unicode"
        )

        xml_safe = safe_html(xml_string)

        rr_status = "SÌ" if has_rr else "NO"
        rr_color = "#16a34a" if has_rr else "#dc2626"

        mittente_raw = ""
        destinatario_raw = ""

        if isinstance(mittente_data, dict):
            mittente_raw = mittente_data.get("raw") or json.dumps(
                mittente_data,
                ensure_ascii=False
            )
        else:
            mittente_raw = str(mittente_data or "")

        if isinstance(destinatario_data, dict):
            destinatario_raw = destinatario_data.get("raw") or json.dumps(
                destinatario_data,
                ensure_ascii=False
            )
        else:
            destinatario_raw = str(destinatario_data or "")

        return f"""
        <html>
        <head>
            <title>Anteprima XML H2H</title>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial;
                    background:#f4f6f9;
                    padding:30px;
                }}

                .card {{
                    background:white;
                    border-radius:14px;
                    padding:22px;
                    margin-bottom:20px;
                    box-shadow:0 2px 10px rgba(0,0,0,.06);
                }}

                pre {{
                    background:#111827;
                    color:#d1d5db;
                    padding:18px;
                    border-radius:12px;
                    overflow:auto;
                    max-height:650px;
                    white-space:pre-wrap;
                    word-break:break-word;
                    font-size:13px;
                    line-height:1.35;
                }}

                .badge {{
                    display:inline-block;
                    background:{rr_color};
                    color:white;
                    padding:8px 12px;
                    border-radius:999px;
                    font-weight:bold;
                }}

                .raw-box {{
                    background:#f9fafb;
                    border:1px solid #e5e7eb;
                    padding:12px;
                    border-radius:10px;
                    margin-top:8px;
                    line-height:1.5;
                }}

                a {{
                    color:#2563eb;
                    font-weight:bold;
                    text-decoration:none;
                }}

                .ok {{
                    color:#16a34a;
                    font-weight:bold;
                }}

                .warn {{
                    color:#dc2626;
                    font-weight:bold;
                }}
            </style>
        </head>

        <body>
            <h1>🧪 Anteprima XML H2H</h1>

            <p>
                <a href="/dashboard/pratiche">← Torna alla dashboard</a>
            </p>

            <div class="card">
                <h2>Pratica</h2>

                <p><strong>ID:</strong> {safe_html(pratica.get("id"))}</p>
                <p><strong>Ordine:</strong> {safe_html(pratica.get("shopify_order_name") or pratica.get("order_name") or "-")}</p>
                <p><strong>Servizio:</strong> {safe_html(pratica.get("tipo_servizio"))}</p>
                <p><strong>Ricevuta di ritorno:</strong> <span class="badge">{rr_status}</span></p>
                <p><strong>Invio reale a Poste:</strong> NO — questa è solo anteprima XML.</p>
            </div>

            <div class="card">
                <h2>Dati pratica letti da Supabase</h2>

                <p><strong>Mittente:</strong></p>
                <div class="raw-box">
                    {safe_html(mittente_raw)}
                </div>

                <p><strong>Destinatario:</strong></p>
                <div class="raw-box">
                    {safe_html(destinatario_raw)}
                </div>
            </div>

            <div class="card">
                <h2>Controllo rapido</h2>

                <p>
                    Se la pratica è RR, qui sotto deve comparire:
                    <strong>&lt;DatiRicevuta&gt;</strong>
                </p>

                <p>
                    Stato RR:
                    {"<span class='ok'>DatiRicevuta attiva</span>" if has_rr else "<span class='warn'>DatiRicevuta non attiva</span>"}
                </p>
            </div>

            <div class="card">
                <h2>XML generato</h2>
                <pre>{xml_safe}</pre>
            </div>
        </body>
        </html>
        """

    except Exception as e:
        errore = (
            str(e)
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )

        return f"""
        <html>
        <body style="font-family:Arial;padding:30px;">
            <h1>Errore anteprima XML</h1>
            <pre>{errore}</pre>
            <a href="/dashboard/pratiche">← Torna alla dashboard</a>
        </body>
        </html>
        """

@app.get("/dashboard/pratiche/ripara-h2h-order/{order_key}")
def dashboard_ripara_h2h_order(order_key: str, order_name: str = ""):
    """
    Ripara una pratica pagata ma senza riga tecnica H2H.
    Esempio:
    /dashboard/pratiche/ripara-h2h-order/1780159027281?order_name=%231386
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .or_(
                f"order_id.eq.{order_key},order_name.eq.{order_key},shopify_order_name.eq.{order_key}"
            ) \
            .limit(1) \
            .execute()

        if not pratica_res.data:
            pratica_res = supabase.table("pratiche") \
                .select("*") \
                .ilike("order_id", f"%{order_key}%") \
                .limit(1) \
                .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "order_key": order_key
            }

        pratica = pratica_res.data[0]
        pratica_id = pratica.get("id")
        pdf_url = pratica.get("pdf_url")

        if not pdf_url:
            return {
                "success": False,
                "error": "pdf_url mancante nella pratica",
                "pratica_id": pratica_id
            }

        order_name_finale = (
            order_name
            or pratica.get("shopify_order_name")
            or pratica.get("order_name")
            or pratica.get("order_id")
            or order_key
        )

        has_rr = bool_from_any(pratica.get("ricevuta_ritorno"))

        # 1. Aggiorna sempre la pratica
        supabase.table("pratiche") \
            .update({
                "stato": "RICEVUTO_PAGATO",
                "order_name": order_name_finale,
                "shopify_order_name": order_name_finale,
                "ricevuta_ritorno": has_rr,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }) \
            .eq("id", pratica_id) \
            .execute()

        # 2. Cerca se esiste già una riga H2H collegata al PDF
        h2h_esistente = supabase.table("poste_h2h_orders") \
            .select("id") \
            .eq("pdf_url", pdf_url) \
            .limit(1) \
            .execute()

        payload_full = {
            "pdf_url": pdf_url,
            "shopify_order_name": order_name_finale,
            "stato": "RICEVUTO_PAGATO",
            "ricevuta_ritorno": has_rr,
            "mittente": pratica.get("mittente") or {},
            "destinatario": pratica.get("destinatario") or {},
            "poste_response": "Riparazione H2H manuale da dashboard"
        }

        payload_light = {
            "pdf_url": pdf_url,
            "shopify_order_name": order_name_finale,
            "stato": "RICEVUTO_PAGATO",
            "poste_response": "Riparazione H2H manuale da dashboard"
        }

        if h2h_esistente.data:
            h2h_id = h2h_esistente.data[0].get("id")

            try:
                supabase.table("poste_h2h_orders") \
                    .update(payload_full) \
                    .eq("id", h2h_id) \
                    .execute()
            except Exception:
                supabase.table("poste_h2h_orders") \
                    .update(payload_light) \
                    .eq("id", h2h_id) \
                    .execute()

        else:
            try:
                supabase.table("poste_h2h_orders") \
                    .insert(payload_full) \
                    .execute()
            except Exception:
                supabase.table("poste_h2h_orders") \
                    .insert(payload_light) \
                    .execute()

        return RedirectResponse(
            url="/dashboard/pratiche",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "order_key": order_key
        }

# ============================================================
# PATCH RACCOMANDATA - PAGAMENTO SHOPIFY -> RICEVUTO_PAGATO + H2H
# ============================================================

def prop_value(props: dict, names, default=""):
    """
    Cerca una proprietà Shopify anche se il nome contiene emoji/spazi.
    """
    if not props:
        return default

    lowered = {
        str(k).strip().lower(): v
        for k, v in props.items()
    }

    for name in names:
        key = str(name).strip().lower()
        if key in lowered:
            return lowered[key]

    for k, v in props.items():
        k_low = str(k).lower()
        for name in names:
            if str(name).lower() in k_low:
                return v

    return default


def extract_racc_token_from_props(props: dict):
    text = " ".join([
        str(k) + " " + str(v)
        for k, v in (props or {}).items()
    ])

    match = re.search(r"RACC-\d{4}-SHOPIFY-\d+", text)

    if match:
        return match.group(0)

    return ""


def crea_o_aggiorna_h2h_da_pratica(pratica: dict, stato="RICEVUTO_PAGATO", note=""):
    """
    Crea o aggiorna la riga tecnica poste_h2h_orders collegata alla pratica.
    NON invia nulla a Poste.
    NON genera costi.
    """
    pdf_url = pratica.get("pdf_url")

    if not pdf_url:
        return None

    has_rr = bool_from_any(pratica.get("ricevuta_ritorno"))

    order_name = (
        pratica.get("shopify_order_name")
        or pratica.get("order_name")
        or pratica.get("order_id")
        or ""
    )

    payload_full = {
        "pdf_url": pdf_url,
        "shopify_order_name": order_name,
        "stato": stato,
        "ricevuta_ritorno": has_rr,
        "mittente": pratica.get("mittente") or {},
        "destinatario": pratica.get("destinatario") or {},
        "poste_response": note or "Preparazione H2H da pagamento Shopify"
    }

    payload_light = {
        "pdf_url": pdf_url,
        "shopify_order_name": order_name,
        "stato": stato,
        "poste_response": note or "Preparazione H2H da pagamento Shopify"
    }

    existing = supabase.table("poste_h2h_orders") \
        .select("id") \
        .eq("pdf_url", pdf_url) \
        .limit(1) \
        .execute()

    if existing.data:
        h2h_id = existing.data[0].get("id")

        try:
            supabase.table("poste_h2h_orders") \
                .update(payload_full) \
                .eq("id", h2h_id) \
                .execute()
        except Exception as e:
            print("H2H update full fallito, provo light:", str(e))

            supabase.table("poste_h2h_orders") \
                .update(payload_light) \
                .eq("id", h2h_id) \
                .execute()

        return h2h_id

    try:
        inserted = supabase.table("poste_h2h_orders") \
            .insert(payload_full) \
            .execute()
    except Exception as e:
        print("H2H insert full fallito, provo light:", str(e))

        inserted = supabase.table("poste_h2h_orders") \
            .insert(payload_light) \
            .execute()

    if inserted.data:
        return inserted.data[0].get("id")

    return None


@app.get("/dashboard/pratiche/marca-pagata/{pratica_id}")
def dashboard_marca_pratica_pagata(pratica_id: str, order_name: str = ""):
    """
    Ripara una pratica pagata rimasta in BOZZA_CHECKOUT.
    NON chiama Poste.
    NON genera costi.
    """
    try:
        result = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not result.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = result.data

        order_name_finale = (
            order_name
            or pratica.get("shopify_order_name")
            or pratica.get("order_name")
            or pratica.get("order_id")
            or ""
        )

        update_data = {
            "stato": "RICEVUTO_PAGATO",
            "order_name": order_name_finale,
            "shopify_order_name": order_name_finale,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }

        supabase.table("pratiche") \
            .update(update_data) \
            .eq("id", pratica_id) \
            .execute()

        pratica.update(update_data)

        h2h_id = crea_o_aggiorna_h2h_da_pratica(
            pratica=pratica,
            stato="RICEVUTO_PAGATO",
            note="Pratica marcata pagata manualmente da dashboard"
        )

        return RedirectResponse(
            url="/dashboard/pratiche",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "pratica_id": pratica_id
        }


@app.post("/shopify/raccomandata/order")
async def shopify_raccomandata_order(request: Request):
    """
    Webhook Shopify per ordini Raccomandata.
    Da collegare a Shopify su ORDINE PAGATO.
    Aggiorna BOZZA_CHECKOUT -> RICEVUTO_PAGATO e prepara H2H.
    NON invia a Poste.
    NON genera costi.
    """
    try:
        order = await request.json()

        order_id_raw = str(order.get("id") or "").strip()
        order_key = f"SHOPIFY-{order_id_raw}" if order_id_raw else ""
        order_name = str(order.get("name") or order_key).strip()
        email = order.get("email") or order.get("contact_email") or ""

        financial_status = str(order.get("financial_status") or "").lower().strip()

        # Sicurezza Eccomi Posta:
        # la raccomandata diventa lavorabile solo se Shopify conferma pagamento incassato.
        # Stati come "authorized" o "partially_paid" NON devono abilitare l'invio Poste.
        is_paid = financial_status == "paid"

        nuovo_stato = "RICEVUTO_PAGATO" if is_paid else "NON_PAGATO"

        risultati = []

        for item in order.get("line_items", []) or []:
            title = str(item.get("title") or "")
            props_preview = {}

            for p in item.get("properties", []) or []:
                name = str(p.get("name") or "").strip()
                value = p.get("value")

                if name:
                    props_preview[name] = value

            is_extra_rr = (
                str(props_preview.get("Tipo extra") or "").lower().strip() == "ricevuta di ritorno"
                or "RICEVUTA DI RITORNO" in title.upper()
            )

            if is_extra_rr:
                continue

            if "RACCOMANDATA" not in title.upper():
                continue

            props = {}

            for p in item.get("properties", []) or []:
                name = str(p.get("name") or "").strip()
                value = p.get("value")

                if name:
                    props[name] = value

            token = extract_racc_token_from_props(props)

            pratica = None

            # 1. Cerca per order_id SHOPIFY
            if order_key:
                res = supabase.table("pratiche") \
                    .select("*") \
                    .or_(
                        f"order_id.eq.{order_key},order_name.eq.{order_key},shopify_order_name.eq.{order_key}"
                    ) \
                    .order("created_at", desc=True) \
                    .limit(1) \
                    .execute()

                if res.data:
                    pratica = res.data[0]

            # 2. Cerca per token nel pdf_url
            if not pratica and token:
                res = supabase.table("pratiche") \
                    .select("*") \
                    .ilike("pdf_url", f"%{token}%") \
                    .order("created_at", desc=True) \
                    .limit(1) \
                    .execute()

                if res.data:
                    pratica = res.data[0]

            # 3. Cerca fallback per numero ordine raw
            if not pratica and order_id_raw:
                res = supabase.table("pratiche") \
                    .select("*") \
                    .ilike("order_id", f"%{order_id_raw}%") \
                    .order("created_at", desc=True) \
                    .limit(1) \
                    .execute()

                if res.data:
                    pratica = res.data[0]

            if not pratica:
                risultati.append({
                    "success": False,
                    "error": "Pratica raccomandata non trovata",
                    "order_id": order_key,
                    "order_name": order_name,
                    "token": token
                })
                continue

            pratica_id = pratica.get("id")

            mittente_raw = prop_value(props, ["Mittente", "mittente"], "")
            destinatario_raw = prop_value(props, ["Destinatario", "destinatario"], "")
            testo_racc = prop_value(props, ["Testo raccomandata", "testo"], "")
            oggetto_racc = prop_value(props, ["Oggetto", "oggetto"], "")
            firma_racc = prop_value(props, ["Firma", "firma"], "")
            metodo_invio = prop_value(props, ["Metodo invio", "metodo_invio"], "")
            nome_file_pdf = prop_value(props, ["Nome file PDF", "nome_file_pdf"], "")
            pagine_racc = prop_value(props, ["Pagine", "pagine"], "")
            token_pratica = prop_value(props, ["Token pratica", "token"], token or "")

            has_rr = (
                detect_ricevuta_ritorno(props)
                or bool_from_any(pratica.get("ricevuta_ritorno"))
            )

            update_data = {
                "stato": nuovo_stato,
                "order_id": order_key or pratica.get("order_id"),
                "order_name": order_name,
                "shopify_order_name": order_name,
                "cliente_email": email or pratica.get("cliente_email") or "",
                "ricevuta_ritorno": has_rr,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }

            if mittente_raw:
                update_data["mittente"] = {"raw": mittente_raw}

            if destinatario_raw:
                update_data["destinatario"] = {"raw": destinatario_raw}

            if testo_racc:
                update_data["testo"] = testo_racc

            poste_response_attuale = pratica.get("poste_response") or {}

            if not isinstance(poste_response_attuale, dict):
                poste_response_attuale = {}

            poste_response_attuale.update({
                "shopify_order_payload": {
                    "oggetto": oggetto_racc,
                    "firma": firma_racc,
                    "metodo_invio": metodo_invio,
                    "nome_file_pdf": nome_file_pdf,
                    "pagine": pagine_racc,
                    "token_pratica": token_pratica,
                    "ricevuta_ritorno": has_rr
                }
            })

            update_data["poste_response"] = poste_response_attuale


            supabase.table("pratiche") \
                .update(update_data) \
                .eq("id", pratica_id) \
                .execute()

            pratica.update(update_data)

            h2h_id = None

            if nuovo_stato == "RICEVUTO_PAGATO":
                h2h_id = crea_o_aggiorna_h2h_da_pratica(
                    pratica=pratica,
                    stato="RICEVUTO_PAGATO",
                    note=f"Webhook Shopify ordine pagato {order_name}"
                )

            risultati.append({
                "success": True,
                "pratica_id": pratica_id,
                "order_id": order_key,
                "order_name": order_name,
                "stato": nuovo_stato,
                "ricevuta_ritorno": has_rr,
                "h2h_id": h2h_id
            })

        return {
            "success": True,
            "order_id": order_key,
            "order_name": order_name,
            "financial_status": financial_status,
            "raccomandate_processate": len(risultati),
            "risultati": risultati
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/invia-diretto-poste/{pratica_id}")
def dashboard_invia_diretto_poste(pratica_id: str):
    """
    Invio diretto a Poste:
    - calcola/prezza
    - finalizza subito
    - può generare costo H2H
    """

    try:
        poste_invio_mode = os.getenv("POSTE_INVIO_MODE", "manual").strip().lower()

        direct_enabled = os.getenv(
            "POSTE_INVIO_DIRETTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        if poste_invio_mode != "auto" or not direct_enabled:
            print(
                "INVIO DIRETTO POSTE BLOCCATO:",
                {
                    "pratica_id": pratica_id,
                    "POSTE_INVIO_MODE": poste_invio_mode,
                    "POSTE_INVIO_DIRETTO_ENABLED": direct_enabled
                }
            )

            return RedirectResponse(
                url="/dashboard/pratiche",
                status_code=302
            )
        h2h_order_id = resolve_h2h_order_id(pratica_id)

        if not h2h_order_id:
            try:
                supabase.table("pratiche").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": {
                        "raw": "Impossibile trovare ordine H2H collegato alla pratica per invio diretto"
                    },
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", pratica_id).execute()
            except Exception:
                pass

            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", h2h_order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        stato_attuale = ordine_res.data.get("stato")

        if stato_attuale not in ["RICEVUTO_PAGATO", "IN_LAVORAZIONE"]:
            return RedirectResponse(
                url="/dashboard/pratiche",
                status_code=302
            )

        # 1. Prima invia tecnicamente a Poste e recupera/prepara la prezzatura
        process_result = process_poste_order(h2h_order_id)

        if not process_result.get("success"):
            errore = process_result.get("error") or str(process_result)

            try:
                supabase.table("poste_h2h_orders").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": errore
                }).eq("id", h2h_order_id).execute()
            except Exception:
                pass

            try:
                supabase.table("pratiche").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": {
                        "raw": errore
                    },
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", pratica_id).execute()
            except Exception:
                pass

            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        # 2. Poi finalizza realmente a Poste
        confirm_result = confirm_poste_order(h2h_order_id)

        if isinstance(confirm_result, dict) and not confirm_result.get("success"):
            errore = confirm_result.get("error") or str(confirm_result)

            try:
                supabase.table("poste_h2h_orders").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": errore
                }).eq("id", h2h_order_id).execute()
            except Exception:
                pass

            try:
                supabase.table("pratiche").update({
                    "stato": "ERRORE_POSTE",
                    "poste_response": {
                        "raw": errore
                    },
                    "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
                }).eq("id", pratica_id).execute()
            except Exception:
                pass

            return RedirectResponse(
                url="/dashboard/pratiche?stato=ERRORE_POSTE",
                status_code=302
            )

        return RedirectResponse(
            url="/dashboard/pratiche?stato=INVIATO_POSTE",
            status_code=302
        )

    except Exception as e:
        print("ERRORE dashboard_invia_diretto_poste:", str(e))

        try:
            supabase.table("pratiche").update({
                "stato": "ERRORE_POSTE",
                "poste_response": {
                    "raw": str(e)
                },
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }).eq("id", pratica_id).execute()
        except Exception:
            pass

        return RedirectResponse(
            url="/dashboard/pratiche?stato=ERRORE_POSTE",
            status_code=302
        )

@app.get("/dashboard/pratiche/raccomandata-test-calcola/{pratica_id}")
def dashboard_raccomandata_test_calcola(pratica_id: str):
    """
    TEST Raccomandata H2H separato.

    Usa SOLO ambiente Poste TEST:
    - POSTE_H2H_ROL_WSDL_TEST
    - POSTE_H2H_SERVICE_URL_TEST
    - POSTE_H2H_USERID_TEST
    - POSTE_H2H_PASSWORD_TEST
    - POSTE_H2H_CONTRACT_ID_TEST

    Fa:
    - Invio TEST
    - Valorizza TEST

    NON finalizza.
    NON manda email.
    NON cambia lo stato reale della pratica.
    NON salva numero raccomandata reale.
    NON usa produzione.
    """

    history = HistoryPlugin()

    try:
        # =====================================================
        # 1. Carica pratica
        # =====================================================

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        tipo_servizio = str(
            pratica.get("tipo_servizio") or ""
        ).upper().strip()

        if tipo_servizio != "RACCOMANDATA":
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_SERVIZIO_NON_VALIDO",
                "pratica_id": pratica_id,
                "tipo_servizio": tipo_servizio,
                "error": "Questo test è disponibile solo per Raccomandata"
            }

        pdf_url = pratica.get("pdf_url") or ""

        if not pdf_url:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_PDF_MANCANTE",
                "pratica_id": pratica_id,
                "error": "PDF documento mancante"
            }

        # =====================================================
        # 2. Scarica PDF documento
        # =====================================================

        response_pdf = requests.get(pdf_url, timeout=60)

        if response_pdf.status_code != 200:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_DOWNLOAD_PDF_KO",
                "pratica_id": pratica_id,
                "status_code": response_pdf.status_code,
                "error": "Impossibile scaricare PDF documento"
            }

        pdf_bytes = response_pdf.content
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest().upper()

        # =====================================================
        # 3. Client Poste TEST
        # =====================================================

        client, service = poste_client_test(
            timeout=120,
            extra_plugins=[history]
        )

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")
        RichiestaType = client.get_type("ns1:Richiesta")
        DatiRicevutaType = client.get_type("ns0:DatiRicevuta")

        has_rr = bool_from_any(pratica.get("ricevuta_ritorno"))

        # =====================================================
        # 4. Mittente / destinatario reali della pratica
        # =====================================================

        mittente_data = pratica.get("mittente") or {}
        destinatario_data = pratica.get("destinatario") or {}

        nom_mitt = build_nominativo_h2h_from_data(
            mittente_data,
            NominativoType,
            IndirizzoType,
            label="mittente"
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        dati_ricevuta = DatiRicevutaType(
            Nominativo=nom_mitt
        ) if has_rr else None

        nom_dest = build_nominativo_h2h_from_data(
            destinatario_data,
            NominativoType,
            IndirizzoType,
            label="destinatario"
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        # =====================================================
        # 5. Recupera ID richiesta TEST
        # =====================================================

        id_result = service.RecuperaIdRichiesta()

        id_richiesta_test = (
            getattr(id_result, "IDRichiesta", None)
            or str(id_result)
        )

        # =====================================================
        # 6. Invio TEST
        # =====================================================

        invio_result = service.Invio(
            IDRichiesta=id_richiesta_test,
            Cliente=POSTE_H2H_USERID_TEST,
            CodiceContratto=POSTE_H2H_CONTRACT_ID_TEST,
            ROLSubmit={
                "Mittente": mittente,
                **({"DatiRicevuta": dati_ricevuta} if has_rr else {}),
                "Destinatari": {
                    "Destinatario": [destinatario]
                },
                "NumeroDestinatari": 1,
                "Documento": [documento],
                "Opzioni": {
                    "OpzionidiStampa": {
                        "ResolutionX": 300,
                        "ResolutionY": 300,
                        "BW": True,
                        "FronteRetro": False,
                        "PageSize": "A4"
                    },
                    "SecurPaper": False,
                    "DPM": False,
                    "DataStampa": datetime.datetime.now().replace(microsecond=0),
                    "InserisciMittente": True,
                    "Archiviazione": False,
                    "AnniArchiviazioneSpecified": False,
                    "FirmaElettronica": False,
                    "AnniArchiviazione": 0,
                    "ArchiviazioneDocumenti": "NESSUNA"
                },
                "PrezzaturaSincrona": False,
                "Nazionale": True,
                "ForzaInvioDestinazioniValide": True
            }
        )

        xml_invio_sent = None
        xml_invio_received = None

        try:
            xml_invio_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_invio_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        guid_utente_test = getattr(invio_result, "GuidUtente", None)

        if not guid_utente_test:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_GUID_MANCANTE",
                "pratica_id": pratica_id,
                "id_richiesta_test": id_richiesta_test,
                "invio_result": str(invio_result),
                "xml_invio_sent": xml_invio_sent,
                "xml_invio_received": xml_invio_received,
                "error": "GuidUtente non restituito da Poste TEST"
            }

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta_test,
            GuidUtente=guid_utente_test
        )

        # =====================================================
        # 7. Valorizza TEST
        # =====================================================

        valorizza_result = service.Valorizza(
            Richieste=[richiesta]
        )

        costo_test = estrai_costo_valorizza(valorizza_result)

        xml_valorizza_sent = None
        xml_valorizza_received = None

        try:
            xml_valorizza_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_valorizza_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # =====================================================
        # 8. Salva SOLO dati TEST dentro poste_response
        #    Non cambia stato reale.
        # =====================================================

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        poste_response["raccomandata_test"] = {
            "step": "RACCOMANDATA_TEST_CALCOLA",
            "ambiente": "TEST",
            "service_url_test": POSTE_H2H_SERVICE_URL_TEST,
            "wsdl_test": POSTE_H2H_ROL_WSDL_TEST,
            "id_richiesta_test": id_richiesta_test,
            "guid_utente_test": guid_utente_test,
            "ricevuta_ritorno": has_rr,
            "costo_test": costo_test,
            "invio_result": str(invio_result),
            "valorizza_result": str(valorizza_result),
            "xml_invio_sent": xml_invio_sent,
            "xml_invio_received": xml_invio_received,
            "xml_valorizza_sent": xml_valorizza_sent,
            "xml_valorizza_received": xml_valorizza_received,
            "tested_at": now_iso
        }

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": now_iso
        }).eq("id", pratica_id).execute()

        return {
            "success": True,
            "step": "RACCOMANDATA_TEST_CALCOLA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "id_richiesta_test": id_richiesta_test,
            "guid_utente_test": guid_utente_test,
            "ricevuta_ritorno": has_rr,
            "costo_test": costo_test,
            "message": "Raccomandata TEST inviata tecnicamente e valorizzata. Nessuna finalizzazione, nessuna email, nessun cambio stato reale."
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_CALCOLA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/dashboard/pratiche/raccomandata-test-auto/{pratica_id}")
def dashboard_raccomandata_test_auto(pratica_id: str):
    """
    Pipeline automatica TEST Raccomandata H2H.

    Esegue in sequenza:
    1. Invio + Valorizza TEST
    2. PreConferma TEST
    3. Stato + Documento finale TEST

    NON usa produzione.
    NON invia email.
    NON cambia stato reale in INVIATO_POSTE.
    NON salva numero_raccomandata reale.
    """

    try:
        test_auto_enabled = os.getenv(
            "RACCOMANDATA_TEST_AUTO_ENABLED",
            "false"
        ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

        if not test_auto_enabled:
            return {
                "success": False,
                "blocked": True,
                "step": "RACCOMANDATA_TEST_AUTO_DISABLED",
                "pratica_id": pratica_id,
                "error": "Automazione TEST Raccomandata disattivata. Imposta RACCOMANDATA_TEST_AUTO_ENABLED=true."
            }

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_AUTO_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        tipo_servizio = str(
            pratica.get("tipo_servizio") or ""
        ).upper().strip()

        if tipo_servizio != "RACCOMANDATA":
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_AUTO_SERVIZIO_NON_VALIDO",
                "pratica_id": pratica_id,
                "tipo_servizio": tipo_servizio,
                "error": "Automazione TEST disponibile solo per Raccomandata"
            }

        stato_pratica = str(
            pratica.get("stato") or ""
        ).upper().strip()

        if stato_pratica not in ["RICEVUTO_PAGATO", "IN_LAVORAZIONE", "PREZZATA_DA_CONFERMARE"]:
            return {
                "success": False,
                "blocked": True,
                "step": "RACCOMANDATA_TEST_AUTO_STATO_NON_VALIDO",
                "pratica_id": pratica_id,
                "stato": stato_pratica,
                "error": "Automazione TEST consentita solo per pratiche pagate/lavorabili."
            }

        results = {
            "calcola": None,
            "finalizza": None,
            "stato_documento": None
        }

        # =====================================================
        # 1. Invio + Valorizza TEST
        # =====================================================

        calcola_result = dashboard_raccomandata_test_calcola(pratica_id)
        results["calcola"] = calcola_result

        if not isinstance(calcola_result, dict) or not calcola_result.get("success"):
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_AUTO_STOP_CALCOLA",
                "pratica_id": pratica_id,
                "results": results,
                "error": "Automazione TEST fermata su Invio/Valorizza TEST."
            }

        # =====================================================
        # 2. PreConferma TEST
        # =====================================================

        finalizza_result = dashboard_raccomandata_test_finalizza(pratica_id)
        results["finalizza"] = finalizza_result

        if not isinstance(finalizza_result, dict) or not finalizza_result.get("success"):
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_AUTO_STOP_PRECONFERMA",
                "pratica_id": pratica_id,
                "results": results,
                "error": "Automazione TEST fermata su PreConferma TEST."
            }
            
        numero_test = str(
            finalizza_result.get("numero_raccomandata_test") or ""
        ).strip()

        id_ordine_poste_test = str(
            finalizza_result.get("id_ordine_poste_test") or ""
        ).strip()

        if not numero_test or id_ordine_poste_test in ["", "None", "none", "null"]:
            return {
                "success": False,
                "pending": True,
                "step": "RACCOMANDATA_TEST_AUTO_PRECONFERMA_NON_PRONTA",
                "ambiente": "TEST",
                "pratica_id": pratica_id,
                "results": results,
                "message": "La pipeline TEST ha inviato la raccomandata, ma Poste non ha ancora restituito numero raccomandata/costo. Riprovare la finalizzazione TEST tra qualche minuto."
            }


        # =====================================================
        # 3. Stato + Documento finale TEST
        # =====================================================

        stato_result = dashboard_raccomandata_test_stato_documento(pratica_id)
        results["stato_documento"] = stato_result

        if not isinstance(stato_result, dict) or not stato_result.get("success"):
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_AUTO_STOP_STATO_DOCUMENTO",
                "pratica_id": pratica_id,
                "results": results,
                "error": "Automazione TEST fermata su Stato/Documento TEST."
            }

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # =====================================================
        # 4. Marchio interno di test completato
        #    Non cambio lo stato reale della pratica.
        # =====================================================

        pratica_refresh = supabase.table("pratiche") \
            .select("poste_response") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        poste_response = {}

        if pratica_refresh.data:
            poste_response = pratica_refresh.data.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        if not isinstance(raccomandata_test, dict):
            raccomandata_test = {}

        raccomandata_test["auto_pipeline"] = {
            "step": "RACCOMANDATA_TEST_AUTO_COMPLETATA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "completed_at": now_iso,
            "results_summary": {
                "calcola_success": bool(calcola_result.get("success")),
                "finalizza_success": bool(finalizza_result.get("success")),
                "stato_documento_success": bool(stato_result.get("success")),
                "numero_raccomandata_test": finalizza_result.get("numero_raccomandata_test"),
                "stato_test": (
                    stato_result.get("stato_result", {}) or {}
                ).get("StatoIdRichiesta"),
                "documento_is_pdf": stato_result.get("documento_is_pdf"),
                "documento_size_bytes": stato_result.get("documento_size_bytes")
            }
        }

        raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_AUTO_COMPLETATA"
        raccomandata_test["auto_test_completata"] = True
        raccomandata_test["auto_test_completed_at"] = now_iso

        poste_response["raccomandata_test"] = raccomandata_test

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": now_iso
        }).eq("id", pratica_id).execute()

        return {
            "success": True,
            "step": "RACCOMANDATA_TEST_AUTO_COMPLETATA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "numero_raccomandata_test": finalizza_result.get("numero_raccomandata_test"),
            "id_richiesta_test": finalizza_result.get("id_richiesta_test"),
            "guid_utente_test": finalizza_result.get("guid_utente_test"),
            "stato_test": (
                stato_result.get("stato_result", {}) or {}
            ).get("StatoIdRichiesta"),
            "documento_is_pdf": stato_result.get("documento_is_pdf"),
            "documento_size_bytes": stato_result.get("documento_size_bytes"),
            "results": results,
            "message": "Pipeline automatica Raccomandata TEST completata. Produzione non toccata."
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_AUTO",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "error": str(e)
        }


@app.get("/dashboard/pratiche/raccomandata-test-finalizza/{pratica_id}")
def dashboard_raccomandata_test_finalizza(pratica_id: str):
    """
    TEST Raccomandata H2H - PreConferma TEST.

    Usa SOLO ambiente Poste TEST.

    Fa:
    - legge id_richiesta_test e guid_utente_test salvati da raccomandata-test-calcola
    - chiama PreConferma TEST con autoConferma=True
    - salva risultato dentro poste_response["raccomandata_test"]

    NON cambia stato reale della pratica.
    NON salva numero_raccomandata reale.
    NON invia email.
    NON usa produzione.
    """

    history = HistoryPlugin()

    try:
        # =====================================================
        # 1. Carica pratica
        # =====================================================

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_FINALIZZA_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        tipo_servizio = str(
            pratica.get("tipo_servizio") or ""
        ).upper().strip()

        if tipo_servizio != "RACCOMANDATA":
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_FINALIZZA_SERVIZIO_NON_VALIDO",
                "pratica_id": pratica_id,
                "tipo_servizio": tipo_servizio,
                "error": "Questo test è disponibile solo per Raccomandata"
            }

        # =====================================================
        # 2. Legge dati TEST salvati dal primo step
        # =====================================================

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        if not isinstance(raccomandata_test, dict):
            raccomandata_test = {}

        id_richiesta_test = raccomandata_test.get("id_richiesta_test") or ""
        guid_utente_test = raccomandata_test.get("guid_utente_test") or ""

        if not id_richiesta_test or not guid_utente_test:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_DATI_PRECONFERMA_MANCANTI",
                "pratica_id": pratica_id,
                "error": "Prima devi eseguire raccomandata-test-calcola. Mancano id_richiesta_test o guid_utente_test.",
                "id_richiesta_test": id_richiesta_test,
                "guid_utente_test": guid_utente_test
            }

        # =====================================================
        # 3. Client Poste TEST
        # =====================================================

        client, service = poste_client_test(
            timeout=120,
            extra_plugins=[history]
        )

        RichiestaType = client.get_type("ns1:Richiesta")

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta_test,
            GuidUtente=guid_utente_test
        )

        # =====================================================
        # 4. PreConferma TEST
        # =====================================================

        pre_result = service.PreConferma(
            Richieste=[richiesta],
            autoConferma=True
        )

        pre_plain = make_json_safe(
            zeep_to_plain(pre_result)
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        # =====================================================
        # 5. Estrazione dati TEST in modo prudente
        # =====================================================

        numero_raccomandata_test = ""
        id_ricevuta_test = ""
        id_ordine_poste_test = ""
        costo_test_finale = None

        try:
            id_ordine_poste_test = str(pre_result.IdOrdine)
        except Exception:
            id_ordine_poste_test = ""

        try:
            destinatari_racc = (
                pre_result
                .DestinatariRaccomandata
                .ArrayOfDestinatarioRaccomandata
            )

            if isinstance(destinatari_racc, list) and destinatari_racc:
                primo = destinatari_racc[0]
            else:
                primo = destinatari_racc

            numero_raccomandata_test = str(
                getattr(primo, "NumeroRaccomandata", "") or ""
            )

            id_ricevuta_test = str(
                getattr(primo, "IdRicevuta", "") or ""
            )

        except Exception:
            pass

        try:
            costo_test_finale = float(
                pre_result.Valorizzazione.Totale.ImportoTotale
            )
        except Exception:
            costo_test_finale = None

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # =====================================================
        # 6. Salva SOLO dati TEST
        # =====================================================

        raccomandata_test["preconferma_test"] = {
            "step": "RACCOMANDATA_TEST_PRECONFERMA",
            "ambiente": "TEST",
            "id_richiesta_test": id_richiesta_test,
            "guid_utente_test": guid_utente_test,
            "id_ordine_poste_test": id_ordine_poste_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "id_ricevuta_test": id_ricevuta_test,
            "costo_test_finale": costo_test_finale,
            "preconferma_result": pre_plain,
            "preconferma_raw": str(pre_result),
            "xml_sent": xml_sent,
            "xml_received": xml_received,
            "tested_at": now_iso
        }

        raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_PRECONFERMA"
        raccomandata_test["numero_raccomandata_test"] = numero_raccomandata_test
        raccomandata_test["id_ricevuta_test"] = id_ricevuta_test
        raccomandata_test["id_ordine_poste_test"] = id_ordine_poste_test
        raccomandata_test["costo_test_finale"] = costo_test_finale

        poste_response["raccomandata_test"] = raccomandata_test

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": now_iso
        }).eq("id", pratica_id).execute()

        return {
            "success": True,
            "step": "RACCOMANDATA_TEST_PRECONFERMA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "id_richiesta_test": id_richiesta_test,
            "guid_utente_test": guid_utente_test,
            "id_ordine_poste_test": id_ordine_poste_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "id_ricevuta_test": id_ricevuta_test,
            "costo_test_finale": costo_test_finale,
            "message": "PreConferma TEST eseguita. Nessuna email, nessun cambio stato reale, nessuna produzione."
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_PRECONFERMA",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }
        
def _ecx_bytes_from_h2h_contenuto(value):
    """
    Converte il campo Contenuto restituito da Poste in bytes.
    Poste può restituire bytes, bytearray, lista di interi o stringa base64.
    """

    if value is None:
        return b""

    if isinstance(value, bytes):
        return value

    if isinstance(value, bytearray):
        return bytes(value)

    if isinstance(value, list):
        try:
            return bytes(value)
        except Exception:
            return b""

    if isinstance(value, str):
        clean_value = value.strip()

        if not clean_value:
            return b""

        # Se è già un PDF testuale
        if clean_value.startswith("%PDF"):
            return clean_value.encode("utf-8")

        # Prova base64
        try:
            return base64.b64decode(clean_value, validate=False)
        except Exception:
            return clean_value.encode("utf-8")

    return b""


def _ecx_find_first_value_by_keys(obj, keys):
    """
    Cerca in modo ricorsivo il primo valore utile in un dict/lista
    usando una lista di chiavi possibili.
    """

    if obj is None:
        return None

    if isinstance(obj, dict):
        for k in keys:
            if k in obj and obj.get(k) not in [None, ""]:
                return obj.get(k)

        for value in obj.values():
            found = _ecx_find_first_value_by_keys(value, keys)
            if found not in [None, ""]:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = _ecx_find_first_value_by_keys(item, keys)
            if found not in [None, ""]:
                return found

    return None


@app.get("/dashboard/pratiche/raccomandata-test-ricevuta/{pratica_id}")
def dashboard_raccomandata_test_ricevuta(pratica_id: str):
    """
    TEST Raccomandata H2H - RecuperaRicevutaAccettazione TEST.

    Usa SOLO ambiente Poste TEST.

    Fa:
    - legge id_richiesta_test / id_ricevuta_test salvati nello step PreConferma TEST
    - chiama RecuperaRicevutaAccettazione in TEST
    - verifica che il contenuto sia un PDF reale
    - salva la ricevuta PDF in base64 dentro poste_response["raccomandata_test"]

    NON cambia stato reale.
    NON salva ricevuta produzione.
    NON invia email.
    NON usa produzione.
    """

    history = HistoryPlugin()

    try:
        # =====================================================
        # 1. Carica pratica
        # =====================================================

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        tipo_servizio = str(
            pratica.get("tipo_servizio") or ""
        ).upper().strip()

        if tipo_servizio != "RACCOMANDATA":
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_SERVIZIO_NON_VALIDO",
                "pratica_id": pratica_id,
                "tipo_servizio": tipo_servizio,
                "error": "Questo test è disponibile solo per Raccomandata"
            }

        # =====================================================
        # 2. Legge dati TEST già salvati
        # =====================================================

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        if not isinstance(raccomandata_test, dict):
            raccomandata_test = {}

        preconferma_test = raccomandata_test.get("preconferma_test") or {}

        if not isinstance(preconferma_test, dict):
            preconferma_test = {}

        id_richiesta_test = (
            raccomandata_test.get("id_richiesta_test")
            or preconferma_test.get("id_richiesta_test")
            or ""
        )

        numero_raccomandata_test = (
            raccomandata_test.get("numero_raccomandata_test")
            or preconferma_test.get("numero_raccomandata_test")
            or ""
        )

        id_ricevuta_test = (
            raccomandata_test.get("id_ricevuta_test")
            or preconferma_test.get("id_ricevuta_test")
            or ""
        )

        if not id_richiesta_test:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_ID_RICHIESTA_MANCANTE",
                "pratica_id": pratica_id,
                "error": "Prima devi eseguire raccomandata-test-calcola e raccomandata-test-finalizza."
            }

        # =====================================================
        # 3. Client Poste TEST
        # =====================================================

        client, service = poste_client_test(
            timeout=120,
            extra_plugins=[history]
        )

        # =====================================================
        # 4. RecuperaRicevutaAccettazione TEST
        #    Proviamo più firme perché Poste può essere sensibile
        #    al nome esatto del parametro.
        # =====================================================

        ricevuta_result = None
        ultimo_errore = None
        tentativi = []

        possible_calls = []

        if id_ricevuta_test:
            possible_calls.append({
                "label": "IdRicevuta",
                "kwargs": {
                    "IdRicevuta": id_ricevuta_test
                }
            })

            possible_calls.append({
                "label": "IDRicevuta",
                "kwargs": {
                    "IDRicevuta": id_ricevuta_test
                }
            })

            possible_calls.append({
                "label": "IdRichiesta + IdRicevuta",
                "kwargs": {
                    "IdRichiesta": id_richiesta_test,
                    "IdRicevuta": id_ricevuta_test
                }
            })

            possible_calls.append({
                "label": "IDRichiesta + IDRicevuta",
                "kwargs": {
                    "IDRichiesta": id_richiesta_test,
                    "IDRicevuta": id_ricevuta_test
                }
            })

        possible_calls.append({
            "label": "IdRichiesta",
            "kwargs": {
                "IdRichiesta": id_richiesta_test
            }
        })

        possible_calls.append({
            "label": "IDRichiesta",
            "kwargs": {
                "IDRichiesta": id_richiesta_test
            }
        })

        for call in possible_calls:
            try:
                ricevuta_result = service.RecuperaRicevutaAccettazione(
                    **call["kwargs"]
                )

                tentativi.append({
                    "label": call["label"],
                    "success": True
                })

                break

            except Exception as ex:
                ultimo_errore = str(ex)

                tentativi.append({
                    "label": call["label"],
                    "success": False,
                    "error": str(ex)
                })

        if ricevuta_result is None:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_CHIAMATA_KO",
                "ambiente": "TEST",
                "pratica_id": pratica_id,
                "id_richiesta_test": id_richiesta_test,
                "id_ricevuta_test": id_ricevuta_test,
                "numero_raccomandata_test": numero_raccomandata_test,
                "tentativi": tentativi,
                "error": ultimo_errore or "RecuperaRicevutaAccettazione non riuscita"
            }

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        # =====================================================
        # 5. Estrae contenuto ricevuta
        # =====================================================

        ricevuta_plain = make_json_safe(
            zeep_to_plain(ricevuta_result)
        )

        contenuto = getattr(ricevuta_result, "Contenuto", None)

        if contenuto is None:
            contenuto = _ecx_find_first_value_by_keys(
                ricevuta_plain,
                [
                    "Contenuto",
                    "contenuto",
                    "Content",
                    "content",
                    "File",
                    "file",
                    "Documento",
                    "documento",
                    "Ricevuta",
                    "ricevuta"
                ]
            )

        estensione = str(
            getattr(ricevuta_result, "Estensione", "")
            or _ecx_find_first_value_by_keys(
                ricevuta_plain,
                ["Estensione", "estensione", "Extension", "extension"]
            )
            or "pdf"
        )

        data_accettazione = str(
            getattr(ricevuta_result, "DataAccettazione", "")
            or _ecx_find_first_value_by_keys(
                ricevuta_plain,
                ["DataAccettazione", "dataAccettazione", "Data", "data"]
            )
            or ""
        )

        ricevuta_bytes = _ecx_bytes_from_h2h_contenuto(contenuto)

        ricevuta_size = len(ricevuta_bytes)

        content_preview_text = ""

        try:
            content_preview_text = ricevuta_bytes[:160].decode(
                "utf-8",
                errors="replace"
            )
        except Exception:
            content_preview_text = ""

        content_base64_preview = ""

        try:
            content_base64_preview = base64.b64encode(
                ricevuta_bytes[:160]
            ).decode("utf-8")
        except Exception:
            content_base64_preview = ""

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # =====================================================
        # 6. Se manca contenuto
        # =====================================================

        if not ricevuta_bytes:
            if isinstance(ricevuta_plain, dict) and "Contenuto" in ricevuta_plain:
                ricevuta_plain["Contenuto"] = "<vuoto>"

            raccomandata_test["ricevuta_accettazione_test_debug"] = {
                "step": "RACCOMANDATA_TEST_RICEVUTA_CONTENUTO_MANCANTE",
                "ambiente": "TEST",
                "id_richiesta_test": id_richiesta_test,
                "id_ricevuta_test": id_ricevuta_test,
                "numero_raccomandata_test": numero_raccomandata_test,
                "estensione": estensione,
                "data_accettazione": data_accettazione,
                "tentativi": tentativi,
                "ricevuta_result": ricevuta_plain,
                "xml_sent": xml_sent,
                "xml_received": xml_received,
                "tested_at": now_iso
            }

            raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_RICEVUTA_CONTENUTO_MANCANTE"
            raccomandata_test["ricevuta_accettazione_test_presente"] = False

            poste_response["raccomandata_test"] = raccomandata_test

            supabase.table("pratiche").update({
                "poste_response": poste_response,
                "updated_at": now_iso
            }).eq("id", pratica_id).execute()

            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_CONTENUTO_MANCANTE",
                "ambiente": "TEST",
                "pratica_id": pratica_id,
                "id_richiesta_test": id_richiesta_test,
                "id_ricevuta_test": id_ricevuta_test,
                "numero_raccomandata_test": numero_raccomandata_test,
                "tentativi": tentativi,
                "message": "Poste TEST ha risposto, ma non ha restituito contenuto ricevuta."
            }

        # =====================================================
        # 7. Validazione PDF reale
        # =====================================================

        is_pdf = ricevuta_bytes.startswith(b"%PDF")

        if str(estensione).lower().strip() == "pdf" and not is_pdf:
            if isinstance(ricevuta_plain, dict) and "Contenuto" in ricevuta_plain:
                ricevuta_plain["Contenuto"] = f"<{ricevuta_size} bytes non PDF>"

            raccomandata_test["ricevuta_accettazione_test_debug"] = {
                "step": "RACCOMANDATA_TEST_RICEVUTA_NON_PDF",
                "ambiente": "TEST",
                "id_richiesta_test": id_richiesta_test,
                "id_ricevuta_test": id_ricevuta_test,
                "numero_raccomandata_test": numero_raccomandata_test,
                "estensione": estensione,
                "data_accettazione": data_accettazione,
                "size_bytes": ricevuta_size,
                "content_preview_text": content_preview_text,
                "content_base64_preview": content_base64_preview,
                "tentativi": tentativi,
                "ricevuta_result": ricevuta_plain,
                "xml_sent": xml_sent,
                "xml_received": xml_received,
                "tested_at": now_iso
            }

            raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_RICEVUTA_NON_PDF"
            raccomandata_test["ricevuta_accettazione_test_presente"] = False
            raccomandata_test["ricevuta_accettazione_test_size_bytes"] = ricevuta_size

            poste_response["raccomandata_test"] = raccomandata_test

            supabase.table("pratiche").update({
                "poste_response": poste_response,
                "updated_at": now_iso
            }).eq("id", pratica_id).execute()

            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_NON_PDF",
                "ambiente": "TEST",
                "pratica_id": pratica_id,
                "id_richiesta_test": id_richiesta_test,
                "id_ricevuta_test": id_ricevuta_test,
                "numero_raccomandata_test": numero_raccomandata_test,
                "estensione": estensione,
                "data_accettazione": data_accettazione,
                "size_bytes": ricevuta_size,
                "content_preview_text": content_preview_text,
                "content_base64_preview": content_base64_preview,
                "tentativi": tentativi,
                "message": "Poste TEST ha risposto, ma il contenuto ricevuto non è un PDF valido."
            }

        # =====================================================
        # 8. Salva ricevuta TEST valida
        # =====================================================

        ricevuta_base64 = base64.b64encode(ricevuta_bytes).decode("utf-8")

        if isinstance(ricevuta_plain, dict) and "Contenuto" in ricevuta_plain:
            ricevuta_plain["Contenuto"] = f"<{ricevuta_size} bytes>"

        raccomandata_test["ricevuta_accettazione_test"] = {
            "step": "RACCOMANDATA_TEST_RICEVUTA_ACCETTAZIONE",
            "ambiente": "TEST",
            "id_richiesta_test": id_richiesta_test,
            "id_ricevuta_test": id_ricevuta_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "estensione": estensione,
            "data_accettazione": data_accettazione,
            "size_bytes": ricevuta_size,
            "contenuto_base64": ricevuta_base64,
            "tentativi": tentativi,
            "ricevuta_result": ricevuta_plain,
            "xml_sent": xml_sent,
            "xml_received": xml_received,
            "tested_at": now_iso
        }

        raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_RICEVUTA_ACCETTAZIONE"
        raccomandata_test["ricevuta_accettazione_test_presente"] = True
        raccomandata_test["ricevuta_accettazione_test_size_bytes"] = ricevuta_size
        raccomandata_test["ricevuta_accettazione_test_estensione"] = estensione
        raccomandata_test["ricevuta_accettazione_test_data"] = data_accettazione

        poste_response["raccomandata_test"] = raccomandata_test

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": now_iso
        }).eq("id", pratica_id).execute()

        return {
            "success": True,
            "step": "RACCOMANDATA_TEST_RICEVUTA_ACCETTAZIONE",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "id_richiesta_test": id_richiesta_test,
            "id_ricevuta_test": id_ricevuta_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "estensione": estensione,
            "data_accettazione": data_accettazione,
            "size_bytes": ricevuta_size,
            "open_pdf_url": f"/dashboard/pratiche/raccomandata-test-ricevuta-pdf/{pratica_id}",
            "message": "Ricevuta Accettazione TEST recuperata e salvata. Nessuna email, nessun cambio stato reale, nessuna produzione."
        }

    except Exception as e:
        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_RICEVUTA_ACCETTAZIONE",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }


@app.get("/dashboard/pratiche/raccomandata-test-ricevuta-pdf/{pratica_id}")
def dashboard_raccomandata_test_ricevuta_pdf(pratica_id: str):
    """
    Apre/scarica la ricevuta accettazione TEST salvata.
    NON usa Poste.
    NON usa produzione.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_PDF_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        ricevuta_test = raccomandata_test.get("ricevuta_accettazione_test") or {}

        contenuto_base64 = ricevuta_test.get("contenuto_base64") or ""

        if not contenuto_base64:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_PDF_NON_PRESENTE",
                "pratica_id": pratica_id,
                "error": "Ricevuta TEST non presente. Prima esegui raccomandata-test-ricevuta."
            }

        pdf_bytes = base64.b64decode(contenuto_base64)

        if not pdf_bytes.startswith(b"%PDF"):
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_RICEVUTA_PDF_NON_VALIDO",
                "pratica_id": pratica_id,
                "size_bytes": len(pdf_bytes),
                "error": "Il contenuto salvato non è un PDF valido."
            }

        filename = f"ricevuta-raccomandata-test-{pratica_id}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            }
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_RICEVUTA_PDF",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/raccomandata-test-stato-documento/{pratica_id}")
def dashboard_raccomandata_test_stato_documento(pratica_id: str):
    """
    TEST Raccomandata H2H - Stato + Documento Finale TEST.

    Usa SOLO ambiente Poste TEST.

    Fa:
    - RecuperaStatoIdRichiesta TEST
    - RecuperaDocumentoFinale TEST se presente numero_raccomandata_test

    NON cambia stato reale.
    NON invia email.
    NON usa produzione.
    """

    history = HistoryPlugin()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_STATO_DOC_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        if not isinstance(raccomandata_test, dict):
            raccomandata_test = {}

        preconferma_test = raccomandata_test.get("preconferma_test") or {}

        if not isinstance(preconferma_test, dict):
            preconferma_test = {}

        id_richiesta_test = (
            raccomandata_test.get("id_richiesta_test")
            or preconferma_test.get("id_richiesta_test")
            or ""
        )

        numero_raccomandata_test = (
            raccomandata_test.get("numero_raccomandata_test")
            or preconferma_test.get("numero_raccomandata_test")
            or ""
        )

        if not id_richiesta_test:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_STATO_DOC_ID_RICHIESTA_MANCANTE",
                "pratica_id": pratica_id,
                "error": "Manca id_richiesta_test. Esegui prima calcola e finalizza TEST."
            }

        client, service = poste_client_test(
            timeout=120,
            extra_plugins=[history]
        )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # =====================================================
        # 1. RecuperaStatoIdRichiesta TEST
        # =====================================================

        stato_result = None
        stato_error = None
        stato_plain = None

        try:
            stato_result = service.RecuperaStatoIdRichiesta(
                IdRichiesta=id_richiesta_test
            )

            stato_plain = make_json_safe(
                zeep_to_plain(stato_result)
            )

        except Exception as e:
            stato_error = str(e)

        xml_stato_sent = None
        xml_stato_received = None

        try:
            xml_stato_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_stato_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        # =====================================================
        # 2. RecuperaDocumentoFinale TEST
        # =====================================================

        documento_result = None
        documento_plain = None
        documento_error = None
        documento_bytes = b""
        documento_size = 0
        documento_is_pdf = False
        documento_estensione = "pdf"
        documento_md5 = ""
        documento_base64 = ""
        documento_preview_text = ""
        documento_tentativi = []

        if numero_raccomandata_test:
            possible_doc_calls = [
                {
                    "label": "NumeroRaccomandata",
                    "kwargs": {
                        "NumeroRaccomandata": numero_raccomandata_test
                    }
                },
                {
                    "label": "NumeroLettera",
                    "kwargs": {
                        "NumeroLettera": numero_raccomandata_test
                    }
                },
                {
                    "label": "Numero",
                    "kwargs": {
                        "Numero": numero_raccomandata_test
                    }
                },
                {
                    "label": "numeroRaccomandata",
                    "kwargs": {
                        "numeroRaccomandata": numero_raccomandata_test
                    }
                }
            ]

            for call in possible_doc_calls:
                try:
                    documento_result = service.RecuperaDocumentoFinale(
                        **call["kwargs"]
                    )

                    documento_tentativi.append({
                        "label": call["label"],
                        "success": True
                    })

                    break

                except Exception as ex:
                    documento_error = str(ex)

                    documento_tentativi.append({
                        "label": call["label"],
                        "success": False,
                        "error": str(ex)
                    })

            if documento_result is not None:
                documento_plain = make_json_safe(
                    zeep_to_plain(documento_result)
                )

                documento_obj = getattr(documento_result, "Documento", None)

                contenuto = None

                if documento_obj is not None:
                    contenuto = getattr(documento_obj, "Contenuto", None)
                    documento_estensione = str(getattr(documento_obj, "Estensione", "") or "pdf")
                    documento_md5 = str(getattr(documento_obj, "MD5", "") or "")

                if contenuto is None:
                    contenuto = _ecx_find_first_value_by_keys(
                        documento_plain,
                        [
                            "Contenuto",
                            "contenuto",
                            "Content",
                            "content",
                            "Documento",
                            "documento"
                        ]
                    )

                documento_bytes = _ecx_bytes_from_h2h_contenuto(contenuto)
                documento_size = len(documento_bytes)
                documento_is_pdf = documento_bytes.startswith(b"%PDF")

                try:
                    documento_preview_text = documento_bytes[:160].decode(
                        "utf-8",
                        errors="replace"
                    )
                except Exception:
                    documento_preview_text = ""

                if documento_is_pdf:
                    documento_base64 = base64.b64encode(documento_bytes).decode("utf-8")

                    if isinstance(documento_plain, dict):
                        # Evita di salvare il file intero duplicato dentro il plain.
                        doc_plain_contenuto = _ecx_find_first_value_by_keys(
                            documento_plain,
                            ["Contenuto", "contenuto"]
                        )

                        if doc_plain_contenuto:
                            documento_plain["DocumentoFinaleNote"] = f"Documento PDF presente: {documento_size} bytes"

        # =====================================================
        # 3. Salvataggio solo dati TEST
        # =====================================================

        raccomandata_test["stato_documento_test"] = {
            "step": "RACCOMANDATA_TEST_STATO_DOCUMENTO",
            "ambiente": "TEST",
            "id_richiesta_test": id_richiesta_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "stato_result": stato_plain,
            "stato_error": stato_error,
            "xml_stato_sent": xml_stato_sent,
            "xml_stato_received": xml_stato_received,
            "documento_tentativi": documento_tentativi,
            "documento_result": documento_plain,
            "documento_error": documento_error,
            "documento_size_bytes": documento_size,
            "documento_is_pdf": documento_is_pdf,
            "documento_estensione": documento_estensione,
            "documento_md5": documento_md5,
            "documento_preview_text": documento_preview_text,
            "tested_at": now_iso
        }

        if documento_is_pdf:
            raccomandata_test["documento_finale_test"] = {
                "step": "RACCOMANDATA_TEST_DOCUMENTO_FINALE",
                "ambiente": "TEST",
                "numero_raccomandata_test": numero_raccomandata_test,
                "estensione": documento_estensione,
                "md5": documento_md5,
                "size_bytes": documento_size,
                "contenuto_base64": documento_base64,
                "tested_at": now_iso
            }

            raccomandata_test["documento_finale_test_presente"] = True
            raccomandata_test["documento_finale_test_size_bytes"] = documento_size
        else:
            raccomandata_test["documento_finale_test_presente"] = False
            raccomandata_test["documento_finale_test_size_bytes"] = documento_size

        raccomandata_test["ultimo_step"] = "RACCOMANDATA_TEST_STATO_DOCUMENTO"

        poste_response["raccomandata_test"] = raccomandata_test

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": now_iso
        }).eq("id", pratica_id).execute()

        return {
            "success": True,
            "step": "RACCOMANDATA_TEST_STATO_DOCUMENTO",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "order_name": pratica.get("shopify_order_name") or pratica.get("order_name"),
            "id_richiesta_test": id_richiesta_test,
            "numero_raccomandata_test": numero_raccomandata_test,
            "stato_result": stato_plain,
            "stato_error": stato_error,
            "documento_tentativi": documento_tentativi,
            "documento_error": documento_error,
            "documento_size_bytes": documento_size,
            "documento_is_pdf": documento_is_pdf,
            "documento_preview_text": documento_preview_text,
            "open_documento_finale_test_url": (
                f"/dashboard/pratiche/raccomandata-test-documento-finale-pdf/{pratica_id}"
                if documento_is_pdf else None
            ),
            "message": "Stato e Documento Finale TEST verificati. Nessuna email, nessun cambio stato reale, nessuna produzione."
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_STATO_DOCUMENTO",
            "ambiente": "TEST",
            "pratica_id": pratica_id,
            "error": str(e)
        }


@app.get("/dashboard/pratiche/raccomandata-test-documento-finale-pdf/{pratica_id}")
def dashboard_raccomandata_test_documento_finale_pdf(pratica_id: str):
    """
    Apre/scarica il Documento Finale TEST salvato.
    NON usa Poste.
    NON usa produzione.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_DOCUMENTO_FINALE_PDF_PRATICA_NON_TROVATA",
                "pratica_id": pratica_id,
                "error": "Pratica non trovata"
            }

        pratica = pratica_res.data

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        raccomandata_test = poste_response.get("raccomandata_test") or {}

        documento_test = raccomandata_test.get("documento_finale_test") or {}

        contenuto_base64 = documento_test.get("contenuto_base64") or ""

        if not contenuto_base64:
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_DOCUMENTO_FINALE_PDF_NON_PRESENTE",
                "pratica_id": pratica_id,
                "error": "Documento finale TEST non presente. Prima esegui raccomandata-test-stato-documento."
            }

        pdf_bytes = base64.b64decode(contenuto_base64)

        if not pdf_bytes.startswith(b"%PDF"):
            return {
                "success": False,
                "step": "RACCOMANDATA_TEST_DOCUMENTO_FINALE_PDF_NON_VALIDO",
                "pratica_id": pratica_id,
                "size_bytes": len(pdf_bytes),
                "error": "Il contenuto salvato non è un PDF valido."
            }

        filename = f"documento-finale-raccomandata-test-{pratica_id}.pdf"

        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'inline; filename="{filename}"'
            }
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RACCOMANDATA_TEST_DOCUMENTO_FINALE_PDF",
            "pratica_id": pratica_id,
            "error": str(e)
        }

        
@app.get("/dashboard/pratiche", response_class=HTMLResponse)
def dashboard_pratiche(stato: str = None):
    filtro_stato = (stato or "").strip().upper() or None

    result = (
        supabase
        .table("pratiche")
        .select(
            "id,order_id,order_name,shopify_order_name,tipo_servizio,"
            "cliente_email,stato,numero_raccomandata,pdf_url,poste_response,"
            "id_richiesta,ricevuta_ritorno,created_at,updated_at,"
            "pdf_ricevuta_cliente_url,"
            "email_sent,email_sent_at,email_error,email_to,email_subject,email_resend_id"
        )
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )

    tutte_pratiche = result.data or []

    if not filtro_stato or filtro_stato == "TUTTI":
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") not in ["BOZZA_CHECKOUT", "NON_PAGATO"]
        ]

    elif filtro_stato in ["ERRORI", "ERRORE_POSTE"]:
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == "ERRORE_POSTE"
        ]

    elif filtro_stato in ["INVIATI", "INVIATO_POSTE"]:
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == "INVIATO_POSTE"
        ]

    elif filtro_stato == "MANUALI":
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") in ["LAVORAZIONE_MANUALE", "RICEVUTO_MANUALE"]
        ]

    elif filtro_stato in ["COMPLETATI", "COMPLETATO"]:
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == "COMPLETATO"
        ]

    elif filtro_stato == "BOZZA_CHECKOUT":
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == "BOZZA_CHECKOUT"
        ]

    elif filtro_stato == "NON_PAGATO":
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == "NON_PAGATO"
        ]

    else:
        pratiche = [
            p for p in tutte_pratiche
            if p.get("stato") == filtro_stato
        ]

    h2h_result = (
        supabase
        .table("poste_h2h_orders")
        .select("id,pdf_url,shopify_order_name,costo,pdf_ricevuta_url")
        .order("created_at", desc=True)
        .limit(100)
        .execute()
    )

    h2h_rows = h2h_result.data or []

    h2h_by_pdf = {
        h.get("pdf_url"): h.get("shopify_order_name")
        for h in h2h_rows
        if h.get("pdf_url") and h.get("shopify_order_name")
    }

    h2h_id_by_pdf = {
        h.get("pdf_url"): h.get("id")
        for h in h2h_rows
        if h.get("pdf_url") and h.get("id")
    }

    h2h_costo_by_pdf = {
        h.get("pdf_url"): h.get("costo")
        for h in h2h_rows
        if h.get("pdf_url")
    }

    h2h_ricevuta_by_pdf = {
        h.get("pdf_url"): h.get("pdf_ricevuta_url")
        for h in h2h_rows
        if h.get("pdf_url")
    }

    counter_visibili = [
        p for p in tutte_pratiche
        if p.get("stato") not in ["BOZZA_CHECKOUT", "NON_PAGATO"]
    ]

    tot_tutti = len(counter_visibili)

    rows = ""

    for p in pratiche:
        pratica_id = p.get("id")
        stato_pratica = p.get("stato", "-")
        has_rr = bool_from_any(p.get("ricevuta_ritorno"))

        tipo_servizio_upper = str(p.get("tipo_servizio") or "").upper().strip()

        # 1) PDF documento = contenuto scritto/caricato dal cliente
        if tipo_servizio_upper == "TELEGRAMMA":
            pdf_documento_href = f"/dashboard/pratiche/pdf-telegramma/{pratica_id}"
            pdf_documento_label = "📄 PDF documento Telegramma"
        elif tipo_servizio_upper == "RACCOMANDATA":
            pdf_documento_href = p.get("pdf_url") or f"/dashboard/pratiche/pdf/{p.get('id_richiesta') or pratica_id}"
            pdf_documento_label = "📄 PDF documento Raccomandata"
        else:
            pdf_documento_href = p.get("pdf_url") or f"/dashboard/pratiche/pdf/{p.get('id_richiesta') or pratica_id}"
            pdf_documento_label = "📄 PDF documento"

        # 2) Ricevuta cliente = documento Eccomi per il cliente
        ricevuta_cliente_html = ""

        ricevuta_cliente_url_pratica = (
            p.get("pdf_ricevuta_cliente_url")
            or ""
        )

        email_gia_inviata = bool_from_any(p.get("email_sent"))

        if stato_pratica == "INVIATO_POSTE":
            if ricevuta_cliente_url_pratica:
                ricevuta_cliente_html = f"""
                    <a class="btn-action"
                       href="{ricevuta_cliente_url_pratica}"
                       target="_blank">
                        ✅ Ricevuta cliente
                    </a>
                """

            elif email_gia_inviata:
                ricevuta_cliente_html = """
                    <span class="btn-action btn-disabled">
                        ✅ Ricevuta cliente già inviata - verifica manuale
                    </span>
                """

            elif tipo_servizio_upper == "TELEGRAMMA":
                ricevuta_cliente_html = f"""
                    <a class="btn-action"
                       href="/dashboard/pratiche/apri-pdf/{pratica_id}"
                       target="_blank">
                        ✅ Ricevuta cliente
                    </a>
                """

            elif tipo_servizio_upper == "RACCOMANDATA":
                ricevuta_cliente_html = f"""
                    <a class="btn-action"
                       href="/dashboard/pratiche/genera-ricevuta-cliente/{pratica_id}?apri=1"
                       target="_blank"
                       onclick="return confirm('Generare la ricevuta cliente Eccomi? Non verrà chiamata Poste e non verrà inviata email.')">
                        ✅ Ricevuta cliente
                    </a>
                """

            else:
                ricevuta_cliente_html = """
                    <span class="btn-action btn-disabled">
                        ✅ Ricevuta cliente non pronta
                    </span>
                """
        else:
            ricevuta_cliente_html = """
                <span class="btn-action btn-disabled">
                    ✅ Ricevuta cliente non pronta
                </span>
            """

        servizio_display = p.get("tipo_servizio") or "-"

        if has_rr:
            servizio_display = f"{servizio_display} + RR"

        pdf_url_pratica = p.get("pdf_url")
        h2h_order_id = h2h_id_by_pdf.get(pdf_url_pratica)
        costo_valorizzato = h2h_costo_by_pdf.get(pdf_url_pratica)
        ricevuta_poste_url = h2h_ricevuta_by_pdf.get(pdf_url_pratica)

        ricevuta_poste_label = "Apri ricevuta Poste"
        ricevuta_poste_telegramma_url = None
        ricevuta_cliente_url_pratica = p.get("pdf_ricevuta_cliente_url")

        try:
            pr_telegramma = p.get("poste_response") or {}

            if isinstance(pr_telegramma, str):
                try:
                    pr_telegramma = json.loads(pr_telegramma)
                except Exception:
                    pr_telegramma = {}

            ricevuta_poste_telegramma_url = (
                pr_telegramma.get("pdf_ricevuta_poste_url")
                or pr_telegramma.get("pdf_poste_originale_url")
                or pr_telegramma.get("ricevuta_poste_url")
                or pr_telegramma.get("pdf_ricevuta_url")
                or pr_telegramma.get("ricevuta_url")
            )

        except Exception:
            ricevuta_poste_telegramma_url = None
            
        if costo_valorizzato is not None:
            try:
                costo_float = float(str(costo_valorizzato).replace(",", "."))
                costo_display = f"€ {costo_float:.2f}".replace(".", ",")
            except Exception:
                costo_display = f"€ {costo_valorizzato}"
        else:
            costo_display = None

        if stato_pratica == "INVIATO_POSTE":
            if p.get("tipo_servizio") == "TELEGRAMMA":
                if ricevuta_poste_telegramma_url:
                    ricevuta_poste_html = f"""
                        <a class="btn-action"
                           href="{ricevuta_poste_telegramma_url}"
                           target="_blank">
                            🏛️ Ricevuta Poste interna
                        </a>
                    """
                else:
                    ricevuta_poste_html = """
                        <span class="btn-action btn-disabled">
                            🏛️ Ricevuta Poste interna non ancora disponibile
                        </span>
                    """

            elif ricevuta_poste_url:
                ricevuta_poste_html = f"""
                    <span class="receipt-pill receipt-ok">
                        🏛️ Ricevuta Poste interna salvata
                    </span>

                    <a class="btn-action"
                       href="{ricevuta_poste_url}"
                       target="_blank">
                        {ricevuta_poste_label}
                    </a>
                """
            else:
                ricevuta_poste_html = f"""
                    <span class="receipt-pill receipt-wait">
                        🏛️ Ricevuta Poste interna da recuperare
                    </span>

                    <a class="btn-action"
                       href="/dashboard/pratiche/ricevuta-poste/{pratica_id}"
                       target="_blank">
                        Recupera ricevuta Poste
                    </a>
                """
        elif stato_pratica == "ERRORE_POSTE":
            ricevuta_poste_html = """
                <span class="receipt-pill receipt-error">
                    ⚠️ Ricevuta non disponibile
                </span>
            """
        else:
            ricevuta_poste_html = """
                <span class="receipt-pill receipt-na">
                    Ricevuta non ancora prevista
                </span>
            """

        email_cliente_html = ""
        
        email_sent = bool_from_any(p.get("email_sent"))
        email_error = p.get("email_error")
        email_to_val = p.get("email_to") or p.get("cliente_email") or ""

        if stato_pratica == "INVIATO_POSTE":
            if email_sent:
                email_cliente_html = """
                    <span class="receipt-pill receipt-ok">
                        ✅ Email inviata
                    </span>
                """
            elif email_error:
                email_cliente_html = f"""
                    <span class="receipt-pill receipt-error" title="{str(email_error).replace('"', '')}">
                        ⚠️ Email errore
                    </span>

                    <a class="btn-action"
                       href="/dashboard/pratiche/invia-email-cliente/{pratica_id}"
                       onclick="return confirm('Riprovo a inviare la mail al cliente? Non verrà chiamata Poste.')">
                        📧 Riprova email
                    </a>
                """
            elif email_to_val:
                email_cliente_html = f"""
                    <span class="receipt-pill receipt-wait">
                        📧 Email da inviare
                    </span>

                    <a class="btn-action"
                       href="/dashboard/pratiche/invia-email-cliente/{pratica_id}"
                       onclick="return confirm('Inviare email al cliente per questa raccomandata? Non verrà chiamata Poste.')">
                        📧 Email cliente
                    </a>
                """
            else:
                email_cliente_html = """
                    <span class="receipt-pill receipt-error">
                        ⚠️ Email mancante
                    </span>
                """
        else:
            email_cliente_html = """
                <span class="btn-action btn-disabled">
                    📧 Email non pronta
                </span>
            """

        monitor_btn = ""

        stato_monitorabile = str(stato_pratica or "").upper().strip()

        if stato_monitorabile in ["INVIATO_POSTE", "COMPLETATO"]:
            monitor_target = (
                p.get("id_richiesta")
                or pratica_id
                or ""
            )

            if monitor_target:
                monitor_btn = f"""
                    <a class="btn-action btn-monitor"
                       href="/dashboard/pratiche/monitora-view/{monitor_target}">
                        🔎 Monitora
                    </a>
                """
                
        order_display = (
            p.get("shopify_order_name")
            or h2h_by_pdf.get(pdf_url_pratica)
            or p.get("order_name")
            or "-"
        )
        
        created_raw = p.get("created_at") or ""
        data_breve = created_raw.replace("T", " ")[:16]

        cliente_email = p.get("cliente_email") or "-"
        email_breve = (
            cliente_email
            if len(cliente_email) <= 14
            else cliente_email[:11] + "..."
        )

        numero_raccomandata = p.get("numero_raccomandata")

        colore = "#999"

        if stato_pratica == "RICEVUTO":
            colore = "#3498db"
        elif stato_pratica == "RICEVUTO_PAGATO":
            colore = "#0ea5e9"
        elif stato_pratica == "INVIATO_POSTE":
            colore = "#27ae60"
        elif stato_pratica == "ERRORE_POSTE":
            colore = "#e74c3c"
        elif stato_pratica == "LAVORAZIONE_MANUALE":
            colore = "#f39c12"
        elif stato_pratica == "COMPLETATO":
            colore = "#8e44ad"
        elif stato_pratica == "PREZZATA_DA_CONFERMARE":
            colore = "#6366f1"
        elif stato_pratica == "RICEVUTO_MANUALE":
            colore = "#f97316"
        elif stato_pratica == "BOZZA_CHECKOUT":
            colore = "#9ca3af"
        elif stato_pratica == "NON_PAGATO":
            colore = "#6b7280"

        tracking_html = "-"

        if numero_raccomandata:
            if p.get("tipo_servizio") == "TELEGRAMMA":
                tracking_html = f"""
                <span style="
                      background:#eef3ff;
                      padding:8px 12px;
                      border-radius:10px;
                      display:inline-block;
                      font-size:14px;
                      font-weight:bold;
                      color:#2563eb;
                   ">
                   📨 N. accettazione<br>
                   {numero_raccomandata}
                </span>
                """
            else:
                tracking_html = f"""
                <a href="https://www.poste.it/cerca/index.html#/risultati-spedizioni/{numero_raccomandata}"
                   target="_blank"
                   style="
                      background:#eef3ff;
                      padding:8px 12px;
                      border-radius:10px;
                      display:inline-block;
                      font-size:14px;
                      font-weight:bold;
                   ">
                   📦 {numero_raccomandata}
                </a>
                """

        row_bg = "#ffffff"

        if stato_pratica == "ERRORE_POSTE":
            row_bg = "#fff5f5"
        elif stato_pratica == "INVIATO_POSTE":
            row_bg = "#f0fff4"
        elif stato_pratica == "COMPLETATO":
            row_bg = "#faf5ff"
        elif stato_pratica == "RICEVUTO_PAGATO":
            row_bg = "#eff6ff"
        elif stato_pratica == "PREZZATA_DA_CONFERMARE":
            row_bg = "#eef2ff"
        elif stato_pratica in ["BOZZA_CHECKOUT", "NON_PAGATO"]:
            row_bg = "#f9fafb"

        prezzo_poste_html = ""

        try:
            poste_response_prezzo = p.get("poste_response") or {}

            if isinstance(poste_response_prezzo, str):
                try:
                    poste_response_prezzo = json.loads(poste_response_prezzo)
                except Exception:
                    poste_response_prezzo = {}

            prezzo_poste = (
                poste_response_prezzo.get("prezzo_totale")
                or poste_response_prezzo.get("prezzoTotale")
            )

            if prezzo_poste is not None:
                prezzo_poste = float(prezzo_poste)
                prezzo_formattato = f"{prezzo_poste:.2f}".replace(".", ",")

                prezzo_poste_html = f"""
                    <span class="btn-action"
                          style="background:#fff7e6;color:#c2410c;font-weight:900;">
                        💶 Prezzo Poste: € {prezzo_formattato}
                    </span>
                """
        except Exception:
            prezzo_poste_html = ""

        if p.get("tipo_servizio") == "TELEGRAMMA" and stato_pratica == "RICEVUTO_MANUALE":
            invia_poste_html = f"""
                <a class="btn-action"
                   href="/dashboard/pratiche/telegramma-preventivo/{pratica_id}?redirect=1"
                   onclick="return confirm('Vuoi richiedere il preventivo reale Poste per questo Telegramma? Non verrà inviato nulla.')">
                    💶 Preventivo Poste
                </a>
            """

        elif stato_pratica == "BOZZA_CHECKOUT":
            invia_poste_html = f"""
                <a class="btn-action"
                   href="/dashboard/pratiche/marca-pagata/{pratica_id}"
                   onclick="return confirm('Confermi che questa pratica è stata pagata su Shopify? Questa azione NON invia a Poste.')">
                    💳 Segna pagata
                </a>

                <span class="btn-action btn-disabled">
                    🔒 Poste bloccato
                </span>
            """
        elif stato_pratica in ["RICEVUTO_PAGATO", "IN_LAVORAZIONE"] and h2h_order_id:
            direct_button_html = ""

            if POSTE_INVIO_MODE == "auto" and POSTE_INVIO_DIRETTO_ENABLED:
                direct_button_html = (
                    '<a class="btn-action btn-send" '
                    f'href="/dashboard/pratiche/invia-diretto-poste/{pratica_id}" '
                    'onclick="return confirm(\'ATTENZIONE: questa azione calcola e FINALIZZA realmente la raccomandata a Poste. Può generare costo H2H. Confermi?\')">'
                    '🚀 Invia diretto Poste'
                    '</a>'
                )
            else:
                direct_button_html = (
                    '<span class="btn-action btn-disabled" '
                    'title="Invio diretto disattivato: usare prima Calcola prezzo Poste, poi Finalizza Poste.">'
                    '🚀 Diretto disattivato'
                    '</span>'
                )

            invia_poste_html = (
                '<a class="btn-action" '
                f'href="/dashboard/pratiche/invia-poste/{pratica_id}" '
                'onclick="return confirm(\'Confermi il calcolo prezzo Poste? Non verrà finalizzata la raccomandata.\')">'
                '💶 Calcola prezzo Poste'
                '</a>'
            + direct_button_html
        )
        
                
        elif p.get("tipo_servizio") == "TELEGRAMMA" and stato_pratica == "PREZZATA_DA_CONFERMARE":
            invia_poste_html = f"""
                {prezzo_poste_html}

                <a class="btn-action btn-send"
                   href="/dashboard/pratiche/telegramma-loading/{pratica_id}"
                   target="_blank"
                   onclick="return confirm('ATTENZIONE: confermi invio automatico Telegramma H2H a Poste? Questa azione può inviare davvero il telegramma e generare costo H2H.');">
                    🚀 Invia Telegramma H2H
                </a>

                <a class="btn-action"
                   href="/dashboard/pratiche/telegramma-manuale/{pratica_id}"
                   onclick="return confirm('Vuoi segnare questo telegramma come inviato manualmente?')">
                    📝 Segna inviato manuale
                </a>
            """


        elif stato_pratica == "PREZZATA_DA_CONFERMARE" and h2h_order_id:
            if costo_display:
                invia_poste_html = (
                    '<span class="btn-price">'
                    f'💶 Prezzo Poste: {costo_display}'
                    '</span>'
                    '<a class="btn-action btn-send" '
                    f'href="/poste/h2h/finalizza/{h2h_order_id}" '
                    'target="_blank" '
                    'onclick="return confirm(\'Confermi finalizzazione Poste al prezzo indicato? Questa operazione può generare costo H2H.\')">'
                    '✅ Finalizza Poste'
                    '</a>'
                )
            else:
                invia_poste_html = (
                    '<a class="btn-action" '
                    f'href="/poste/h2h/ricalcola-prezzo/{h2h_order_id}" '
                    'target="_blank" '
                    'onclick="return confirm(\'Vuoi ricalcolare il prezzo Poste senza finalizzare la raccomandata?\')">'
                    '🔁 Ricalcola prezzo'
                    '</a>'
                    '<span class="btn-action btn-disabled">'
                    '✅ Finalizza bloccato'
                    '</span>'
                )

        elif stato_pratica in ["RICEVUTO_PAGATO", "IN_LAVORAZIONE", "PREZZATA_DA_CONFERMARE"] and not h2h_order_id:
            invia_poste_html = (
                '<span class="btn-action btn-disabled">'
                '⚠️ H2H non pronto'
                '</span>'
            )

        else:
            invia_poste_html = (
                '<span class="btn-action btn-disabled">'
                '🔒 Invia Poste bloccato'
                '</span>'
            )

        rows += f"""
        <tr class="main-row searchable-row" style="background:{row_bg};">
            <td>{clean_order_display(order_display)}</td>

            <td>
                {servizio_display}
                {"<span class='badge-rr'>📬 RR</span>" if has_rr else ""}
            </td>

            <td class="email-cell" title="{cliente_email}">
                {email_breve}
            </td>

            <td>
                <span class="badge" style="background:{colore};">
                    {stato_pratica}
                </span>
            </td>

            <td>{tracking_html}</td>

            <td>{data_breve}</td>
        </tr>

        <tr class="action-row searchable-row" style="background:{row_bg};">
            <td colspan="6">
                <div class="action-bar">
                    <a class="btn-action"
                       href="/dashboard/pratiche/{pratica_id}"
                       target="_blank">
                        Dettaglio
                    </a>

                    <a class="btn-action"
                       href="/poste/h2h/preview-xml/{pratica_id}"
                       target="_blank">
                        🧪 Anteprima XML
                    </a>

                    {invia_poste_html}

                    <a class="btn-action"
                       href="/dashboard/pratiche/manuale/{pratica_id}"
                       onclick="return confirm('Spostare questa pratica in lavorazione manuale?')">
                        Manuale
                    </a>

                    <a class="btn-action"
                       href="/dashboard/pratiche/completa/{pratica_id}"
                       onclick="return confirm('Confermi di voler COMPLETARE questa pratica?')">
                        Completa
                    </a>

                    <a class="btn-action"
                       href="{pdf_documento_href}"
                       target="_blank">
                        {pdf_documento_label}
                    </a>
                    
                    {ricevuta_cliente_html}

                    {email_cliente_html}
                    
                    {monitor_btn}

                    {ricevuta_poste_html}

                    <a class="btn-action btn-delete"
                       href="/dashboard/pratiche/elimina/{pratica_id}"
                       onclick="return confirm('Confermi di voler eliminare questa pratica?')">
                        Elimina
                    </a>
                </div>
            </td>
        </tr>
        """
        
    telegramma_auto_enabled = os.getenv(
        "TELEGRAMMA_H2H_AUTO_ENABLED",
        "false"
    ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

    telegramma_test_enabled = os.getenv(
        "TELEGRAMMA_H2H_TEST_SEND_ENABLED",
        "false"
    ).strip().lower() in ["true", "1", "yes", "si", "sì", "on"]

    is_sptest = "sptest" in str(POSTE_H2H_TOL_SERVICE_URL).lower()

    if telegramma_auto_enabled and is_sptest and telegramma_test_enabled:
        telegramma_label = "Telegramma: TEST AUTO"
    elif telegramma_auto_enabled and not is_sptest:
        telegramma_label = "Telegramma: AUTO PRODUZIONE"
    else:
        telegramma_label = "Telegramma: MANUALE"

    raccomandata_label = (
        "Raccomandata: AUTO"
        if POSTE_INVIO_AUTO
        else "Raccomandata: MANUALE"
    )

    h2h_mode_label = f"{telegramma_label} · {raccomandata_label}"

    if "AUTO PRODUZIONE" in telegramma_label and POSTE_INVIO_AUTO:
        h2h_led = "🟢"
        h2h_mode_bg = "#16a34a"
    elif "TEST AUTO" in telegramma_label:
        h2h_led = "🟠"
        h2h_mode_bg = "#f97316"
    else:
        h2h_led = "🔴"
        h2h_mode_bg = "#dc2626"


    return f"""
    <html>
    <head>
        <title>Eccomi Posta Dashboard</title>
        <meta charset="utf-8">
        <meta http-equiv="refresh" content="15">

        <style>
            body {{
                font-family: Arial;
                background:#f4f6f9;
                padding:30px;
            }}

            .badge-rr {{
                display:inline-block;
                margin-left:8px;
                background:#f97316;
                color:white;
                padding:4px 8px;
                border-radius:999px;
                font-size:12px;
                font-weight:bold;
            }}

            h1 {{
                color:#222;
                text-align:center;
                margin:0 0 18px 0;
                width:100%;
                font-size:34px;
                font-weight:800;
            }}

            table {{
                width:100%;
                border-collapse:collapse;
                background:white;
                border-radius:12px;
                overflow:hidden;
            }}

            th {{
                background:#111827;
                color:white;
                padding:14px;
                text-align:left;
            }}

            td {{
                padding:12px;
                border-bottom:1px solid #eee;
            }}

            tr:hover {{
                background:#fafafa;
            }}

            a {{
                color:#2563eb;
                text-decoration:none;
                font-weight:bold;
            }}

            .badge {{
                color:white;
                padding:4px 8px;
                border-radius:8px;
                font-size:12px;
                font-weight:bold;
                display:inline-block;
            }}
            
            .receipt-pill {{
                display:inline-block;
                padding:5px 9px;
                border-radius:999px;
                font-size:12px;
                font-weight:bold;
                margin-top:4px;
            }}

           .receipt-ok {{
                background:#dcfce7;
                color:#15803d;
            }}

            .receipt-wait {{
                background:#fff7ed;
                color:#c2410c;
            }}

            .receipt-error {{
                background:#fee2e2;
                color:#b91c1c;
            }}

            .receipt-na {{
                background:#f3f4f6;
                color:#6b7280;
            }}

            .main-row td {{
                border-bottom:0 !important;
            }}

            .action-row td {{
                padding-top:0 !important;
                padding-bottom:18px !important;
                border-bottom:1px solid #e5e7eb;
            }}

            .action-bar {{
                display:flex;
                flex-wrap:wrap;
                gap:8px;
                padding:10px 0 4px 0;
            }}

            .btn-action {{
                display:inline-flex;
                align-items:center;
                justify-content:center;
                background:#eef3ff;
                color:#2563eb;
                padding:8px 12px;
                border-radius:10px;
                font-size:13px;
                margin:0;
                text-decoration:none;
                font-weight:bold;
                min-height:34px;
            }}

            .btn-price {{
                display:inline-flex;
                align-items:center;
                justify-content:center;
                background:#fff7ed;
                color:#c2410c;
                padding:8px 12px;
                border-radius:10px;
                font-size:13px;
                margin:0;
                text-decoration:none;
                font-weight:bold;
                min-height:34px;
            }}

            .btn-send {{
                background:#dcfce7 !important;
                color:#15803d !important;
            }}

            .btn-delete {{
                background:#fee2e2 !important;
                color:#b91c1c !important;
            }}

            .btn-disabled {{
                background:#f3f4f6 !important;
                color:#9ca3af !important;
                cursor:not-allowed;
                pointer-events:none;
            }}

            .email-cell {{
                text-decoration:none !important;
                white-space:nowrap !important;
                word-break:normal !important;
                min-width:140px;
            }}

            .btn-filter-active {{
                background:#111827 !important;
                color:white !important;
            }}

            .legend-box {{
                margin-top:250px;
                padding:18px;
                background:#fff;
                border-radius:14px;
            }}

            .legend-line {{
                display:flex;
                flex-wrap:wrap;
                gap:10px;
            }}

            .legend-line span {{
                color:white;
                padding:8px 12px;
                border-radius:20px;
                display:inline-block;
                font-weight:bold;
            }}

            .footer-brand {{
                text-align:center;
                margin-top:35px;
                color:#6b7280;
                font-size:13px;
            }}

            .topbar-sticky {{
                position: sticky;
                top: 0;
                z-index: 999;
                background: #f4f6f9;
                padding-top: 10px;
                padding-bottom: 10px;
            }}

            .mode-bar {{
                width:100%;
                box-sizing:border-box;
                background:{h2h_mode_bg};
                color:white;
                padding:18px 24px;
                border-radius:18px;
                font-weight:bold;
                font-size:22px;
                display:flex;
                align-items:center;
                justify-content:center;
                box-shadow:0 2px 8px rgba(0,0,0,.10);
                text-align:center;
            }}
            
            /* Bordi colonne dashboard - versione pulita */
            table {{
                border: 2px solid #111827 !important;
                border-collapse: separate !important;
                border-spacing: 0 !important;
            }}

            th {{
                border-right: 1px solid #374151 !important;
            }}

            th:last-child {{
                border-right: none !important;
            }}

            .main-row td {{
                border-right: 1px solid rgba(17, 24, 39, 0.28) !important;
                border-top: 1px solid rgba(17, 24, 39, 0.16) !important;
                border-bottom: none !important;
            }}

            .main-row td:last-child {{
                border-right: none !important;
            }}

            .action-row td {{
                border-right: none !important;
                border-top: none !important;
                border-bottom: 2px solid rgba(17, 24, 39, 0.16) !important;
            }}

            @media (max-width: 700px) {{
                body {{
                    padding:14px !important;
                }}

                h1 {{
                    font-size:24px !important;
                    line-height:1.2 !important;
                    text-align:center !important;
                }}

                .mode-bar {{
                    font-size:18px !important;
                    padding:15px 16px !important;
                    border-radius:16px !important;
                }}

                table, thead, tbody, th, td, tr {{
                    display:block !important;
                    width:100% !important;
                }}

                thead {{
                    display:none !important;
                }}

                .main-row {{
                    background:white !important;
                    margin-bottom:0 !important;
                    border-radius:16px 16px 0 0 !important;
                    padding:14px 14px 0 14px !important;
                    box-shadow:0 2px 10px rgba(0,0,0,.06) !important;
                }}

                .action-row {{
                    background:white !important;
                    margin-bottom:18px !important;
                    border-radius:0 0 16px 16px !important;
                    padding:0 14px 14px 14px !important;
                    box-shadow:0 8px 10px rgba(0,0,0,.04) !important;
                }}

                td {{
                    border:none !important;
                    padding:8px 0 !important;
                    font-size:15px !important;
                    word-break:break-word !important;
                }}

                .email-cell {{
                    white-space:nowrap !important;
                    word-break:normal !important;
                    overflow-wrap:normal !important;
                }}

                .main-row td:nth-child(1)::before {{ content:"Ordine: "; font-weight:bold; }}
                .main-row td:nth-child(2)::before {{ content:"Servizio: "; font-weight:bold; }}
                .main-row td:nth-child(3)::before {{ content:"Email: "; font-weight:bold; }}
                .main-row td:nth-child(4)::before {{ content:"Stato: "; font-weight:bold; }}
                .main-row td:nth-child(5)::before {{ content:"Tracking: "; font-weight:bold; }}
                .main-row td:nth-child(6)::before {{ content:"Data: "; font-weight:bold; }}

                .action-row td::before {{
                    content:"Azioni: ";
                    font-weight:bold;
                    display:block;
                    margin-bottom:8px;
                }}

                .action-bar {{
                    display:flex !important;
                    flex-wrap:wrap !important;
                    gap:8px !important;
                }}

                .action-bar a,
                .action-bar span {{
                    flex:1 1 45%;
                    font-size:13px !important;
                    padding:9px 8px !important;
                }}

                .legend-line {{
                    gap:8px !important;
                }}

                .legend-line span {{
                    font-size:13px !important;
                    padding:7px 10px !important;
                }}
            }}
        </style>
    </head>

    <body>
        <div class="topbar-sticky">

            <div style="margin-bottom:18px;">
                <h1>📬 Eccomi Posta — Dashboard Pratiche</h1>

                <div class="mode-bar">
                    {h2h_led} Modalità: {h2h_mode_label}
                </div>
            </div>

            <div style="display:flex;flex-wrap:wrap;gap:10px;margin:18px 0 25px 0;">

                <a class="btn-action {'btn-filter-active' if not filtro_stato or filtro_stato == 'TUTTI' else ''}"
                   href="/dashboard/pratiche">
                    Tutti ({tot_tutti})
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'ERRORE_POSTE' else ''}"
                   href="/dashboard/pratiche?stato=ERRORE_POSTE">
                    Errori
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'INVIATO_POSTE' else ''}"
                   href="/dashboard/pratiche?stato=INVIATO_POSTE">
                    Inviati
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'MANUALI' else ''}"
                   href="/dashboard/pratiche?stato=MANUALI">
                    Manuali
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'COMPLETATO' else ''}"
                   href="/dashboard/pratiche?stato=COMPLETATO">
                    Completati
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'BOZZA_CHECKOUT' else ''}"
                   href="/dashboard/pratiche?stato=BOZZA_CHECKOUT">
                    Bozze checkout
                </a>

                <a class="btn-action {'btn-filter-active' if filtro_stato == 'NON_PAGATO' else ''}"
                   href="/dashboard/pratiche?stato=NON_PAGATO">
                    Non pagati
                </a>

                <input
                    type="text"
                    id="searchInput"
                    placeholder="Cerca ordine, email, tracking..."
                    style="
                        flex:1;
                        min-width:260px;
                        max-width:420px;
                        padding:10px 14px;
                        border-radius:12px;
                        border:1px solid #d1d5db;
                        font-size:14px;
                        outline:none;
                    "
                >
            </div>

        </div>

        <table>
            <thead>
                <tr>
                    <th>Ordine</th>
                    <th>Servizio</th>
                    <th>Email</th>
                    <th>Stato</th>
                    <th>Tracking</th>
                    <th>Data</th>
                </tr>
            </thead>

            <tbody>
                {rows}
            </tbody>
        </table>

        <div class="legend-box">
            <h3>📌 Legenda Stati</h3>

            <div class="legend-line">
                <span style="background:#3498db;">RICEVUTO</span>
                <span style="background:#0ea5e9;">RICEVUTO_PAGATO</span>
                <span style="background:#27ae60;">INVIATO_POSTE</span>
                <span style="background:#e74c3c;">ERRORE_POSTE</span>
                <span style="background:#f39c12;">LAVORAZIONE_MANUALE</span>
                <span style="background:#8e44ad;">COMPLETATO</span>
                <span style="background:#6366f1;">PREZZATA_DA_CONFERMARE</span>
                <span style="background:#f97316;">RICEVUTO_MANUALE</span>
                <span style="background:#9ca3af;">BOZZA_CHECKOUT</span>
                <span style="background:#6b7280;">NON_PAGATO</span>
            </div>
        </div>

        <div class="footer-brand">
            Progettato ed elaborato by
            <a href="https://www.eccomionline.com" target="_blank">
                www.eccomionline.com
            </a>
        </div>

        <script>
            const searchInput = document.getElementById("searchInput");

            if (searchInput) {{
                searchInput.addEventListener("keyup", function() {{
                    const value = this.value.toLowerCase();

                    document.querySelectorAll("tbody tr").forEach(function(row) {{
                        const text = row.innerText.toLowerCase();

                        if (text.includes(value)) {{
                            row.style.display = "";
                        }} else {{
                            row.style.display = "none";
                        }}
                    }});
                }});
            }}
        </script>
    </body>
    </html>
    """

def estrai_pdf_bytes_ricevuta_poste(poste_result):
    """
    Estrae il PDF dalla risposta RecuperaRicevutaAccettazione.
    Gestisce bytes, stringa PDF o base64.
    """

    contenuto = None

    try:
        contenuto = poste_result["Contenuto"]
    except Exception:
        contenuto = getattr(poste_result, "Contenuto", None)

    if contenuto is None:
        raise ValueError("Contenuto ricevuta Poste non trovato nella risposta")

    if isinstance(contenuto, bytes):
        return contenuto

    if isinstance(contenuto, bytearray):
        return bytes(contenuto)

    if isinstance(contenuto, str):
        text = contenuto.strip()

        if text.startswith("%PDF"):
            return text.encode("latin1")

        try:
            return base64.b64decode(text, validate=True)
        except Exception:
            return text.encode("latin1")

    raise ValueError(f"Formato ricevuta non gestito: {type(contenuto)}")


@app.get("/dashboard/pratiche/ricevuta-poste/{pratica_id}")
def dashboard_salva_ricevuta_poste(pratica_id: str):
    """
    Recupera la ricevuta ufficiale Poste di una pratica già INVIATO_POSTE.
    NON invia una nuova raccomandata.
    NON finalizza nulla.
    Recupera solo la ricevuta già disponibile da Poste.
    """

    try:
        h2h_order_id = resolve_h2h_order_id(pratica_id)

        if not h2h_order_id:
            return {
                "success": False,
                "error": "Ordine H2H collegato non trovato",
                "pratica_id": pratica_id
            }

        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", h2h_order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            return {
                "success": False,
                "error": "Ordine H2H non trovato",
                "h2h_order_id": h2h_order_id
            }

        ordine = ordine_res.data

        id_richiesta = ordine.get("id_richiesta")

        if not id_richiesta:
            return {
                "success": False,
                "error": "id_richiesta mancante: impossibile recuperare ricevuta Poste",
                "h2h_order_id": h2h_order_id
            }

        pdf_esistente = ordine.get("pdf_ricevuta_url")

        if pdf_esistente:
            return RedirectResponse(
                url=pdf_esistente,
                status_code=302
            )

        history = HistoryPlugin()

        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        poste_result = service.RecuperaRicevutaAccettazione(
            IDRichiesta=id_richiesta
        )

        pdf_bytes = estrai_pdf_bytes_ricevuta_poste(poste_result)

        file_path = f"ricevute-poste/{h2h_order_id}/ricevuta_accettazione.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            file_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            file_path
        )

        now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        supabase.table("poste_h2h_orders") \
            .update({
                "pdf_ricevuta_url": pdf_public_url,
                "ricevuta_salvata_at": now_iso,
                "poste_response": str(poste_result)
            }) \
            .eq("id", h2h_order_id) \
            .execute()

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .limit(1) \
            .execute()

        pratica = pratica_res.data[0] if pratica_res.data else {}

        receipt_payload = {
            "pratica_id": str(pratica_id),
            "h2h_order_id": str(h2h_order_id),
            "shopify_order_name": (
                ordine.get("shopify_order_name")
                or pratica.get("shopify_order_name")
                or pratica.get("order_name")
                or ""
            ),
            "id_richiesta": str(id_richiesta),
            "guid_utente": str(ordine.get("guid_utente") or ""),
            "id_ricevuta": str(ordine.get("id_ricevuta") or ""),
            "numero_raccomandata": str(ordine.get("numero_raccomandata") or pratica.get("numero_raccomandata") or ""),
            "costo": ordine.get("costo"),
            "pdf_ricevuta_url": pdf_public_url,
            "tipo_ricevuta": "ACCETTAZIONE_POSTE",
            "poste_response": str(poste_result),
            "updated_at": now_iso
        }

        existing_receipt = supabase.table("poste_h2h_ricevute") \
            .select("id") \
            .eq("h2h_order_id", str(h2h_order_id)) \
            .limit(1) \
            .execute()

        if existing_receipt.data:
            supabase.table("poste_h2h_ricevute") \
                .update(receipt_payload) \
                .eq("id", existing_receipt.data[0].get("id")) \
                .execute()
        else:
            supabase.table("poste_h2h_ricevute") \
                .insert(receipt_payload) \
                .execute()

        return RedirectResponse(
            url=pdf_public_url,
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_RECUPERO_RICEVUTA_POSTE",
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/dashboard/pratiche/monitora/{pratica_id}")
def dashboard_monitora_pratica_poste(pratica_id: str):
    """
    Monitoraggio pratica Eccomi Posta.
    NON invia nulla a Poste.
    TELEGRAMMA: usa GetStatus già esistente.
    RACCOMANDATA: recupera/salva ricevuta ufficiale Poste se disponibile.
    """

    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .limit(1) \
            .execute()

        # Fallback 1: se non è pratiche.id, prova come id_richiesta Poste
        if not pratica_res.data:
            pratica_res = supabase.table("pratiche") \
                .select("*") \
                .eq("id_richiesta", pratica_id) \
                .limit(1) \
                .execute()

        # Fallback 2: prova come order_name / shopify_order_name
        if not pratica_res.data:
            pratica_res = supabase.table("pratiche") \
                .select("*") \
                .or_(
                    f"order_name.eq.{pratica_id},shopify_order_name.eq.{pratica_id},order_id.eq.{pratica_id}"
                ) \
                .limit(1) \
                .execute()

        # Fallback 3: se scrivi 1400, prova anche #1400
        if not pratica_res.data and not str(pratica_id).startswith("#"):
            pratica_res = supabase.table("pratiche") \
                .select("*") \
                .or_(
                    f"order_name.eq.#{pratica_id},shopify_order_name.eq.#{pratica_id}"
                ) \
                .limit(1) \
                .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "step": "PRATICA_NON_TROVATA",
                "input": pratica_id,
                "message": "Nessuna pratica trovata né per id, né per id_richiesta, né per ordine."
            }

        pratica = pratica_res.data[0]
        pratica_id_effettivo = pratica.get("id") or pratica_id
        tipo_servizio = str(pratica.get("tipo_servizio") or "").upper().strip()

        if "RACCOMANDATA" in tipo_servizio:
            return monitora_raccomandata_poste(pratica_id_effettivo, pratica, now_iso)

        if "TELEGRAMMA" in tipo_servizio:
            return monitora_telegramma_poste(pratica_id_effettivo, pratica, now_iso)
            
        return {
            "success": False,
            "step": "TIPO_SERVIZIO_NON_GESTITO",
            "pratica_id": pratica_id,
            "tipo_servizio": tipo_servizio
        }

    except Exception as e:
        return {
            "success": False,
            "step": "ERRORE_MONITORAGGIO_PRATICA",
            "pratica_id": pratica_id,
            "error": str(e)
        }


def monitora_raccomandata_poste(pratica_id: str, pratica: dict, now_iso: str):
    """
    Monitora una Raccomandata già inviata.
    NON invia una nuova raccomandata.
    Recupera solo la ricevuta ufficiale Poste se disponibile.
    """

    try:
        h2h_order_id = resolve_h2h_order_id(pratica_id)

        if not h2h_order_id:
            monitor_payload = {
                "tipo": "RACCOMANDATA",
                "stato_monitoraggio": "H2H_ORDER_NON_TROVATO",
                "last_monitor_at": now_iso,
                "message": "Nessun ordine H2H collegato alla pratica."
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": False,
                "step": "H2H_ORDER_NON_TROVATO",
                "pratica_id": pratica_id,
                "message": "Nessun ordine H2H collegato alla pratica."
            }

        ordine_res = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", h2h_order_id) \
            .single() \
            .execute()

        if not ordine_res.data:
            monitor_payload = {
                "tipo": "RACCOMANDATA",
                "stato_monitoraggio": "H2H_ORDER_NON_PRESENTE",
                "last_monitor_at": now_iso,
                "h2h_order_id": str(h2h_order_id)
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": False,
                "step": "H2H_ORDER_NON_PRESENTE",
                "pratica_id": pratica_id,
                "h2h_order_id": h2h_order_id
            }

        ordine = ordine_res.data
        id_richiesta = ordine.get("id_richiesta")

        if not id_richiesta:
            monitor_payload = {
                "tipo": "RACCOMANDATA",
                "stato_monitoraggio": "ID_RICHIESTA_MANCANTE",
                "last_monitor_at": now_iso,
                "h2h_order_id": str(h2h_order_id),
                "message": "La pratica non ha ancora un id_richiesta Poste."
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": False,
                "step": "ID_RICHIESTA_MANCANTE",
                "pratica_id": pratica_id,
                "h2h_order_id": h2h_order_id,
                "message": "La pratica non ha ancora un id_richiesta Poste."
            }

        pdf_esistente = ordine.get("pdf_ricevuta_url")

        if pdf_esistente:
            monitor_payload = {
                "tipo": "RACCOMANDATA",
                "stato_monitoraggio": "RICEVUTA_POSTE_GIA_PRESENTE",
                "last_monitor_at": now_iso,
                "h2h_order_id": str(h2h_order_id),
                "id_richiesta": str(id_richiesta),
                "pdf_ricevuta_url": pdf_esistente
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": True,
                "step": "RICEVUTA_POSTE_GIA_PRESENTE",
                "pratica_id": pratica_id,
                "h2h_order_id": h2h_order_id,
                "id_richiesta": id_richiesta,
                "pdf_ricevuta_url": pdf_esistente
            }

        history = HistoryPlugin()

        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        poste_result = service.RecuperaRicevutaAccettazione(
            IDRichiesta=id_richiesta
        )

        pdf_bytes = estrai_pdf_bytes_ricevuta_poste(poste_result)

        file_path = f"ricevute-poste/{h2h_order_id}/ricevuta_accettazione.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            file_path,
            pdf_bytes,
            {
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_public_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            file_path
        )

        xml_sent = None
        xml_received = None

        try:
            xml_sent = etree.tostring(
                history.last_sent["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        try:
            xml_received = etree.tostring(
                history.last_received["envelope"],
                pretty_print=True,
                encoding="unicode"
            )
        except Exception:
            pass

        supabase.table("poste_h2h_orders") \
            .update({
                "pdf_ricevuta_url": pdf_public_url,
                "ricevuta_salvata_at": now_iso,
                "poste_response": str(poste_result)
            }) \
            .eq("id", h2h_order_id) \
            .execute()

        receipt_payload = {
            "pratica_id": str(pratica_id),
            "h2h_order_id": str(h2h_order_id),
            "shopify_order_name": (
                ordine.get("shopify_order_name")
                or pratica.get("shopify_order_name")
                or pratica.get("order_name")
                or ""
            ),
            "id_richiesta": str(id_richiesta),
            "guid_utente": str(ordine.get("guid_utente") or ""),
            "id_ricevuta": str(ordine.get("id_ricevuta") or ""),
            "numero_raccomandata": str(
                ordine.get("numero_raccomandata")
                or pratica.get("numero_raccomandata")
                or ""
            ),
            "costo": ordine.get("costo"),
            "pdf_ricevuta_url": pdf_public_url,
            "tipo_ricevuta": "ACCETTAZIONE_POSTE",
            "poste_response": str(poste_result),
            "updated_at": now_iso
        }

        try:
            existing_receipt = supabase.table("poste_h2h_ricevute") \
                .select("id") \
                .eq("h2h_order_id", str(h2h_order_id)) \
                .limit(1) \
                .execute()

            if existing_receipt.data:
                supabase.table("poste_h2h_ricevute") \
                    .update(receipt_payload) \
                    .eq("id", existing_receipt.data[0].get("id")) \
                    .execute()
            else:
                supabase.table("poste_h2h_ricevute") \
                    .insert(receipt_payload) \
                    .execute()

        except Exception as receipt_db_error:
            print("ERRORE_SALVATAGGIO_POSTE_H2H_RICEVUTE:", receipt_db_error)

        monitor_payload = {
            "tipo": "RACCOMANDATA",
            "stato_monitoraggio": "RICEVUTA_POSTE_SALVATA",
            "last_monitor_at": now_iso,
            "h2h_order_id": str(h2h_order_id),
            "id_richiesta": str(id_richiesta),
            "pdf_ricevuta_url": pdf_public_url,
            "pdf_size_bytes": len(pdf_bytes),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

        aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

        return {
            "success": True,
            "step": "RICEVUTA_POSTE_SALVATA",
            "pratica_id": pratica_id,
            "h2h_order_id": h2h_order_id,
            "id_richiesta": id_richiesta,
            "pdf_ricevuta_url": pdf_public_url
        }

    except Exception as e:
        monitor_payload = {
            "tipo": "RACCOMANDATA",
            "stato_monitoraggio": "ERRORE_MONITORAGGIO_RACCOMANDATA",
            "last_monitor_at": now_iso,
            "error": str(e)
        }

        aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

        return {
            "success": False,
            "step": "ERRORE_MONITORAGGIO_RACCOMANDATA",
            "pratica_id": pratica_id,
            "error": str(e)
        }


def monitora_telegramma_poste(pratica_id: str, pratica: dict, now_iso: str):
    """
    Monitora lo stato di un Telegramma già inviato.
    NON invia un nuovo Telegramma.
    NON fa PreConfirm.
    Usa solo GetStatus.
    """

    try:
        get_status_result = telegramma_get_status_debug(pratica_id)

        if not isinstance(get_status_result, dict):
            monitor_payload = {
                "tipo": "TELEGRAMMA",
                "stato_monitoraggio": "ERRORE_GETSTATUS_RISPOSTA_NON_VALIDA",
                "last_monitor_at": now_iso,
                "raw_response": str(get_status_result)
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": False,
                "step": "ERRORE_GETSTATUS_RISPOSTA_NON_VALIDA",
                "pratica_id": pratica_id,
                "response": str(get_status_result)
            }

        if not get_status_result.get("success"):
            monitor_payload = {
                "tipo": "TELEGRAMMA",
                "stato_monitoraggio": "ERRORE_GETSTATUS_TELEGRAMMA",
                "last_monitor_at": now_iso,
                "error": get_status_result.get("error"),
                "get_status_result": get_status_result
            }

            aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

            return {
                "success": False,
                "step": "ERRORE_GETSTATUS_TELEGRAMMA",
                "pratica_id": pratica_id,
                "get_status_result": get_status_result
            }

        status_plain = get_status_result.get("result") or {}

        state = None
        id_telegramma = None

        try:
            details = (
                status_plain.get("Status", {})
                .get("TelgramStatusDetails", {})
                .get("TelegrammaStatusDetailsType", [])
            )

            if isinstance(details, dict):
                details = [details]

            if details:
                first = details[0] or {}
                state = first.get("State")
                id_telegramma = first.get("IDTelegramma")

        except Exception:
            pass

        stato_monitoraggio = "TELEGRAMMA_MONITORATO"

        if state in ["Printing", "Confirmed"]:
            stato_monitoraggio = "TELEGRAMMA_IN_LAVORAZIONE_POSTE"

        if state in ["Delivered", "Recapitato", "Consegnato"]:
            stato_monitoraggio = "TELEGRAMMA_CONSEGNATO"

        monitor_payload = {
            "tipo": "TELEGRAMMA",
            "stato_monitoraggio": stato_monitoraggio,
            "last_monitor_at": now_iso,
            "guid_message": get_status_result.get("guid_message"),
            "state": state,
            "id_telegramma": id_telegramma,
            "get_status_result": status_plain
        }

        aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

        update_data = {
            "updated_at": now_iso
        }

        if id_telegramma and not pratica.get("numero_raccomandata"):
            update_data["numero_raccomandata"] = str(id_telegramma)

        if stato_monitoraggio == "TELEGRAMMA_CONSEGNATO":
            update_data["stato"] = "CONSEGNATO"

        if len(update_data.keys()) > 1:
            supabase.table("pratiche") \
                .update(update_data) \
                .eq("id", pratica_id) \
                .execute()

        return {
            "success": True,
            "step": "TELEGRAMMA_MONITORATO",
            "pratica_id": pratica_id,
            "stato_monitoraggio": stato_monitoraggio,
            "state": state,
            "id_telegramma": id_telegramma,
            "get_status_result": status_plain
        }

    except Exception as e:
        monitor_payload = {
            "tipo": "TELEGRAMMA",
            "stato_monitoraggio": "ERRORE_MONITORAGGIO_TELEGRAMMA",
            "last_monitor_at": now_iso,
            "error": str(e)
        }

        aggiorna_monitoraggio_pratica(pratica_id, pratica, monitor_payload)

        return {
            "success": False,
            "step": "ERRORE_MONITORAGGIO_TELEGRAMMA",
            "pratica_id": pratica_id,
            "error": str(e)
        }


def aggiorna_monitoraggio_pratica(pratica_id: str, pratica: dict, monitor_payload: dict):
    """
    Salva il risultato del monitoraggio dentro pratiche.poste_response.
    Non richiede nuove colonne Supabase.
    Rilegge la pratica prima di salvare per non sovrascrivere altri dati.
    """

    try:
        pratica_fresh_res = supabase.table("pratiche") \
            .select("poste_response") \
            .eq("id", pratica_id) \
            .limit(1) \
            .execute()

        if pratica_fresh_res.data:
            poste_response_attuale = pratica_fresh_res.data[0].get("poste_response") or {}
        else:
            poste_response_attuale = pratica.get("poste_response") or {}

        if isinstance(poste_response_attuale, str):
            try:
                poste_response_attuale = json.loads(poste_response_attuale)
            except Exception:
                poste_response_attuale = {}

        if not isinstance(poste_response_attuale, dict):
            poste_response_attuale = {}

        poste_response_attuale["monitoraggio_poste"] = monitor_payload

        supabase.table("pratiche") \
            .update({
                "poste_response": poste_response_attuale,
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
            }) \
            .eq("id", pratica_id) \
            .execute()

    except Exception as e:
        print("ERRORE_AGGIORNA_MONITORAGGIO_PRATICA:", e)

@app.get("/dashboard/pratiche/monitora-view/{pratica_id}", response_class=HTMLResponse)
def dashboard_monitora_pratica_view(pratica_id: str):
    """
    Pagina operatore per monitorare una pratica Poste.
    Mostra risultato in HTML leggibile, non JSON tecnico.
    NON invia nulla a Poste.
    """

    def esc(value):
        return (
            str(value or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    try:
        monitor_result = dashboard_monitora_pratica_poste(pratica_id)

        if not isinstance(monitor_result, dict):
            monitor_result = {
                "success": False,
                "step": "RISPOSTA_NON_VALIDA",
                "raw": str(monitor_result)
            }

        success = bool(monitor_result.get("success"))
        step = monitor_result.get("step") or "-"
        pratica_id_effettivo = monitor_result.get("pratica_id") or pratica_id

        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id_effettivo) \
            .limit(1) \
            .execute()

        pratica = pratica_res.data[0] if pratica_res.data else {}

        tipo_servizio = str(pratica.get("tipo_servizio") or monitor_result.get("tipo_servizio") or "-").upper()
        order_name = (
            pratica.get("shopify_order_name")
            or pratica.get("order_name")
            or pratica.get("order_id")
            or "-"
        )

        stato_pratica = pratica.get("stato") or "-"
        cliente_email = pratica.get("cliente_email") or "-"

        numero_raccomandata = (
            pratica.get("numero_raccomandata")
            or monitor_result.get("id_telegramma")
            or "-"
        )

        stato_monitoraggio = (
            monitor_result.get("stato_monitoraggio")
            or monitor_result.get("state")
            or step
            or "-"
        )

        state_poste = monitor_result.get("state") or "-"
        id_telegramma = monitor_result.get("id_telegramma") or "-"
        h2h_order_id = monitor_result.get("h2h_order_id") or "-"
        id_richiesta = monitor_result.get("id_richiesta") or pratica.get("id_richiesta") or "-"
        pdf_ricevuta_url = monitor_result.get("pdf_ricevuta_url") or ""

        if success:
            header_color = "#16a34a"
            header_icon = "✅"
            header_title = "Monitoraggio completato"
            header_subtitle = "La pratica è stata controllata correttamente sui sistemi Poste."
        else:
            header_color = "#dc2626"
            header_icon = "⚠️"
            header_title = "Monitoraggio non completato"
            header_subtitle = monitor_result.get("error") or monitor_result.get("message") or "Controlla i dettagli tecnici."

        if tipo_servizio == "TELEGRAMMA":
            if state_poste == "Printing":
                human_status = "Telegramma in lavorazione/stampa presso Poste"
                status_color = "#2563eb"
                status_icon = "🖨️"
            elif state_poste in ["Confirmed"]:
                human_status = "Telegramma confermato da Poste"
                status_color = "#16a34a"
                status_icon = "✅"
            elif state_poste in ["Delivered", "Recapitato", "Consegnato"]:
                human_status = "Telegramma consegnato"
                status_color = "#16a34a"
                status_icon = "📬"
            else:
                human_status = stato_monitoraggio
                status_color = "#6366f1"
                status_icon = "📨"

            servizio_box = f"""
            <div class="status-card">
                <div class="status-icon">{status_icon}</div>
                <div>
                    <h2>{esc(human_status)}</h2>
                    <p>Stato tecnico Poste: <strong>{esc(state_poste)}</strong></p>
                    <p>Numero Telegramma: <strong>{esc(id_telegramma)}</strong></p>
                </div>
            </div>
            """

        elif tipo_servizio == "RACCOMANDATA":
            if pdf_ricevuta_url:
                human_status = "Ricevuta ufficiale Poste disponibile"
                status_color = "#16a34a"
                status_icon = "📄"
            else:
                human_status = "Ricevuta Poste non ancora disponibile"
                status_color = "#f97316"
                status_icon = "⏳"

            ricevuta_button = ""

            if pdf_ricevuta_url:
                ricevuta_button = f"""
                <a class="btn-main"
                   href="{esc(pdf_ricevuta_url)}"
                   target="_blank">
                    📄 Apri ricevuta ufficiale Poste
                </a>
                """

            servizio_box = f"""
            <div class="status-card">
                <div class="status-icon">{status_icon}</div>
                <div>
                    <h2>{esc(human_status)}</h2>
                    <p>ID richiesta Poste: <strong>{esc(id_richiesta)}</strong></p>
                    <p>Ordine tecnico H2H: <strong>{esc(h2h_order_id)}</strong></p>
                    {ricevuta_button}
                </div>
            </div>
            """

        else:
            status_color = "#6b7280"
            servizio_box = f"""
            <div class="status-card">
                <div class="status-icon">ℹ️</div>
                <div>
                    <h2>Servizio non riconosciuto</h2>
                    <p>{esc(tipo_servizio)}</p>
                </div>
            </div>
            """

        raw_json = json.dumps(monitor_result, ensure_ascii=False, indent=2)

        html = f"""
        <html>
        <head>
            <title>Monitoraggio pratica {esc(order_name)}</title>
            <meta charset="utf-8">
            <style>
                body {{
                    font-family: Arial, Helvetica, sans-serif;
                    background: #f4f6f9;
                    padding: 30px;
                    color: #111827;
                }}

                .page {{
                    max-width: 980px;
                    margin: 0 auto;
                }}

                .top {{
                    background: {header_color};
                    color: white;
                    padding: 28px;
                    border-radius: 22px;
                    box-shadow: 0 8px 24px rgba(0,0,0,.12);
                    margin-bottom: 22px;
                }}

                .top h1 {{
                    margin: 0 0 8px 0;
                    font-size: 34px;
                }}

                .top p {{
                    margin: 0;
                    font-size: 17px;
                    opacity: .95;
                }}

                .grid {{
                    display: grid;
                    grid-template-columns: repeat(2, minmax(0, 1fr));
                    gap: 16px;
                    margin-bottom: 18px;
                }}

                .card {{
                    background: white;
                    border-radius: 18px;
                    padding: 20px;
                    box-shadow: 0 2px 10px rgba(0,0,0,.06);
                }}

                .card h3 {{
                    margin-top: 0;
                    color: #0f172a;
                }}

                .status-card {{
                    background: white;
                    border-left: 8px solid {status_color};
                    border-radius: 18px;
                    padding: 24px;
                    box-shadow: 0 2px 10px rgba(0,0,0,.06);
                    display: flex;
                    gap: 18px;
                    align-items: flex-start;
                    margin-bottom: 18px;
                }}

                .status-icon {{
                    font-size: 44px;
                    line-height: 1;
                }}

                .status-card h2 {{
                    margin: 0 0 10px 0;
                    font-size: 25px;
                    color: #111827;
                }}

                .badge {{
                    display: inline-block;
                    background: #eef3ff;
                    color: #2563eb;
                    padding: 8px 12px;
                    border-radius: 999px;
                    font-weight: bold;
                }}

                .btn-row {{
                    display: flex;
                    flex-wrap: wrap;
                    gap: 10px;
                    margin: 22px 0;
                }}

                .btn-main {{
                    display: inline-block;
                    background: #2563eb;
                    color: white;
                    padding: 12px 18px;
                    border-radius: 12px;
                    text-decoration: none;
                    font-weight: bold;
                }}

                .btn-secondary {{
                    display: inline-block;
                    background: #e5e7eb;
                    color: #111827;
                    padding: 12px 18px;
                    border-radius: 12px;
                    text-decoration: none;
                    font-weight: bold;
                }}

                pre {{
                    background: #111827;
                    color: #d1d5db;
                    padding: 18px;
                    border-radius: 14px;
                    overflow: auto;
                    max-height: 480px;
                    white-space: pre-wrap;
                    word-break: break-word;
                    font-size: 13px;
                }}

                @media (max-width: 760px) {{
                    body {{
                        padding: 14px;
                    }}

                    .top h1 {{
                        font-size: 26px;
                    }}

                    .grid {{
                        grid-template-columns: 1fr;
                    }}

                    .status-card {{
                        flex-direction: column;
                    }}
                }}
            </style>
        </head>

        <body>
            <div class="page">
                <div class="top">
                    <h1>{header_icon} {esc(header_title)}</h1>
                    <p>{esc(header_subtitle)}</p>
                </div>

                <div class="btn-row">
                    <a class="btn-secondary" href="/dashboard/pratiche">
                        ← Torna alla dashboard
                    </a>

                    <a class="btn-secondary" href="/dashboard/pratiche/{esc(pratica_id_effettivo)}">
                        Dettaglio pratica
                    </a>

                    <a class="btn-main" href="/dashboard/pratiche/monitora-view/{esc(pratica_id)}">
                        🔄 Ricontrolla ora
                    </a>
                </div>

                {servizio_box}

                <div class="grid">
                    <div class="card">
                        <h3>📌 Pratica</h3>
                        <p><strong>Ordine:</strong> {esc(order_name)}</p>
                        <p><strong>Servizio:</strong> <span class="badge">{esc(tipo_servizio)}</span></p>
                        <p><strong>Stato dashboard:</strong> {esc(stato_pratica)}</p>
                        <p><strong>Email cliente:</strong> {esc(cliente_email)}</p>
                    </div>

                    <div class="card">
                        <h3>🏛️ Dati Poste</h3>
                        <p><strong>ID richiesta:</strong> {esc(id_richiesta)}</p>
                        <p><strong>Numero/Tracking:</strong> {esc(numero_raccomandata)}</p>
                        <p><strong>Stato monitoraggio:</strong> {esc(stato_monitoraggio)}</p>
                        <p><strong>Step:</strong> {esc(step)}</p>
                    </div>
                </div>

                <div class="card">
                    <h3>🧪 Dettaglio tecnico</h3>
                    <pre>{esc(raw_json)}</pre>
                </div>
            </div>
        </body>
        </html>
        """

        return HTMLResponse(html)

    except Exception as e:
        errore = esc(str(e))

        return HTMLResponse(
            f"""
            <html>
            <body style="font-family:Arial;padding:30px;background:#f4f6f9;">
                <div style="background:white;border-radius:16px;padding:24px;max-width:800px;margin:auto;">
                    <h1>⚠️ Errore monitoraggio pratica</h1>
                    <p>{errore}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </div>
            </body>
            </html>
            """,
            status_code=500
        )

@app.get("/dashboard/pratiche/ricevuta-poste-telegramma/{pratica_id}")
def dashboard_ricevuta_poste_telegramma(pratica_id: str):
    """
    Genera/apre PDF interno Eccomi/Poste per Telegramma.
    NON chiama Poste.
    NON invia email.
    NON genera costi.
    """

    try:
        pratica_res = supabase.table("pratiche") \
            .select("*") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return HTMLResponse(
                f"""
                <html>
                <body style="font-family:Arial;padding:30px;">
                    <h2>Pratica non trovata</h2>
                    <p>ID: {pratica_id}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </body>
                </html>
                """,
                status_code=404
            )

        pratica = pratica_res.data

        tipo_servizio = str(pratica.get("tipo_servizio") or "").upper().strip()

        if tipo_servizio != "TELEGRAMMA":
            return HTMLResponse(
                f"""
                <html>
                <body style="font-family:Arial;padding:30px;">
                    <h2>Servizio non valido</h2>
                    <p>Questa funzione è disponibile solo per Telegramma.</p>
                    <p>Pratica: {pratica_id}</p>
                    <a href="/dashboard/pratiche">← Torna alla dashboard</a>
                </body>
                </html>
                """,
                status_code=400
            )

        poste_response = pratica.get("poste_response") or {}

        if isinstance(poste_response, str):
            try:
                poste_response = json.loads(poste_response)
            except Exception:
                poste_response = {}

        if not isinstance(poste_response, dict):
            poste_response = {}

        pdf_esistente = (
            poste_response.get("ricevuta_poste_telegramma_url")
            or poste_response.get("pdf_ricevuta_poste_interna_url")
            or ""
        )

        if pdf_esistente:
            return RedirectResponse(
                url=pdf_esistente,
                status_code=303
            )

        pdf_bytes = genera_pdf_interno_monitoraggio_telegramma_bytes(pratica)

        storage_path = f"telegrammi/{pratica_id}/ricevuta_poste_interna_monitoraggio.pdf"

        supabase.storage.from_(SUPABASE_BUCKET).upload(
            storage_path,
            pdf_bytes,
            file_options={
                "content-type": "application/pdf",
                "upsert": "true"
            }
        )

        pdf_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(
            storage_path
        )

        poste_response["ricevuta_poste_telegramma_url"] = pdf_url
        poste_response["pdf_ricevuta_poste_interna_url"] = pdf_url
        poste_response["ricevuta_poste_telegramma_generata_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        poste_response["ricevuta_poste_telegramma_generata_da"] = "dashboard_ricevuta_poste_telegramma"

        supabase.table("pratiche").update({
            "poste_response": poste_response,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).eq("id", pratica_id).execute()

        return RedirectResponse(
            url=pdf_url,
            status_code=303
        )

    except Exception as e:
        return HTMLResponse(
            f"""
            <html>
            <body style="font-family:Arial;padding:30px;">
                <h2>Errore ricevuta Poste interna Telegramma</h2>
                <pre>{str(e)}</pre>
                <a href="/dashboard/pratiche">← Torna alla dashboard</a>
            </body>
            </html>
            """,
            status_code=500
        )


@app.get("/dashboard/pratiche/{pratica_id}", response_class=HTMLResponse)
def dashboard_pratica_dettaglio(pratica_id: str):

    result = supabase.table("pratiche") \
        .select("*") \
        .eq("id", pratica_id) \
        .single() \
        .execute()

    if not result.data:
        return """
        <html>
        <body style="font-family:Arial;padding:30px;">
            <h1>Pratica non trovata</h1>
            <a href="/dashboard/pratiche">Torna alla dashboard</a>
        </body>
        </html>
        """

    p = result.data
    mittente = p.get("mittente") or {}
    destinatario = p.get("destinatario") or {}

    return f"""
    <html>
    <head>
        <title>Dettaglio pratica {p.get('order_name')}</title>
        <style>
            body {{
                font-family: Arial;
                background:#f4f6f9;
                padding:30px;
            }}
            .card {{
                background:white;
                border-radius:14px;
                padding:22px;
                margin-bottom:20px;
                box-shadow:0 2px 10px rgba(0,0,0,.06);
            }}
            pre {{
                background:#111827;
                color:#d1d5db;
                padding:16px;
                border-radius:12px;
                overflow:auto;
                max-height:420px;
            }}
            a {{
                color:#2563eb;
                font-weight:bold;
                text-decoration:none;
            }}
        </style>
    </head>

    <body>
        <h1>📄 Dettaglio pratica {p.get('order_name')}</h1>

        <p>
            <a href="/dashboard/pratiche">← Torna alla dashboard</a>
        </p>

        <div class="card">
            <h2>Stato</h2>
            <p><strong>{p.get('stato')}</strong></p>
            <p><strong>ID richiesta:</strong> {p.get('id_richiesta') or '-'}</p>
            <p><strong>Servizio:</strong> {p.get('tipo_servizio')}</p>
            <p><strong>Email cliente:</strong> {p.get('cliente_email')}</p>
        </div>

        <div class="card">
            <h2>Mittente</h2>
            <div class="detail-box">
                {mittente.get('nome') or mittente.get('raw') or '-'}<br>
                {mittente.get('via') or ''} {mittente.get('civico') or ''}<br>
                {mittente.get('cap') or ''} {mittente.get('comune') or ''} ({mittente.get('provincia') or ''})<br>
                {mittente.get('contatto') or ''}
            </div>
        </div>

        <div class="card">
            <h2>Destinatario</h2>
            <div class="detail-box">
                {destinatario.get('nome') or destinatario.get('raw') or '-'}<br>
                {destinatario.get('via') or ''} {destinatario.get('civico') or ''}<br>
                {destinatario.get('cap') or ''} {destinatario.get('comune') or ''} ({destinatario.get('provincia') or ''})<br>
                {destinatario.get('contatto') or ''}
            </div>
        </div>

        <div class="card">
            <h2>Contenuto documento</h2>
            <p>{p.get('testo') or '-'}</p>
            <p><strong>Servizio:</strong> {p.get('tipo_servizio') or '-'}</p>
        </div>

        <div class="card">
            <h2>Risposta Poste</h2>
            <pre>{json.dumps(p.get('poste_response'), ensure_ascii=False, indent=2) if p.get('poste_response') else '-'}</pre>
        </div>

        <div class="card">
            <h2>XML inviato</h2>
            <pre>{p.get('xml_sent') or '-'}</pre>
        </div>

        <div class="card">
            <h2>XML ricevuto</h2>
            <pre>{p.get('xml_received') or '-'}</pre>
        </div>
    </body>
    </html>
    """

@app.get("/dashboard/pratiche/manuale/{pratica_id}")
def dashboard_pratica_manuale(pratica_id: str):

    supabase.table("pratiche").update({
        "stato": "LAVORAZIONE_MANUALE",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).eq("id", pratica_id).execute()

    return RedirectResponse(
        url="/dashboard/pratiche?stato=MANUALI",
        status_code=302
    )


@app.get("/dashboard/pratiche/completa/{pratica_id}")
def dashboard_pratica_completa(pratica_id: str):

    try:
        pratica_res = supabase.table("pratiche") \
            .select("id,stato,tipo_servizio,numero_raccomandata") \
            .eq("id", pratica_id) \
            .single() \
            .execute()

        if not pratica_res.data:
            return {
                "success": False,
                "error": "Pratica non trovata",
                "pratica_id": pratica_id
            }

        pratica = pratica_res.data

        stato = pratica.get("stato")
        tipo_servizio = pratica.get("tipo_servizio")
        numero_raccomandata = pratica.get("numero_raccomandata")

        # Sicurezza:
        # una Raccomandata non può essere marcata COMPLETATA
        # se prima non è stata realmente inviata a Poste.
        if tipo_servizio == "RACCOMANDATA":
            if stato != "INVIATO_POSTE" and not numero_raccomandata:
                return {
                    "success": False,
                    "blocked": True,
                    "error": "Una raccomandata può essere completata solo dopo INVIATO_POSTE",
                    "stato": stato,
                    "numero_raccomandata": numero_raccomandata,
                    "pratica_id": pratica_id
                }

        supabase.table("pratiche").update({
            "stato": "COMPLETATO",
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
        }).eq("id", pratica_id).execute()

        return RedirectResponse(
            url="/dashboard/pratiche?stato=COMPLETATO",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "error": str(e),
            "pratica_id": pratica_id
        }

@app.get("/dashboard/pratiche/elimina/{pratica_id}")
def dashboard_pratica_elimina(pratica_id: str):

    supabase.table("pratiche") \
        .delete() \
        .eq("id", pratica_id) \
        .execute()

    return RedirectResponse(
        url="/dashboard/pratiche",
        status_code=302
    )

@app.get("/dashboard/pratiche/pdf/{pratica_id}")
def dashboard_pratica_pdf(pratica_id: str):

    # 1. Cerca direttamente in poste_h2h_orders
    result_h2h = supabase.table("poste_h2h_orders") \
        .select("*") \
        .or_(f"id.eq.{pratica_id},id_richiesta.eq.{pratica_id}") \
        .execute()

    if result_h2h.data:
        ordine = result_h2h.data[0]
        pdf_url = (
            ordine.get("pdf_ricevuta_cliente_url")
            or ordine.get("pdf_ricevuta_url")
            or ordine.get("pdf_url")
        )
        if pdf_url:
            return RedirectResponse(url=pdf_url, status_code=302)

    # 2. Cerca nella tabella pratiche
    result = supabase.table("pratiche") \
        .select("*") \
        .or_(f"id.eq.{pratica_id},id_richiesta.eq.{pratica_id}") \
        .execute()

    if result.data:
        pratica = result.data[0]
        pratica_pdf_url = pratica.get("pdf_url")

        # 3. Se la pratica ha il PDF originale, cerca la relativa riga H2H
        if pratica_pdf_url:
            result_h2h_by_pdf = supabase.table("poste_h2h_orders") \
                .select("*") \
                .eq("pdf_url", pratica_pdf_url) \
                .execute()

            if result_h2h_by_pdf.data:
                ordine = result_h2h_by_pdf.data[0]
                pdf_cliente = (
                    ordine.get("pdf_ricevuta_cliente_url")
                    or ordine.get("pdf_ricevuta_url")
                    or ordine.get("pdf_url")
                )

                if pdf_cliente:
                    return RedirectResponse(url=pdf_cliente, status_code=302)

        # 4. Fallback: se non trova H2H, apre il PDF pratica
        pdf_url = (
            pratica.get("pdf_ricevuta_cliente_url")
            or pratica.get("pdf_ricevuta_url")
            or pratica.get("pdf_url")
        )

        if pdf_url:
            return RedirectResponse(url=pdf_url, status_code=302)

    return {
        "success": False,
        "error": "PDF non disponibile per questa pratica"
    }

@app.get("/dashboard/pratiche/pdf-telegramma/{pratica_id}")
def dashboard_pdf_telegramma_testo(pratica_id: str):
    import io
    import json
    import datetime

    result = supabase.table("pratiche") \
        .select("*") \
        .eq("id", pratica_id) \
        .single() \
        .execute()

    if not result.data:
        return {
            "success": False,
            "error": "Pratica non trovata",
            "pratica_id": pratica_id
        }

    pratica = result.data

    if pratica.get("tipo_servizio") != "TELEGRAMMA":
        return {
            "success": False,
            "error": "Questa pratica non è un Telegramma",
            "pratica_id": pratica_id
        }

    def parse_address(value):
        if isinstance(value, dict):
            return value

        if isinstance(value, str):
            try:
                return json.loads(value)
            except Exception:
                return {"raw": value}

        return {}

    mittente = parse_address(pratica.get("mittente"))
    destinatario = parse_address(pratica.get("destinatario"))

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)

    width, height = A4
    y = height - 2.2 * cm

    c.setFont("Helvetica-Bold", 18)
    c.drawCentredString(width / 2, y, "TELEGRAMMA")
    y -= 1.2 * cm

    c.setFont("Helvetica", 10)
    c.drawCentredString(width / 2, y, "Documento generato da Eccomi Posta")
    y -= 1.4 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Ordine:")
    c.setFont("Helvetica", 11)
    c.drawString(4 * cm, y, str(pratica.get("order_name") or pratica.get("shopify_order_name") or "-"))
    y -= 0.7 * cm

    c.setFont("Helvetica-Bold", 11)
    c.drawString(2 * cm, y, "Data:")
    c.setFont("Helvetica", 11)

    try:
        from zoneinfo import ZoneInfo

        created_raw = pratica.get("created_at")

        if created_raw:
            created_dt = datetime.datetime.fromisoformat(
                str(created_raw).replace("Z", "+00:00")
            )

            if created_dt.tzinfo is None:
                created_dt = created_dt.replace(tzinfo=datetime.timezone.utc)

            data_pdf = created_dt.astimezone(
                ZoneInfo("Europe/Rome")
            ).strftime("Roma, %d/%m/%Y")
        else:
            data_pdf = datetime.datetime.now(
                ZoneInfo("Europe/Rome")
            ).strftime("Roma, %d/%m/%Y")

    except Exception:
        data_pdf = datetime.datetime.now().strftime("Roma, %d/%m/%Y")

    c.drawString(4 * cm, y, data_pdf)
    y -= 1.2 * cm

    def draw_address(title, data):
        nonlocal y

        c.setFont("Helvetica-Bold", 12)
        c.drawString(2 * cm, y, title)
        y -= 0.6 * cm

        righe = [
            data.get("nome") or data.get("raw") or "-",
            f"{data.get('via') or ''} {data.get('civico') or ''}".strip(),
            f"{data.get('cap') or ''} {data.get('comune') or ''} ({data.get('provincia') or ''})".strip(),
            data.get("contatto") or ""
        ]

        c.setFont("Helvetica", 11)

        for riga in righe:
            if riga:
                c.drawString(2 * cm, y, riga)
                y -= 0.55 * cm

        y -= 0.5 * cm

    draw_address("MITTENTE", mittente)
    draw_address("DESTINATARIO", destinatario)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(2 * cm, y, "TESTO TELEGRAMMA")
    y -= 0.8 * cm

    testo = str(pratica.get("testo") or "").strip()

    c.setFont("Times-Roman", 14)

    max_width = width - 4 * cm
    words = testo.split()
    line = ""

    for word in words:
        test_line = (line + " " + word).strip()

        if c.stringWidth(test_line, "Times-Roman", 14) <= max_width:
            line = test_line
        else:
            c.drawString(2 * cm, y, line)
            y -= 0.65 * cm
            line = word

            if y < 2.5 * cm:
                c.showPage()
                y = height - 2 * cm
                c.setFont("Times-Roman", 14)

    if line:
        c.drawString(2 * cm, y, line)
        y -= 1.2 * cm

    c.setFont("Helvetica", 10)
    c.drawString(2 * cm, 1.5 * cm, "Documento non ancora ricevuta ufficiale Poste.")

    c.save()
    buffer.seek(0)

    return Response(
        content=buffer.getvalue(),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'inline; filename="telegramma-{pratica.get("order_name") or pratica_id}.pdf"'
        }
    )


@app.get("/dashboard/pratiche/errore/{pratica_id}")
def dashboard_pratica_errore(pratica_id: str):

    supabase.table("pratiche").update({
        "stato": "ERRORE_POSTE",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).eq("id", pratica_id).execute()

    return {
        "success": True,
        "pratica_id": pratica_id,
        "nuovo_stato": "ERRORE_POSTE"
    }
