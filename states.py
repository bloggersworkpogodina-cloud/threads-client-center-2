from aiogram.fsm.state import State, StatesGroup


class AddClient(StatesGroup):
    name = State(); threads = State(); telegram = State(); publish_mode = State(); services = State(); service_price = State(); billing_start = State(); confirm = State()

class LinkSheet(StatesGroup):
    url = State()

class LinkPlan(StatesGroup):
    url = State()

class ManagerMessage(StatesGroup):
    text = State()

class PartialPublication(StatesGroup):
    count = State()

class ResultsFlow(StatesGroup):
    responses = State(); leads = State(); comment = State()

class WeeklyStatsFlow(StatesGroup):
    views = State(); likes = State(); replies = State(); reposts = State(); quotes = State(); new_followers = State(); telegram_clicks = State(); best_post = State(); manager_comment = State()


class BaselineFlow(StatesGroup):
    total_views = State()
    threads_followers = State()
    telegram_followers = State()
    weekly_leads = State()
    overview_screen = State()
    content_screen = State()
    telegram_screen = State()

class WeeklyAnalyticsFlow(StatesGroup):
    total_views = State()
    threads_followers = State()
    telegram_followers = State()
    views = State()
    applications = State()
    overview_screen = State()
    content_screen = State()
    telegram_screen = State()


class ClientDocsFlow(StatesGroup):
    contract = State()
    policy = State()

class ConsentFlow(StatesGroup):
    customer_type = State()
    legal_name = State()
    signer_name = State()
    signer_authority = State()
    inn = State()
    tax_status = State()
    ogrn = State()
    kpp = State()
    address = State()
    email = State()
    phone = State()


class ClientTermsFlow(StatesGroup):
    services = State()
    service_price = State()
    billing_start = State()


class ActFlow(StatesGroup):
    content = State()


class ActRemarkFlow(StatesGroup):
    text = State()
