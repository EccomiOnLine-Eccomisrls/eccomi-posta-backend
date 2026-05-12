import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
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
import datetime
import os
import hashlib
import base64
import requests
import uuid

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
        
        md5_pdf = hashlib.md5(pdf_bytes).hexdigest()

        documento = DocumentoType(
            Immagine=pdf_base64,
            TipoDocumento="PDF",
            MD5=md5_pdf
        )

        submit = ROLSubmitType(
            Mittente=MittenteType(
                Nominativo=nom_mitt,
                InviaStampa=False
            ),
            Destinatari={
                "Destinatario": [
                    DestinatarioType(
                        Nominativo=nom_dest,
                        IdDestinatario="1"
                    )
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

        pdf_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(storage_path)

        return {
            "success": True,
            "token": token,
            "pdf_saved": pdf_saved,
            "folder": pratica_dir,
            "pdf_url": pdf_url,
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


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
