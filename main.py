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
from zeep.transports import Transport
from zeep.wsa import WsAddressingPlugin
from zeep.xsd import AnySimpleType
from lxml import etree
import datetime
import os
import hashlib
import base64


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


def poste_client(timeout=60):
    session = Session()
    session.auth = HTTPBasicAuth(POSTE_H2H_USERID, POSTE_H2H_PASSWORD)
    session.verify = False

    transport = Transport(session=session, timeout=timeout)

    client = Client(
        wsdl=POSTE_H2H_ROL_WSDL,
        transport=transport,
        plugins=[
            WsAddressingPlugin(),
            ForcePosteAddressPlugin()
        ]
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
            Indirizzo=indirizzo_mitt,
            TipoIndirizzo="NORMAL",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0,
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
            Indirizzo=indirizzo_dest,
            TipoIndirizzo="NORMAL",
            ForzaDestinazione=False,
            InesitateDigitali=False,
            CodiceFiscaleResult=0,
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

        stampa = {
            "ResolutionX": "300",
            "ResolutionY": "300",
            "BW": "true",
            "FronteRetro": "false",
            "PageSize": "A4"
        }

        submit = ROLSubmit(

            Mittente=mittente,
            Destinatari={"Destinatario": [destinatario]},
            NumeroDestinatari=1,
            Documento=[documento],
            Opzioni={
                "OpzionidiStampa": stampa,
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

        result = service.Invio(
            IDRichiesta="TEST-INVIO-001",
            Cliente=POSTE_H2H_USERID,
            CodiceContratto=POSTE_H2H_CONTRACT_ID,
            ROLSubmit=submit
        )

        return {
           "success": True,
           "result": str(result)
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
