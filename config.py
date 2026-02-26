import os
from dotenv import load_dotenv
load_dotenv()

BASE_URL = "https://franciscan.instructure.com"
BETA_URL = "https://franciscan.beta.instructure.com"
ACCESS_TOKEN = os.getenv('ACCESS_TOKEN')
ACCESS_TOKEN_EL = os.getenv('ACCESS_TOKEN_EL')

 
API_URL = f'{BASE_URL}/api/v1'
BETA_API_URL = f'{BETA_URL}/api/v1'
FUS_ACCOUNT = '/accounts/1'
HEADERS = {
    "Authorization": "Bearer " + ACCESS_TOKEN_EL
    }

MAX_WORKERS = 30

TERM = 117 # Spring 2026

CACHE_TTL = 60 * 60 * 24 # 1 day in seconds