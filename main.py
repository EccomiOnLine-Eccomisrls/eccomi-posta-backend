import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from fastapi import FastAPI, UploadFile, File, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from supabase import create_client
from requests import Session
from requests.auth import HTTPBasicAuth
from zeep import Client, Plugin
from zeep.plugins import HistoryPlugin
from zeep.transports import Transport
from zeep.wsa import WsAddressingPlugin
from zeep.xsd import AnySimpleType
from lxml import etree
from urllib.parse import urljoin
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from io import BytesIO
import datetime
import os
import hashlib
import base64
import requests
import uuid
import json
import time

app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "eccomi-posta")

POSTE_H2H_USERID = os.getenv("POSTE_H2H_USERID")
POSTE_H2H_PASSWORD = os.getenv("POSTE_H2H_PASSWORD")
POSTE_H2H_CONTRACT_ID = os.getenv("POSTE_H2H_CONTRACT_ID")

POSTE_H2H_ROL_WSDL = os.getenv(
    "POSTE_H2H_ROL_WSDL",
    "https://cewebservices.posteitaliane.it/ROLGC/RolService.WSDL"
)

POSTE_H2H_SERVICE_URL = "https://cewebservices.posteitaliane.it/ROLGC/RolService.svc"
POSTE_H2H_BINDING = "{http://ComunicazioniElettroniche.ROL.WS}BasicHttpBinding_ROLServiceSoap"

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

def salva_poste_h2h_order(data: dict):
    try:
        res = supabase.table("poste_h2h_orders").insert(data).execute()
        return res.data
    except Exception as e:
        print("ERRORE SALVATAGGIO SUPABASE:", str(e))
        return None


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


@app.get("/")
def home():
    return {"status": "Eccomi Posta Backend OK 🚀"}

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

@app.get("/poste/h2h/invio-test-v2")
def poste_invio_test_v2():
    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")
        ROLSubmitType = client.get_type("ns0:ROLSubmit")

        def crea_nominativo(nome, cognome, cap, citta, prov, via, civico):
            indirizzo = IndirizzoType(
                DUG="VIA",
                Toponimo=via,
                NumeroCivico=civico
            )
            return NominativoType(
                Nome=nome,
                Cognome=cognome,
                CAP=cap,
                Citta=citta,
                Provincia=prov,
                Indirizzo=indirizzo,
                TipoIndirizzo="NORMALE",
                ForzaDestinazione=True,
                InesitateDigitali=False,
                CodiceFiscaleResult=0
            )

        nom_mitt = crea_nominativo("MARIO", "ROSSI", "00184", "ROMA", "RM", "NAZIONALE", "1")
        nom_dest = crea_nominativo("LUCA", "BIANCHI", "00138", "ROMA", "RM", "APPIA NUOVA", "1")

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.drawString(100, 750, "Test invio Poste H2H Eccomi Posta")
        c.drawString(100, 720, "Destinatario: LUCA BIANCHI")
        c.drawString(100, 700, "VIA APPIA NUOVA 1")
        c.drawString(100, 680, "00138 ROMA RM")
        c.showPage()
        c.save()

        pdf_bytes = buffer.getvalue()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest()
 
        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="PDF",
            MD5=md5_pdf
        )

        submit = ROLSubmitType(
            Mittente=MittenteType(
                Nominativo=nom_mitt,
                InviaStampa=True
            ),
            Destinatari={
                "Destinatario": [
                    DestinatarioType(Nominativo=nom_dest)                
                ]
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
                "DataStampa": datetime.datetime.now().replace(microsecond=0),
                "InserisciMittente": True,
                "Archiviazione": False,
                "AnniArchiviazioneSpecified": False,
                "FirmaElettronica": False,
                "AnniArchiviazione": 0,
                "ArchiviazioneDocumenti": ""
            },
            PrezzaturaSincrona=True,
            Nazionale=True,
            ForzaInvioDestinazioniValide=False
        )

        id_richiesta = str(uuid.uuid4())

        result = service.Invio(
            IDRichiesta=id_richiesta,
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit=submit
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
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/invio-test-v3")
def poste_invio_test_v3():
    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")

        def crea_nominativo(nome, cognome, cap, citta, prov, via, civico):
            indirizzo = IndirizzoType(
                DUG="VIA",
                Toponimo=via,
                NumeroCivico=civico
            )
            return NominativoType(
                Nome=nome,
                Cognome=cognome,
                CAP=cap,
                Citta=citta,
                Provincia=prov,
                Indirizzo=indirizzo,
                TipoIndirizzo="NORMALE",
                ForzaDestinazione=True,
                InesitateDigitali=False,
                CodiceFiscaleResult=0
            )

        nom_mitt = crea_nominativo("MARIO", "ROSSI", "00184", "ROMA", "RM", "NAZIONALE", "1")
        nom_dest = crea_nominativo("LUCA", "BIANCHI", "00138", "ROMA", "RM", "APPIA NUOVA", "1")

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.drawString(100, 750, "Test invio Poste H2H Eccomi Posta")
        c.drawString(100, 720, "Destinatario: LUCA BIANCHI")
        c.drawString(100, 700, "VIA APPIA NUOVA 1")
        c.drawString(100, 680, "00138 ROMA RM")
        c.showPage()
        c.save()

        pdf_bytes = buffer.getvalue()
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
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
        }

@app.get("/poste/h2h/invio-test-v4")
def poste_invio_test_v4():

    history = HistoryPlugin()

    try:

        client, service = poste_client(
            timeout=60,
            extra_plugins=[history]
        )

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")

        # =========================
        # FUNZIONE NOMINATIVO
        # =========================

        def crea_nominativo(
            nome,
            cognome,
            cap,
            citta,
            prov,
            via,
            civico,
            complemento=""
        ):

            indirizzo = IndirizzoType(
                DUG="VIA",
                Toponimo=via,
                NumeroCivico=civico
            )

            return NominativoType(
                Nome=nome,
                Cognome=cognome,
                CAP=cap,
                Citta=citta,
                Provincia=prov,
                Indirizzo=indirizzo,
                TipoIndirizzo="NORMALE",
                ForzaDestinazione=True,
                InesitateDigitali=False,
                CodiceFiscaleResult=0,
                ComplementoIndirizzo=complemento
            )

        # =========================
        # MITTENTE REALE
        # =========================

        nom_mitt = crea_nominativo(
            "VERUSKA",
            "SCAGLIONE",
            "10135",
            "TORINO",
            "TO",
            "PIOBESI",
            "5"
        )

        # =========================
        # DESTINATARIO REALE
        # =========================

        nom_dest = crea_nominativo(
            "GIANNI",
            "RANIOLO",
            "97017",
            "SANTA CROCE CAMERINA",
            "RG",
            "NEBRODI",
            "2/B",
            "FRAZIONE DI CASUZZE"
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        # =========================
        # PDF REALE
        # =========================

        buffer = BytesIO()

        c = canvas.Canvas(
            buffer,
            pagesize=A4
        )

        c.drawString(
            100,
            750,
            "Test invio Poste H2H Eccomi Posta"
        )

        c.drawString(
            100,
            720,
            "Destinatario: GIANNI RANIOLO"
        )

        c.drawString(
            100,
            700,
            "VIA NEBRODI 2/B"
        )

        c.drawString(
            100,
            680,
            "FRAZIONE DI CASUZZE"
        )

        c.drawString(
            100,
            660,
            "97017 SANTA CROCE CAMERINA RG"
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
            TipoDocumento="PDF",
            MD5=md5_pdf
        )

        # =========================
        # ID RICHIESTA
        # =========================

        id_richiesta = str(uuid.uuid4())

        # =========================
        # INVIO
        # =========================

        result = service.Invio(

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

                    "DataStampa": datetime.datetime.now().replace(
                        microsecond=0
                    ),

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

            "error": str(e),

            "xml_sent": xml_sent,

            "xml_received": xml_received

        }

@app.get("/poste/h2h/invio-test-v6")
def poste_invio_test_v6():

    history = HistoryPlugin()

    try:
        client, service = poste_client(timeout=60, extra_plugins=[history])

        NominativoType = client.get_type("ns1:Nominativo")
        IndirizzoType = client.get_type("ns1:Indirizzo")
        MittenteType = client.get_type("ns1:Mittente")
        DestinatarioType = client.get_type("ns1:Destinatario")
        DocumentoType = client.get_type("ns1:Documento")

        # 1. ID RICHIESTA PRESO DA POSTE
        id_result = service.RecuperaIdRichiesta()

        id_richiesta = None
        try:
            id_richiesta = id_result.IDRichiesta
        except:
            pass

        if not id_richiesta:
            try:
                id_richiesta = id_result["IDRichiesta"]
            except:
                pass

        if not id_richiesta and isinstance(id_result, str):
            id_richiesta = id_result

        if not id_richiesta:
            return {
                "success": False,
                "step": "RecuperaIdRichiesta",
                "error": "Impossibile leggere IDRichiesta dalla risposta Poste",
                "raw_result": str(id_result)
            }

        def crea_nominativo(nome, cognome, cap, citta, prov, via, civico, complemento=""):
            indirizzo = IndirizzoType(
                DUG="VIA",
                Toponimo=via,
                NumeroCivico=civico
            )

            return NominativoType(
                Nome=nome,
                Cognome=cognome,
                CAP=cap,
                Citta=citta,
                Provincia=prov,
                Indirizzo=indirizzo,
                TipoIndirizzo="NORMALE",
                ForzaDestinazione=True,
                InesitateDigitali=False,
                CodiceFiscaleResult=0,
                ComplementoIndirizzo=complemento
            )

        nom_mitt = crea_nominativo(
            "VERUSKA",
            "SCAGLIONE",
            "10135",
            "TORINO",
            "TO",
            "PIOBESI",
            "5"
        )

        nom_dest = crea_nominativo(
            "GIANNI",
            "RANIOLO",
            "97017",
            "SANTA CROCE CAMERINA",
            "RG",
            "NEBRODI",
            "2/B",
            "FRAZIONE DI CASUZZE"
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.drawString(100, 750, "Test invio Poste H2H Eccomi Posta")
        c.drawString(100, 720, "Destinatario: GIANNI RANIOLO")
        c.drawString(100, 700, "VIA NEBRODI 2/B")
        c.drawString(100, 680, "FRAZIONE DI CASUZZE")
        c.drawString(100, 660, "97017 SANTA CROCE CAMERINA RG")
        c.showPage()
        c.save()

        pdf_bytes = buffer.getvalue()
        pdf_base64 = base64.b64encode(pdf_bytes).decode("utf-8")
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest().upper()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        result = service.Invio(
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
            "step": "Invio v6",
            "id_richiesta": id_richiesta,
            "recupera_id_result": str(id_result),
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

@app.post("/shopify/webhook/order-created")
async def shopify_order_created(request: Request):
    try:
        payload = await request.json()

        order_id = payload.get("id")
        order_name = payload.get("name")
        email = payload.get("email") or payload.get("contact_email")

        line_items = payload.get("line_items", [])
        poste_items = []

        for item in line_items:
            title = (item.get("title") or "").lower()
            sku = (item.get("sku") or "").lower()
            properties = item.get("properties", [])

            is_raccomandata_principale = (
                ("raccomandata" in title and "ricevuta di ritorno" not in title)
                or "eolraccomandata" in sku
            )

            if is_raccomandata_principale:
                poste_items.append({
                    "title": item.get("title"),
                    "sku": item.get("sku"),
                    "properties": properties
                })

        if not poste_items:
            return {
                "success": True,
                "message": "Ordine ricevuto ma nessun prodotto Eccomi Posta principale trovato",
                "order": order_name
            }

        saved_items = []

        for poste_item in poste_items:
            props = {
                p.get("name"): p.get("value")
                for p in poste_item.get("properties", [])
            }

            insert_result = supabase.table("poste_h2h_orders").insert({
                "stato": "RICEVUTO",
                "mittente": {
                    "raw": props.get("Mittente")
                },
                "destinatario": {
                    "raw": props.get("Destinatario")
                },
                "pdf_url": props.get("_PDF pratica"),
                "poste_response": str({
                    "shopify_order_id": str(order_id),
                    "shopify_order_name": str(order_name),
                    "email": email,
                    "item": poste_item,
                    "properties": props
                })
            }).execute()

            saved_items.append(insert_result.data)

        return {
            "success": True,
            "message": "Ordine Eccomi Posta salvato in Supabase",
            "order_id": order_id,
            "order_name": order_name,
            "email": email,
            "saved_items": saved_items
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/process-order/{order_id}")
def process_poste_order(order_id: str):

    history = HistoryPlugin()

    try:
        client, service = poste_client(
            timeout=120,
            extra_plugins=[history]
        )

        ordine = supabase.table("poste_h2h_orders") \
            .select("*") \
            .eq("id", order_id) \
            .single() \
            .execute()

        if not ordine.data:
            return {
                "success": False,
                "error": "Ordine non trovato"
            }

        ordine = ordine.data
        pdf_url = ordine.get("pdf_url")

        if not pdf_url:
            return {
                "success": False,
                "error": "PDF non presente"
            }

        response_pdf = requests.get(pdf_url)

        if response_pdf.status_code != 200:
            return {
                "success": False,
                "error": "Impossibile scaricare PDF"
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

        indirizzo_mitt = IndirizzoType(
            DUG="VIALE",
            Toponimo="STEFANO D'ARRIGO",
            NumeroCivico="321"
        )

        nom_mitt = NominativoType(
            Nome="SALVATORE",
            Cognome="DEL LIBANO",
            CAP="00131",
            Citta="ROMA",
            Provincia="RM",
            Indirizzo=indirizzo_mitt,
            TipoIndirizzo="NORMALE",
            ForzaDestinazione=True,
            InesitateDigitali=False,
            CodiceFiscaleResult=0
        )

        mittente = MittenteType(
            Nominativo=nom_mitt,
            InviaStampa=False
        )

        indirizzo_dest = IndirizzoType(
            DUG="VIA",
            Toponimo="PRAGA",
            NumeroCivico="7"
        )

        nom_dest = NominativoType(
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

        destinatario = DestinatarioType(
            Nominativo=nom_dest
        )

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="pdf",
            MD5=md5_pdf
        )

        id_result = service.RecuperaIdRichiesta()
        id_richiesta = id_result.IDRichiesta

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

        richiesta = RichiestaType(
            IDRichiesta=id_richiesta,
            GuidUtente=guid_utente
        )

        valorizza_result = service.Valorizza(
            Richieste=[richiesta]
        )

        supabase.table("poste_h2h_orders") \
            .update({
                "stato": "PREZZATA_DA_CONFERMARE",
                "id_richiesta": id_richiesta,
                "guid_utente": guid_utente,
                "poste_response": str(valorizza_result)
            }) \
            .eq("id", order_id) \
            .execute()

        return {
            "success": True,
            "step": "VALORIZZATA_DA_CONFERMARE",
            "order_id": order_id,
            "id_richiesta": id_richiesta,
            "guid_utente": guid_utente,
            "message": "Ordine valorizzato. Ora eseguire confirm-order."
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
            "error": str(e),
            "xml_sent": xml_sent,
            "xml_received": xml_received
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

@app.get("/poste/h2h/find-tipo-indirizzo-raw")
def find_tipo_indirizzo_raw():
    try:
        session = Session()
        session.auth = HTTPBasicAuth(POSTE_H2H_USERID, POSTE_H2H_PASSWORD)
        session.verify = False

        visited = set()
        found = []

        def fetch_and_scan(url):
            if url in visited:
                return
            visited.add(url)

            r = session.get(url, timeout=30, verify=False)
            text = r.text

            if "TipoIndirizzo" in text or "NominativoTipoIndirizzo" in text:
                found.append({
                    "url": url,
                    "snippet": text[max(0, text.find("TipoIndirizzo") - 1000): text.find("TipoIndirizzo") + 2000]
                })

            root = etree.fromstring(r.content)
            for el in root.xpath("//*[@schemaLocation]"):
                loc = el.attrib.get("schemaLocation")
                if loc:
                    next_url = urljoin(url, loc)
                    fetch_and_scan(next_url)

        fetch_and_scan(POSTE_H2H_ROL_WSDL)

        return {
            "success": True,
            "visited": list(visited),
            "found": found
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/find-pagesize")
def poste_find_pagesize():
    try:
        client, service = poste_client(timeout=30)

        found = []

        for t in client.wsdl.types.types:
            text = str(t)
            if "PageSize" in text or "page" in text.lower() or "a4" in text.lower():
                found.append(text)

        return {
            "success": True,
            "found": found
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/pagesize-type")
def poste_pagesize_type():
    try:
        client, service = poste_client(timeout=30)

        checks = [
            "ns1:PageSize",
            "ns1:FormatoPagina",
            "ns1:TipoFormato",
        ]

        result = {}

        for item in checks:
            try:
                result[item] = str(client.get_type(item))
            except Exception as e:
                result[item] = f"ERRORE: {str(e)}"

        return {
            "success": True,
            "result": result
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
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
            TipoIndirizzo="NORMAL",
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


@app.post("/raccomandata")
async def crea_raccomandata(
    order_id: str = Form(...),
    mittente: str = Form(...),
    destinatario: str = Form(...),
    testo: str = Form(None),
    oggetto: str = Form(None),
    firma: str = Form(None),
    pagine: str = Form(None),
    ricevuta_ritorno: str = Form(None),
    metodo: str = Form(None),
    file: UploadFile = File(None),
):
    try:

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
            f.write("STATO: RICEVUTA\n\n")

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

        try:

            supabase.table("pratiche").insert({

                "order_id": str(order_id),

                "order_name": str(order_id),

                "tipo_servizio": "RACCOMANDATA",

                "cliente_email": "",

                "mittente": {
                    "raw": mittente
                },

                "destinatario": {
                    "raw": destinatario
                },

                "testo": testo or "",

                "parole": 0,

                "pdf_url": pdf_url,

                "stato": "RICEVUTO"

            }).execute()

        except Exception as db_error:

            print(
                "ERRORE SALVATAGGIO PRATICA RACCOMANDATA:",
                str(db_error)
            )

        return {

            "success": True,

            "token": token,

            "pdf_saved": pdf_saved,

            "folder": pratica_dir,

            "pdf_url": pdf_url,

            "stato": "RICEVUTO"

        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }


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

    dug_list = ["VIA", "VIALE", "PIAZZA", "PIAZZALE", "VICOLO", "VICO", "STRADA", "CORSO", "LOCALITÀ", "LOCALITA", "CIRCONVALLAZIONE"]

    first = parts[0].upper()

    if first in dug_list:
        return first, " ".join(parts[1:]).upper()

    return "VIA", via.upper()

@app.get("/shopify/telegramma/order")
def shopify_telegramma_order_info():
    return {
        "success": True,
        "message": "Endpoint attivo. Usa POST per inviare un ordine Shopify."
    }


@app.post("/shopify/telegramma/order")
async def shopify_telegramma_order(request: Request):
    try:
        order = await request.json()

        order_id = order.get("id")
        order_name = order.get("name")
        email = order.get("email")

        telegrammi = []

        for item in order.get("line_items", []):
            title = item.get("title", "")

            if "TELEGRAMMA" not in title.upper():
                continue

            props = {}

            for p in item.get("properties", []):
                name = p.get("name")
                value = p.get("value")
                props[name] = value

            telegrammi.append({
                "order_id": order_id,
                "order_name": order_name,
                "email": email,
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
                "telegrammi": telegrammi
            }, f, ensure_ascii=False, indent=2)

        for tg in telegrammi:
            try:
                supabase.table("pratiche").insert({
                    "order_id": str(order_id),
                    "order_name": order_name,
                    "tipo_servizio": "TELEGRAMMA",
                    "cliente_email": email,
                    "mittente": tg.get("mittente"),
                    "destinatario": tg.get("destinatario"),
                    "testo": tg.get("testo"),
                    "parole": int(tg.get("parole") or 0),
                    "stato": "RICEVUTO"
                }).execute()
            except Exception as db_error:
                print("ERRORE SALVATAGGIO PRATICA:", str(db_error))

        return {
            "success": True,
            "order_id": order_id,
            "order_name": order_name,
            "telegrammi_trovati": len(telegrammi),
            "telegrammi": telegrammi
        }

    except Exception as e:
        return {
            "success": False,
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
            TipoIndirizzo="NORMALE",
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
        TipoDocumento="PDF",
        MD5=hashlib.md5(pdf_bytes).hexdigest()
    )

    id_richiesta = str(uuid.uuid4())

    result = service.Invio(
        IDRichiesta=id_richiesta,
        Cliente=POSTE_H2H_USERID,
        CodiceContratto=POSTE_H2H_CONTRACT_ID,
        ROLSubmit={
            "Mittente": MittenteType(Nominativo=nom_mitt, InviaStampa=False),
            "Destinatari": {"Destinatario": [DestinatarioType(Nominativo=nom_dest)]},
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
    try:
        invia_telegramma_pratica_h2h(pratica_id)

        return RedirectResponse(
            url="/dashboard/pratiche",
            status_code=302
        )

    except Exception as e:
        return {
            "success": False,
            "pratica_id": pratica_id,
            "error": str(e)
        }

@app.get("/shopify/telegramma/process-pending")
def process_pending_telegrammi():

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

@app.get("/dashboard/pratiche", response_class=HTMLResponse)
def dashboard_pratiche(stato: str = None):

    filtro_stato = stato

    query = supabase.table("pratiche") \
        .select("*") \
        .order("created_at", desc=True) \
        .limit(100)

    if filtro_stato:
        query = query.eq("stato", filtro_stato)

    result = query.execute()
    pratiche = result.data or []

    tot_errori = len([p for p in pratiche if p.get("stato") == "ERRORE_POSTE"])
    tot_inviati = len([p for p in pratiche if p.get("stato") == "INVIATO_POSTE"])
    tot_manuali = len([p for p in pratiche if p.get("stato") == "LAVORAZIONE_MANUALE"])
    tot_completati = len([p for p in pratiche if p.get("stato") == "COMPLETATO"])

    rows = ""

    for p in pratiche:

        stato_pratica = p.get("stato", "-")
        created_raw = p.get("created_at") or ""
        data_breve = created_raw.replace("T", " ")[:16]
        cliente_email = p.get("cliente_email") or "-"
        email_breve = cliente_email if len(cliente_email) <= 14 else cliente_email[:11] + "..."
        colore = "#999"

        if stato_pratica == "RICEVUTO":
            colore = "#3498db"
        elif stato_pratica == "INVIATO_POSTE":
            colore = "#27ae60"
        elif stato_pratica == "ERRORE_POSTE":
            colore = "#e74c3c"
        elif stato_pratica == "LAVORAZIONE_MANUALE":
            colore = "#f39c12"
        elif stato_pratica == "COMPLETATO":
            colore = "#8e44ad"

        rows += f"""
        <tr>
            <td>{p.get('order_name')}</td>
            <td>{p.get('tipo_servizio')}</td>
            <td class="email-cell" title="{cliente_email}">{email_breve}</td>
            <td>
                <span class="badge" style="background:{colore};">
                    {stato_pratica}
                </span>
            </td>
            <td>{data_breve}</td>
            <td class="actions">
                <a class="btn-action" href="/dashboard/pratiche/{p.get('id')}" target="_blank">Dettaglio</a>
                <a class="btn-action" href="/shopify/telegramma/invia-pratica/{p.get('id')}" target="_blank">Reinvia</a>
                <a class="btn-action" href="/dashboard/pratiche/manuale/{p.get('id')}" target="_blank">Manuale</a>
                <a class="btn-action" href="/dashboard/pratiche/completa/{p.get('id')}" target="_blank" onclick="return confirm('Confermi di voler COMPLETARE questa pratica?')">Completa</a>
                <a class="btn-action" href="/dashboard/pratiche/pdf/{p.get('id')}" target="_blank">PDF</a>
            </td>
        </tr>
        """

    return f"""
    <html>
    <head>
        <title>Eccomi Posta Dashboard</title>
        <meta http-equiv="refresh" content="15">

        <style>
            body {{
                font-family: Arial;
                background:#f4f6f9;
                padding:30px;
            }}

            h1 {{
                color:#222;
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
            td a[href^="mailto"],
            td a[x-apple-data-detectors] {{
                color:inherit !important;
                text-decoration:none !important;
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

            .actions {{
                white-space:normal;
            }}

            .btn-action {{
                display:inline-block;
                background:#eef3ff;
                color:#2563eb;
                padding:6px 9px;
                border-radius:8px;
                font-size:13px;
                margin:3px;
                text-decoration:none;
                font-weight:bold;
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

            @media (max-width: 700px) {{
                body {{
                    padding:14px !important;
                }}

                h1 {{
                    font-size:24px !important;
                    line-height:1.2 !important;
                }}

                table, thead, tbody, th, td, tr {{
                    display:block !important;
                    width:100% !important;
                }}

                thead {{
                    display:none !important;
                }}

                tr {{
                    background:white !important;
                    margin-bottom:18px !important;
                    border-radius:16px !important;
                    padding:14px !important;
                    box-shadow:0 2px 10px rgba(0,0,0,.06) !important;
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

                td:nth-child(1)::before {{ content:"Ordine: "; font-weight:bold; }}
                td:nth-child(2)::before {{ content:"Servizio: "; font-weight:bold; }}
                td:nth-child(3)::before {{ content:"Email: "; font-weight:bold; }}
                td:nth-child(4)::before {{ content:"Stato: "; font-weight:bold; }}
                td:nth-child(5)::before {{ content:"Data: "; font-weight:bold; }}
                td:nth-child(6)::before {{ content:"Azioni: "; font-weight:bold; }}

                .actions a {{
                    display:inline-block;
                    margin:4px 6px 4px 0;
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

        <h1>📬 Eccomi Posta — Dashboard Pratiche</h1>

        <div style="display:flex;flex-wrap:wrap;gap:14px;margin:25px 0;">
            <div style="background:#e74c3c;color:white;padding:14px 20px;border-radius:16px;font-weight:bold;font-size:18px;">
                🔴 Errori: {tot_errori}
            </div>

            <div style="background:#27ae60;color:white;padding:14px 20px;border-radius:16px;font-weight:bold;font-size:18px;">
                🟢 Inviati: {tot_inviati}
            </div>

            <div style="background:#f39c12;color:white;padding:14px 20px;border-radius:16px;font-weight:bold;font-size:18px;">
                🟠 Manuali: {tot_manuali}
            </div>

            <div style="background:#8e44ad;color:white;padding:14px 20px;border-radius:16px;font-weight:bold;font-size:18px;">
                🟣 Completati: {tot_completati}
            </div>

            <div style="background:#111827;color:white;padding:14px 20px;border-radius:16px;font-weight:bold;font-size:18px;">
                🔄 Auto-refresh: 15s
            </div>
        </div>

        <div style="display:flex;flex-wrap:wrap;gap:10px;margin:20px 0 25px 0;">
            <a class="btn-action {'btn-filter-active' if not filtro_stato else ''}" href="/dashboard/pratiche">Tutti</a>
            <a class="btn-action {'btn-filter-active' if filtro_stato == 'ERRORE_POSTE' else ''}" href="/dashboard/pratiche?stato=ERRORE_POSTE">Errori</a>
            <a class="btn-action {'btn-filter-active' if filtro_stato == 'INVIATO_POSTE' else ''}" href="/dashboard/pratiche?stato=INVIATO_POSTE">Inviati</a>
            <a class="btn-action {'btn-filter-active' if filtro_stato == 'LAVORAZIONE_MANUALE' else ''}" href="/dashboard/pratiche?stato=LAVORAZIONE_MANUALE">Manuali</a>
            <a class="btn-action {'btn-filter-active' if filtro_stato == 'COMPLETATO' else ''}" href="/dashboard/pratiche?stato=COMPLETATO">Completati</a>
        </div>

        <table>
            <thead>
                <tr>
                    <th>Ordine</th>
                    <th>Servizio</th>
                    <th>Email</th>
                    <th>Stato</th>
                    <th>Data</th>
                    <th>Azione</th>
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
                <span style="background:#27ae60;">INVIATO_POSTE</span>
                <span style="background:#e74c3c;">ERRORE_POSTE</span>
                <span style="background:#f39c12;">LAVORAZIONE_MANUALE</span>
                <span style="background:#8e44ad;">COMPLETATO</span>
            </div>
        </div>

    </body>
    </html>
    """

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
              {(p.get('mittente') or {}).get('raw', '-').replace(' - ', '<br>', 1).replace(', ', '<br>', 1)}
            </div>
        </div>

        <div class="card">
            <h2>Destinatario</h2>
            <div class="detail-box">
              {(p.get('destinatario') or {}).get('raw', '-').replace(' - ', '<br>', 1).replace(', ', '<br>', 1)}
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

    return {
        "success": True,
        "pratica_id": pratica_id,
        "nuovo_stato": "LAVORAZIONE_MANUALE"
    }

@app.get("/dashboard/pratiche/completa/{pratica_id}")
def dashboard_pratica_completa(pratica_id: str):

    supabase.table("pratiche").update({
        "stato": "COMPLETATO",
        "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()
    }).eq("id", pratica_id).execute()

    return {
        "success": True,
        "pratica_id": pratica_id,
        "nuovo_stato": "COMPLETATO"
    }

@app.get("/dashboard/pratiche/pdf/{pratica_id}")
def dashboard_pratica_pdf(pratica_id: str):

    result = supabase.table("pratiche") \
        .select("*") \
        .eq("id", pratica_id) \
        .single() \
        .execute()

    if not result.data:
        return {"success": False, "error": "Pratica non trovata"}

    pratica = result.data

    pdf_url = pratica.get("pdf_url")

    if pdf_url:
        return RedirectResponse(url=pdf_url, status_code=302)

    return {
        "success": False,
        "error": "PDF non disponibile per questa pratica"
    }


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
