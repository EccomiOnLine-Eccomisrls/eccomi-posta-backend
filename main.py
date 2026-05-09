from fastapi import FastAPI, UploadFile, File, Form
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from reportlab.lib.units import cm
from supabase import create_client
import datetime
import os

app = FastAPI()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")
SUPABASE_BUCKET = os.getenv("SUPABASE_BUCKET", "eccomi-posta")

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


def genera_pdf_da_testo(pdf_path, mittente, destinatario, oggetto, testo, firma):
    c = canvas.Canvas(pdf_path, pagesize=A4)
    width, height = A4
    y = height - 2 * cm

    def draw_wrapped(text, x, y, max_width, size=11, font="Times-Roman", line_height=0.55 * cm):
        c.setFont(font, size)
        for raw_line in (text or "").split("\n"):
            words = raw_line.split()
            line = ""
            if not words:
                y -= line_height
                continue

            for word in words:
                test = f"{line} {word}".strip()
                if c.stringWidth(test, font, size) <= max_width:
                    line = test
                else:
                    c.drawString(x, y, line)
                    y -= line_height
                    line = word

                    if y < 2.5 * cm:
                        c.showPage()
                        y = height - 2 * cm
                        c.setFont(font, size)

            if line:
                c.drawString(x, y, line)
                y -= line_height

        return y

    c.setFont("Times-Roman", 11)

    y = draw_wrapped(mittente, 2 * cm, y, width - 4 * cm, 11)
    y -= 0.5 * cm

    c.drawRightString(width - 2 * cm, y, datetime.datetime.now().strftime("%d/%m/%Y"))
    y -= 1.2 * cm

    y = draw_wrapped(destinatario, 2 * cm, y, width - 4 * cm, 11)
    y -= 0.8 * cm

    if oggetto:
        c.setFont("Times-Bold", 12)
        c.drawString(2 * cm, y, f"Oggetto: {oggetto}")
        y -= 1 * cm

    y = draw_wrapped(testo, 2 * cm, y, width - 4 * cm, 11)

    y -= 1 * cm
    if y < 4 * cm:
        c.showPage()
        y = height - 2 * cm

    if firma:
        c.setFont("Times-Roman", 11)
        c.drawRightString(width - 2 * cm, y, "Distinti saluti")
        y -= 1.2 * cm

        c.setFont("Times-Italic", 14)
        c.drawRightString(width - 2 * cm, y, firma)

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
    file: UploadFile = File(None)
):
    try:
        now = datetime.datetime.now()
        anno = now.year
        timestamp = now.strftime("%d/%m/%Y %H:%M:%S")

        token = f"RACC-{anno}-{order_id}"
        pratica_dir = f"data/{token}"
        os.makedirs(pratica_dir, exist_ok=True)

        with open(f"{pratica_dir}/pratica.txt", "w", encoding="utf-8") as f:
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

                pdf_path = f"{pratica_dir}/documento.pdf"

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
                firma=firma or ""
            )
            pdf_saved = True

        # UPLOAD PDF SU SUPABASE
        storage_path = f"raccomandate/{token}/documento.pdf"

        with open(pdf_path, "rb") as f:
            supabase.storage.from_(SUPABASE_BUCKET).upload(
                path=storage_path,
                file=f,
                file_options={
                    "content-type": "application/pdf",
                    "upsert": "true"
                }
            )

        pdf_url = supabase.storage.from_(SUPABASE_BUCKET).get_public_url(storage_path)

        return {
            "success": True,
            "token": token,
            "pdf_saved": pdf_saved,
            "folder": pratica_dir,
            "pdf_url": pdf_url
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
        filename=f"{token}.pdf"
    )
