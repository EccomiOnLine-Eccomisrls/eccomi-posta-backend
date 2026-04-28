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
    file: UploadFile = File(None)
):
    try:
        anno = datetime.datetime.now().year

        token = f"RACC-{anno}-{order_id}"

        # SALVATAGGIO BASE (MVP)
        os.makedirs("data", exist_ok=True)

        with open(f"data/{token}.txt", "w") as f:
            f.write(f"MITTENTE: {mittente}\n")
            f.write(f"DESTINATARIO: {destinatario}\n\n")
            if testo:
                f.write("TESTO:\n" + testo)

        # Se file PDF
        if file:
            contents = await file.read()
            with open(f"data/{token}.pdf", "wb") as f:
                f.write(contents)

        return {
            "success": True,
            "token": token
        }

    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }
