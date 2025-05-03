from dotenv import load_dotenv
import os
from flask import Flask, redirect, request
import requests

load_dotenv()

CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = "http://localhost:5050/callback"
SCOPES = "openid profile email accounting.transactions accounting.settings offline_access"
ACCESS_TOKEN = os.getenv("ACCESS_TOKEN")
TENANT_ID = os.getenv("TENANT_ID")

app = Flask(__name__)

@app.route("/")
def home():
    # Build the authorization URL manually
    auth_url = (
        "https://login.xero.com/identity/connect/authorize"
        "?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={' '.join(SCOPES.split())}"
    )
    return redirect(auth_url)

@app.route("/callback")
def callback():
    code = request.args.get("code")
    if not code:
        return "No code found in callback URL."
    try:
        # Exchange code for token using requests
        token_response = requests.post(
            "https://identity.xero.com/connect/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ).json()
        access_token = token_response["access_token"]

        # Get connections (tenants) using requests
        connections = requests.get(
            "https://api.xero.com/connections",
            headers={"Authorization": f"Bearer {access_token}"}
        ).json()
        tenant_id = connections[0]["tenantId"]
        print("Access token:", access_token)
        print("Tenant ID:", tenant_id)
        return f"Access token: {access_token}<br>Tenant ID: {tenant_id}<br>You can close this window."
    except Exception as e:
        print("Error in callback:", e)
        return f"Error: {e}"

if __name__ == "__main__":
    app.run(port=5050, debug=True)