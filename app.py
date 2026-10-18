"""Standalone authorized-media transfer API; no Telegram bot starts here."""
import logging
import os
from flask import Flask, jsonify
from relay_api import install_relay_routes

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = int(float(os.getenv("RELAY_MAX_SEGMENT_MB", "128")) * 1024 * 1024) + 1024 * 1024

@app.get("/")
@app.get("/health")
def health():
    return "OK - lecture-bridge is running", 200, {"Content-Type": "text/plain; charset=utf-8"}

install_relay_routes(app)
