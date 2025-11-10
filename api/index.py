# api/index.py
from vercel_wsgi import handle
from app import app  # import Flask app từ app.py

def handler(request, context):
    return handle(request, app)
