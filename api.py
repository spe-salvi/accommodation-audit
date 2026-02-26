import logging, config
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class CanvasClient:
    def __init__(self, base_url, token, session):
        self.base_url = base_url
        self.token = token
        self.session = session  # httpx.AsyncClient or aiohttp session

    async def get_paginated(self, path, params=None):
        results = []
        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.token}"}

        while url:
            response = await self.session.get(url, headers=headers, params=params)
            response.raise_for_status()

            results.extend(response.json())

            url = self._extract_next_link(response.headers)

        return results

    def _extract_next_link(headers):
        from requests.utils import parse_header_links

        link_header = headers.get("Link")
        if not link_header:
            return None

        links = parse_header_links(link_header.rstrip('>').replace('>,', '>,'))

        for link in links:
            if link.get("rel") == "next":
                return link.get("url")

        return None

    async def call(self, name, **kwargs):
        endpoint = ENDPOINTS[name]

        path = endpoint["path"]
        params = endpoint["params"].format(**kwargs)
        parser = endpoint["parser"]

        data = await self.get_paginated(path, params=params)

        return parser(data)


'''
Builders
'''

def build_term():
    return

def build_courses_list():
    return

def build_course():
    return

def build_new_quiz():
    return

def build_classic_quiz():
    return

def build_new_quizzes_list():
    return

def build_classic_quizzes_list():
    return

def build_users_list():
    return

def build_user():
    return

def build_enrollments_list():
    return

def build_classic_submissions_list():
    return

def build_new_submissions_list():
    return

def build_items_list():
    return


ENDPOINTS = {
    'term' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1{config.FUS_ACCOUNT}/terms/{term_id}",
        "params": {},
        "builder": build_term,
    },
    'courses' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1{config.FUS_ACCOUNT}/courses",
        "params": {"search_term": search_param, "enrollment_term_id": term_id} if term_id else {"search_term": search_param} if search_param else {},
        "builder": build_courses_list,
    },
    'course' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}",
        "params": {},
        "builder": build_course,
    },
    'course_users' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}/users",
        "params": {},
        "builder": build_users_list,
    },
    'users' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1{config.FUS_ACCOUNT}/users",
        "params": {"search_term": search_param} if search_param else {},
        "builder": build_users_list,
    },
    'c_quizzes' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}/quizzes",
        "params": {"search_term": search_param} if search_param else {},
        "builder": build_classic_quizzes_list,
    },
    'c_quiz' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}/quizzes/{quiz_id}",
        "params": {},
        "builder": build_classic_quiz,
    },
    'c_quiz_submissions': {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}/quizzes/{quiz_id}/submissions",
        "params": {},
        "builder": build_classic_submissions_list,
    },
    'n_quizzes' : {
        "method": "GET",
        "path": f"{config.API_URL}/quiz/v1/courses/{course_id}/quizzes",
        "params": {"search_term": search_param} if search_param else {},
        "builder": build_new_quizzes_list,
    },
    'n_quiz' : {
        "method": "GET",
        "path": f"{config.API_URL}/quiz/v1/courses/{course_id}/quizzes/{quiz_id}",
        "params": {},
        "builder": build_new_quiz,
    },
    'n_quiz_submissions': {
        "method": "GET",
        "path": f"{config.API_URL}/v1/courses/{course_id}/assignments/{quiz_id}/submissions",
        "params": {},
        "builder": build_new_submissions_list,
    },
    'n_quiz_items': {
        "method": "GET",
        "path": f"{config.API_URL}/quiz/v1/courses/{course_id}/quizzes/{quiz_id}/items",
        "params": {},
        "builder": build_items_list,
    },
    'enrollments' : {
        "method": "GET",
        "path": f"{config.API_URL}/v1/users/{user_id}/enrollments",
        "params": {"enrollment_term_id": term_id} if term_id else {},
        "builder": build_enrollments_list,
    },
}