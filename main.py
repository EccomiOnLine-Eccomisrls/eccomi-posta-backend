from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import datetime
import os

app = FastAPI()

# CORS (fondamentale per Shopify)
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

        # CARTELLA PRATICA
        pratica_dir = f"data/{token}"
        os.makedirs(pratica_dir, exist_ok=True)

        # FILE INFO PRATICA
        with open(
            f"{pratica_dir}/pratica.txt",
            "w",
            encoding="utf-8"
        ) as f:

            f.write(f"TOKEN: {token}\n")
            f.write(f"DATA CREAZIONE: {timestamp}\n")
            f.write(f"ORDER ID: {order_id}\n\n")

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

        pdf_saved = False

        # SALVATAGGIO PDF
        if file:

            contents = await file.read()

            with open(
                f"{pratica_dir}/documento.pdf",
                "wb"
            ) as f:
                f.write(contents)

            pdf_saved = True

        return {
            "success": True,
            "token": token,
            "pdf_saved": pdf_saved,
            "folder": pratica_dir
        }

    except Exception as e:

        return {
            "success": False,
            "error": str(e)
        }
