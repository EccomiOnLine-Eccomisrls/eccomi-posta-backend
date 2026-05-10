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
from zeep import Client
from zeep.transports import Transport
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
    "https://cewebservices.posteitaliane.it/ROLGC/RolService.svc?wsdl"
)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {"status": "Eccomi Posta Backend OK 🚀"}

@app.get("/poste/h2h/test")
def test_poste_h2h():
    try:
        session = Session()

        session.auth = HTTPBasicAuth(
            POSTE_H2H_USERID,
            POSTE_H2H_PASSWORD
        )

        session.verify = False

        transport = Transport(
            session=session,
            timeout=30
        )

        client = Client(
            wsdl=POSTE_H2H_ROL_WSDL,
            transport=transport
        )

        operations = []

        for service in client.wsdl.services.values():
            for port in service.ports.values():
                operations.extend(list(port.binding._operations.keys()))

        return {
            "success": True,
            "service": "Poste H2H Raccomandata Online",
            "userid": POSTE_H2H_USERID,
            "contract_id": POSTE_H2H_CONTRACT_ID,
            "operations": operations
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/operations")
def poste_operations():
    try:
        session = Session()

        session.auth = HTTPBasicAuth(
            POSTE_H2H_USERID,
            POSTE_H2H_PASSWORD
        )

        session.verify = False

        transport = Transport(
            session=session,
            timeout=30
        )

        client = Client(
            wsdl=POSTE_H2H_ROL_WSDL,
            transport=transport
        )

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
        return {
            "success": False,
            "error": str(e)
        }

@app.get("/poste/h2h/types")
def poste_types():
    try:
        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_USERID,
            POSTE_H2H_PASSWORD
        )
        session.verify = False

        transport = Transport(
            session=session,
            timeout=30
        )

        client = Client(
            wsdl=POSTE_H2H_ROL_WSDL,
            transport=transport
        )

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

        session = Session()
        session.auth = HTTPBasicAuth(
            POSTE_H2H_USERID,
            POSTE_H2H_PASSWORD
        )
        session.verify = False

        transport = Transport(
            session=session,
            timeout=60
        )

        client = Client(
            wsdl=POSTE_H2H_ROL_WSDL,
            transport=transport
        )

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

        service = client.create_service(
            "{http://tempuri.org/}BasicHttpBinding_IRolService",
            "https://cewebservices.posteitaliane.it/ROLGC/RolService.svc"
        )

        result = service.InvioDoc(
            Richiesta=richiesta,
            Documento=documento
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

    # MITTENTE
    y = draw_lines(format_indirizzo_blocco(mittente), left, y, size=10.5)
    y -= 1.0 * cm

    # DATA
    c.setFont("Times-Roman", 10.5)
    c.drawRightString(right, y, f"Roma, {datetime.datetime.now().strftime('%d/%m/%Y')}")
    y -= 1.5 * cm

    # DESTINATARIO
    y = draw_lines(format_indirizzo_blocco(destinatario), left, y, size=10.5)
    y -= 1.0 * cm

    # OGGETTO
    if oggetto:
        c.setFont("Times-Bold", 11)
        c.drawString(left, y, "OGGETTO:")
        c.setFont("Times-Roman", 11)
        c.drawString(left + 2.5 * cm, y, oggetto.upper())
        y -= 1.2 * cm

    # TESTO
    y = draw_wrapped(
        testo or "",
        left,
        y,
        right - left,
        size=10.8,
        line_height=0.68 * cm
    )

    # FIRMA
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
