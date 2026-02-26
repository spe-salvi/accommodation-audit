import requests, time, config, logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

MAX_RETRIES = 3
INITIAL_RETRY_DELAY = 1  # seconds

def paginatedGet(url, headers, inputdata):
    perPageData = {"per_page": 100}
    mergedData = {**inputdata, **perPageData}
    response = requests.get(url, data=mergedData, headers=headers)
    data = response.json()
    if 'next' in response.links:
        data = data + paginatedGet(response.links['next']['url'], headers, inputdata)
 
    return data

def retry_get(url, params):
    if not url or not isinstance(url, str) or not isinstance(params, dict):
        logger.error("Invalid URL or parameters")
        return None

    retry_count = 0
    while retry_count < MAX_RETRIES:

        try:
            data = paginatedGet(url, config.HEADERS, params)
            break
        except:
            retry_count += 1
            if retry_count == MAX_RETRIES:
                logger.error(f"Failed to fetch data after {MAX_RETRIES} attempts")
                return None
            delay = INITIAL_RETRY_DELAY * (2 ** (retry_count - 1))
            time.sleep(delay)
            continue
    return data

