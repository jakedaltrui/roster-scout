"""
Run this once locally to generate a fresh OAuth token.
Requires credentials.json (the OAuth client secret from Google Cloud Console).

    python3 get_token.py

Copy the printed JSON and set it as GOOGLE_TOKEN_JSON on Render.
"""
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

flow = InstalledAppFlow.from_client_secrets_file("oauth_credentials.json", SCOPES)
creds = flow.run_local_server(port=8080)

token_json = creds.to_json()

with open("token.json", "w") as f:
    f.write(token_json)

print("\n--- Token saved to token.json ---")
print("--- Also copying below for GOOGLE_TOKEN_JSON on Render ---\n")
print(token_json)
