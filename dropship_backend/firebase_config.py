import firebase_admin
from firebase_admin import credentials, firestore
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Create credentials dictionary from environment variables
cred_dict = {
    "type": "service_account",
    "project_id": os.getenv('FIREBASE_PROJECT_ID'),
    "private_key_id": os.getenv('FIREBASE_PRIVATE_KEY_ID'),
    "private_key": os.getenv('FIREBASE_PRIVATE_KEY').replace('\\n', '\n') if os.getenv('FIREBASE_PRIVATE_KEY') else None,
    "client_email": os.getenv('FIREBASE_CLIENT_EMAIL'),
    "client_id": os.getenv('FIREBASE_CLIENT_ID'),
    "auth_uri": os.getenv('FIREBASE_AUTH_URI'),
    "token_uri": os.getenv('FIREBASE_TOKEN_URI'),
    "auth_provider_x509_cert_url": os.getenv('FIREBASE_AUTH_PROVIDER_X509_CERT_URL'),
    "client_x509_cert_url": os.getenv('FIREBASE_CLIENT_X509_CERT_URL'),
    "universe_domain": os.getenv('FIREBASE_UNIVERSE_DOMAIN', 'googleapis.com')
}

# Verify all required fields are present
required_fields = ['project_id', 'private_key_id', 'private_key', 'client_email']
missing_fields = [field for field in required_fields if not cred_dict.get(field)]
if missing_fields:
    raise ValueError(f"Missing required Firebase credentials: {', '.join(missing_fields)}")

# Initialize Firebase with credentials dictionary
cred = credentials.Certificate(cred_dict)
firebase_admin.initialize_app(cred)

# Get Firestore client
db = firestore.client()

print("Firebase initialized successfully.") 