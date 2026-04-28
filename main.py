from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
import os
from datetime import datetime

app = FastAPI()

# CORS (per Shopify)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# cartella storage locale (MVP)
UPLOAD_DIR = "data"
os.makedirs(UPLOAD_DIR, exist_ok=True)


def genera_token(order_id: str):
    year = datetime.now().year
    return f"RACC-{year}-{order_id}"


@app.get("/")
def home():
    return {"status": "Eccomi Posta Backend OK 🚀"}


@app.post("/raccomandata")
async def crea_raccomandata(
    order_id: str = Form(...),
    mittente: str = Form(...),
    destinatario: str = Form(...),
    testo: str = Form(None),
    file: UploadFile = File(None),
):
    token = genera_token(order_id)

    record = {
        "token": token,
        "order_id": order_id,
        "mittente": mittente,
        "destinatario": destinatario,
        "testo": testo,
        "file": None
    }

    # salva PDF se presente
    if file:
        file_path = f"{UPLOAD_DIR}/{token}_{file.filename}"
        with open(file_path, "wb") as f:
            f.write(await file.read())
        record["file"] = file_path

    # salva record semplice (MVP)
    with open(f"{UPLOAD_DIR}/{token}.txt", "w") as f:
        f.write(str(record))

    return {
        "success": True,
        "token": token
    }
