from fastapi import FastAPI

app = FastAPI(
    title="TranscriptsKE API",
    description="Academic transcript request and verification platform for Kenya.",
    version="0.1.0",
)


@app.get("/")
def root():
    return {"message": " Welcome to Transcripts Kenya"}


@app.get("/health")
def health_check():
    return {"status": "app running"}
